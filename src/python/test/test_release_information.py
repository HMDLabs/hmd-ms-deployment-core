import pytest
from hmd_base_service.exceptions import ServiceException
from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_schema_loader import DefaultLoader

from hmd_ms_deployment_core.class_information import ClassInformation
from hmd_ms_deployment_core.release_information import ReleaseInformation


def _new_client():
    loader = DefaultLoader("schemas/local")
    return HmdLangDeploymentClient(MemoryClient(loader, {}))


def _registered(*pins):
    client = _new_client()
    ci = ClassInformation(client)
    for name, version in pins:
        ci.add_repo_version_by_name(name, version, {}, {})
    return client


def _pins(*pairs):
    return [{"repo_class_name": n, "version": v} for n, v in pairs]


OTEL = ("hmd-inf-otel-collector", "0.1.40")
CH = ("hmd-inf-clickhouse", "0.2.7")
HDX = ("hmd-inf-hyperdx", "0.1.4")


# --- lifecycle -------------------------------------------------------------


def test_create_release_version_records_pins_and_candidate_status():
    client = _registered(OTEL, CH)
    info = ReleaseInformation(client)

    rv = info.create_release_version("telemetry", _pins(OTEL, CH), version="0.1.0")

    assert rv.status == "candidate"
    assert info.pins_of(rv) == {
        "hmd-inf-otel-collector": "0.1.40",
        "hmd-inf-clickhouse": "0.2.7",
    }
    edges = client.get_from_release_version_pins_repo_class_version_hmd_lang_deployment(
        rv
    )
    assert len(edges) == 2


def test_generated_versions_are_monotonic():
    info = ReleaseInformation(_registered(OTEL))
    first = info.create_release_version("telemetry", _pins(OTEL))
    second = info.create_release_version("telemetry", _pins(OTEL))
    assert first.version != second.version
    assert info.get_release_version("telemetry").version == second.version


def test_existing_version_label_is_rejected():
    info = ReleaseInformation(_registered(OTEL))
    info.create_release_version("telemetry", _pins(OTEL), version="0.1.0")
    with pytest.raises(ServiceException, match="already has version"):
        info.create_release_version("telemetry", _pins(OTEL), version="0.1.0")


def test_unregistered_pin_is_rejected():
    info = ReleaseInformation(_registered(OTEL))
    with pytest.raises(ServiceException, match="not found"):
        info.create_release_version("telemetry", _pins(OTEL, CH))


@pytest.mark.parametrize(
    "path",
    [
        ["verifying", "verified", "released"],
        ["verifying", "failed"],
        ["superseded"],
        ["verifying", "verified", "superseded"],
    ],
)
def test_allowed_transitions(path):
    info = ReleaseInformation(_registered(OTEL))
    rv = info.create_release_version("telemetry", _pins(OTEL))
    for status in path:
        rv = info.transition(rv, status)
    assert rv.status == path[-1]


@pytest.mark.parametrize(
    "path",
    [
        ["verified"],
        ["released"],
        ["verifying", "released"],
        ["verifying", "failed", "verified"],
        ["verifying", "verified", "released", "superseded"],
        ["bogus"],
    ],
)
def test_rejected_transitions(path):
    info = ReleaseInformation(_registered(OTEL))
    rv = info.create_release_version("telemetry", _pins(OTEL))
    with pytest.raises(ServiceException):
        for status in path:
            rv = info.transition(rv, status)


def test_repeated_status_is_a_noop_and_evidence_merges():
    info = ReleaseInformation(_registered(OTEL))
    rv = info.create_release_version("telemetry", _pins(OTEL))
    rv = info.transition(rv, "verifying", evidence={"change_set_deployment": "csd-1"})
    rv = info.transition(rv, "verifying", evidence={"ignored": True})
    rv = info.transition(rv, "verified", evidence={"bender_results": "s3://x"})
    assert rv.evidence == {"change_set_deployment": "csd-1", "bender_results": "s3://x"}


# --- install ---------------------------------------------------------------


class FakePresence:
    def __init__(self, statuses):
        self.statuses = statuses

    def status(self, content_path, digest):
        return self.statuses.get(content_path, "awaiting_replication")


def _lock(*pairs):
    return {
        "repo_class_name": "hmd-bundle-core",
        "resolved": [
            {
                "repo_class_name": n,
                "version": v,
                "content_path": f"repository:/{n}/{v}/{n}_{v}_build.zip",
                "digest": f"sha256:{n}",
            }
            for n, v in pairs
        ],
    }


def _release_json(version="0.1.7", reference_bom=None):
    return {
        "release_name": "telemetry",
        "version": version,
        "reference_bom": reference_bom or [],
        "config_policy": {"required": ["*.account"]},
    }


def _path(pair):
    n, v = pair
    return f"repository:/{n}/{v}/{n}_{v}_build.zip"


def test_install_with_nothing_replicated_reports_awaiting():
    client = _new_client()
    result = ReleaseInformation(client, FakePresence({})).install_release(
        _lock(OTEL, CH), _release_json()
    )
    assert result["installed"] is False
    assert {e["status"] for e in result["entries"]} == {"awaiting_replication"}


