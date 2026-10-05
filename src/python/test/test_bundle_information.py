import pytest
from hmd_base_service.exceptions import ServiceException
from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_schema_loader import DefaultLoader

from hmd_ms_deployment_core.bundle_information import BundleInformation


def _new_client():
    loader = DefaultLoader("schemas/local")
    return HmdLangDeploymentClient(MemoryClient(loader, {}))


INGRESS = {
    "resource_namespace": "kubernetes.neuronsphere.io",
    "resource_definition_name": "ingress-controller",
    "version": "0.1.0",
    "version_spec": "~= 0.1",
}


def _telemetry(version=None, **extra):
    bundle = {
        "bundle_name": "telemetry",
        "config_schema": {
            "type": "object",
            "properties": {"retention_days": {"type": "integer"}},
        },
        "default_configuration": {"retention_days": 7},
        "roles": {
            "otel-collector": {
                "repo_class_name": "hmd-inf-otel-collector",
                "required": "true",
                "version_spec": "~= 0.1",
            },
            "eks-alb": {
                "repo_class_name": "hmd-inf-eks-alb",
                "required": "true",
                "resource": INGRESS,
            },
        },
        "discovery": {"summary": "OTel collector, ClickHouse and HyperDX."},
    }
    if version:
        bundle["version"] = version
    bundle.update(extra)
    return bundle


def _role_edges(client, bv):
    class_reqs = sorted(
        r.role
        for r in client.get_from_bundle_version_req_repo_class_hmd_lang_deployment(bv)
    )
    resource_reqs = sorted(
        r.role
        for r in client.get_from_bundle_version_req_resource_definition_hmd_lang_deployment(
            bv
        )
    )
    return class_reqs, resource_reqs


def test_registers_bundle_version_and_wires_role_edges():
    client = _new_client()
    info = BundleInformation(client)

    bv, status = info.add_bundle_version(_telemetry("0.1.0"))

    assert status == "created"
    assert bv.version == "0.1.0"
    assert bv.default_configuration == {"retention_days": 7}
    assert set(bv.members) == {"otel-collector", "eks-alb"}
    # hmd-inf-eks-alb is not registered, so on a resource role its name
    # degrades to a suggestion with no class edge (NERD0004 SPEC0008).
    assert _role_edges(client, bv) == (["otel-collector"], ["eks-alb"])
    assert info.get_bundle_version("telemetry", "0.1.0").identifier == bv.identifier


def test_resource_definition_is_stub_upserted_when_unregistered():
    client = _new_client()
    BundleInformation(client).add_bundle_version(_telemetry("0.1.0"))

    rds = client.search_resource_definition_hmd_lang_deployment(
        {
            "attribute": "resource_definition_name",
            "operator": "=",
            "value": "ingress-controller",
        }
    )
    assert len(rds) == 1


def test_identical_repost_is_unchanged_and_changed_content_is_rejected():
    client = _new_client()
    info = BundleInformation(client)
    first, _ = info.add_bundle_version(_telemetry("0.1.0"))

    again, status = info.add_bundle_version(_telemetry("0.1.0"))
    assert status == "unchanged"
    assert again.identifier == first.identifier
    assert _role_edges(client, again) == (["otel-collector"], ["eks-alb"])

    with pytest.raises(ServiceException, match="different content"):
        info.add_bundle_version(
            _telemetry("0.1.0", default_configuration={"retention_days": 30})
        )


def test_version_defaults_to_source_repo_class_version():
    info = BundleInformation(_new_client())
    bv, _ = info.add_bundle_version(
        _telemetry(), source_repo_class_name="hmd-bundle-core", source_version="0.1.12"
    )
    assert bv.version == "0.1.12"
    assert bv.source_repo_class_name == "hmd-bundle-core"
    assert bv.source_repo_class_version == "0.1.12"


def test_missing_version_and_source_is_rejected():
    with pytest.raises(ServiceException, match="version"):
        BundleInformation(_new_client()).add_bundle_version(_telemetry())


def test_role_without_class_or_resource_is_rejected():
    bundle = _telemetry("0.1.0")
    bundle["roles"]["broken"] = {"required": "true"}
    with pytest.raises(ServiceException, match="broken"):
        BundleInformation(_new_client()).add_bundle_version(bundle)


def test_get_bundle_version_by_spec_and_latest():
    info = BundleInformation(_new_client())
    for v in ["0.1.0", "0.1.4", "0.2.0"]:
        info.add_bundle_version(_telemetry(v))

    assert info.get_bundle_version("telemetry").version == "0.2.0"
    assert (
        info.get_bundle_version("telemetry", version_spec="~= 0.1.0").version == "0.1.4"
    )
    assert (
        info.get_bundle_version("telemetry", version_spec=">= 0.1.1,< 0.2.0").version
        == "0.1.4"
    )
    with pytest.raises(ServiceException):
        info.get_bundle_version("telemetry", version_spec="~= 0.3")
    with pytest.raises(ServiceException):
        info.get_bundle_version("nope")


def test_add_bundle_versions_reports_per_bundle():
    info = BundleInformation(_new_client())
    bad = {"bundle_name": "bad", "roles": {"x": {}}}
    results = info.add_bundle_versions("hmd-bundle-core", "0.1.3", [_telemetry(), bad])
    assert [(r["bundle_name"], r["status"]) for r in results] == [
        ("telemetry", "created"),
        ("bad", "error"),
    ]
    assert results[0]["version"] == "0.1.3"