def test_install_partial_then_full_and_idempotent():
    client = _registered(OTEL)
    presence = FakePresence({_path(OTEL): "present"})
    info = ReleaseInformation(client, presence)

    partial = info.install_release(_lock(OTEL, CH), _release_json())
    assert partial["installed"] is False
    assert {e["repo_class_name"]: e["status"] for e in partial["entries"]} == {
        "hmd-inf-otel-collector": "present",
        "hmd-inf-clickhouse": "awaiting_replication",
    }

    # Clickhouse arrives, but the ArtifactMonitor has not registered it yet.
    presence.statuses[_path(CH)] = "present"
    pending = info.install_release(_lock(OTEL, CH), _release_json())
    statuses = {e["repo_class_name"]: e["status"] for e in pending["entries"]}
    assert statuses["hmd-inf-clickhouse"] == "awaiting_registration"

    ClassInformation(client).add_repo_version_by_name(*CH, {}, {})
    full = info.install_release(_lock(OTEL, CH), _release_json())
    assert full["installed"] is True
    again = info.install_release(_lock(OTEL, CH), _release_json())
    assert again["installed"] is True

    rv = info.get_release_version("telemetry", "0.1.7")
    assert rv.status == "released"
    assert info.pins_of(rv) == {
        "hmd-inf-otel-collector": "0.1.40",
        "hmd-inf-clickhouse": "0.2.7",
    }
    edges = client.get_from_release_version_pins_repo_class_version_hmd_lang_deployment(
        rv
    )
    assert len(edges) == 2
    assert info.is_installed(rv)


def test_install_reports_digest_mismatch():
    client = _registered(OTEL)
    result = ReleaseInformation(
        client, FakePresence({_path(OTEL): "digest_mismatch"})
    ).install_release(_lock(OTEL), _release_json())
    assert result["installed"] is False
    assert result["entries"][0]["status"] == "digest_mismatch"


def test_install_refuses_lock_and_release_json_disagreeing():
    bom = [
        {
            "repo_instance_name": "otel",
            "repo_class_name": OTEL[0],
            "repo_class_version": "0.1.39",
        }
    ]
    with pytest.raises(ServiceException, match="disagree"):
        ReleaseInformation(_new_client(), FakePresence({})).install_release(
            _lock(OTEL), _release_json(reference_bom=bom)
        )


def test_install_without_artifact_check_relies_on_registration():
    client = _registered(OTEL)
    result = ReleaseInformation(client, None).install_release(
        _lock(OTEL), _release_json()
    )
    assert result["artifact_check"] == "unavailable"
    assert result["installed"] is True


# --- coverage --------------------------------------------------------------


def test_check_release_coverage():
    client = _registered(OTEL, CH, HDX, ("hmd-inf-hyperdx", "0.1.5"))
    info = ReleaseInformation(client)
    good = info.create_release_version("telemetry", _pins(OTEL, CH, HDX))
    info.transition(info.transition(good, "verifying"), "verified")
    bad = info.create_release_version(
        "telemetry", _pins(OTEL, CH, ("hmd-inf-hyperdx", "0.1.5"))
    )
    info.transition(info.transition(bad, "verifying"), "failed")

    equal = info.check_release_coverage(dict([OTEL, CH, HDX]))
    assert equal["match"] == "equal"
    assert equal["release_versions"] == [
        {"release_name": "telemetry", "version": good.version, "status": "verified"}
    ]

    covered = info.check_release_coverage(dict([OTEL, CH]))
    assert covered["match"] == "covered"
    assert covered["pins"]["hmd-inf-clickhouse@0.2.7"] == [good.version]

    neither = info.check_release_coverage(dict([OTEL, ("hmd-inf-clickhouse", "0.2.8")]))
    assert neither["match"] == "neither"

    failed = info.check_release_coverage(dict([OTEL, CH, ("hmd-inf-hyperdx", "0.1.5")]))
    assert failed["match"] == "neither"
    assert failed["failed"] == [
        {"release_name": "telemetry", "version": bad.version, "status": "failed"}
    ]


def test_coverage_accepts_a_changeset_definition():
    client = _registered(OTEL)
    info = ReleaseInformation(client)
    rv = info.create_release_version("telemetry", _pins(OTEL))
    info.transition(info.transition(rv, "verifying"), "verified")
    definition = [
        {
            "repo_instance_name": "otel",
            "repo_class_name": OTEL[0],
            "repo_class_version": OTEL[1],
        }
    ]
    assert info.check_release_coverage(definition)["match"] == "equal"


def test_a_class_pinned_at_several_versions():
    client = _registered(("hmd-inf-s3bucket", "0.1.11"), ("hmd-inf-s3bucket", "0.1.14"))
    info = ReleaseInformation(client)
    rv = info.create_release_version(
        "telemetry",
        _pins(("hmd-inf-s3bucket", "0.1.14"), ("hmd-inf-s3bucket", "0.1.11")),
    )
    assert info.pins_of(rv) == {"hmd-inf-s3bucket": ["0.1.11", "0.1.14"]}
    edges = client.get_from_release_version_pins_repo_class_version_hmd_lang_deployment(
        rv
    )
    assert len(edges) == 2
    info.transition(info.transition(rv, "verifying"), "verified")

    both = info.check_release_coverage({"hmd-inf-s3bucket": ["0.1.11", "0.1.14"]})
    assert both["match"] == "equal"
    one = info.check_release_coverage({"hmd-inf-s3bucket": "0.1.11"})
    assert one["match"] == "covered"
    assert one["pins"] == {"hmd-inf-s3bucket@0.1.11": [rv.version]}


def test_install_accepts_a_class_at_several_versions():
    client = _registered(("hmd-inf-s3bucket", "0.1.11"), ("hmd-inf-s3bucket", "0.1.14"))
    bom = [
        {
            "repo_instance_name": "a",
            "repo_class_name": "hmd-inf-s3bucket",
            "repo_class_version": "0.1.11",
        },
        {
            "repo_instance_name": "b",
            "repo_class_name": "hmd-inf-s3bucket",
            "repo_class_version": "0.1.14",
        },
    ]
    result = ReleaseInformation(client, None).install_release(
        _lock(("hmd-inf-s3bucket", "0.1.11"), ("hmd-inf-s3bucket", "0.1.14")),
        _release_json(reference_bom=bom),
    )
    assert result["installed"] is True
