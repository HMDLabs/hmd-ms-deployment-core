"""Bundle membership in an environment and the bundle file's ``local`` block
(NERD0019 SPEC0002, SPEC0003)."""

import pytest
from hmd_base_service.exceptions import ServiceException
from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_lang_deployment.environment import Environment
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_schema_loader import DefaultLoader

from hmd_ms_deployment_core.bundle_information import BundleInformation
from hmd_ms_deployment_core.class_information import ClassInformation
from hmd_ms_deployment_core.environment_information import EnvironmentInformation

LOCAL = {
    "default_profiles": ["airflow", "trino"],
    "roles": {"airflow": {"profiles": ["airflow"]}},
    "reuse": "auto",
}


def _analytics(**extra):
    bundle = {
        "bundle_name": "analytics",
        "roles": {
            "airflow": {
                "repo_class_name": "hmd-app-airflow",
                "required": "true",
                "version_spec": "~= 0.4",
            },
            "trino": {
                "repo_class_name": "hmd-inf-trino",
                "required": "true",
                "version_spec": "~= 0.2",
            },
            "compute": {
                "repo_class_name": "hmd-inf-eks-node-group",
                "required": "true",
                "provision": "false",
                "version_spec": "~= 0.2",
            },
        },
    }
    bundle.update(extra)
    return bundle


@pytest.fixture()
def env():
    client = HmdLangDeploymentClient(MemoryClient(DefaultLoader("schemas/local"), {}))
    ci = ClassInformation(client)
    for name, version in (
        ("hmd-app-airflow", "0.4.352"),
        ("hmd-inf-trino", "0.2.10"),
        ("hmd-inf-eks-node-group", "0.2.3"),
    ):
        ci.add_repo_version_by_name(name, version, {}, {})
    environment = Environment(type="local", account_number="1", hmd_region="reg1")
    client.upsert(environment)
    env_info = EnvironmentInformation(environment, client)
    for instance, (name, version) in {
        "airflow": ("hmd-app-airflow", "0.4.352"),
        "trino": ("hmd-inf-trino", "0.2.10"),
        "eks-cluster": ("hmd-inf-eks-node-group", "0.2.3"),
    }.items():
        env_info.add_repo_instance(
            instance, "aaa", {}, ci.get_repo_class_version(name, version)
        )
    info = BundleInformation(client)
    info.add_bundle_version(_analytics(local=LOCAL), "hmd-stack-analytics", "0.1.4")
    return client, environment, info


BINDINGS = {"airflow": "airflow", "trino": "trino", "compute": "eks-cluster"}


# --- SPEC0002: the local block ----------------------------------------------


def test_local_block_is_stored_verbatim_and_returned(env):
    _, _, info = env
    bv = info.get_bundle_version("analytics", "0.1.4")
    assert bv.local == LOCAL
    rows = info.list_bundle_versions("analytics")
    assert rows[0]["local"] == LOCAL


def test_reregistering_with_a_changed_local_block_is_a_content_change(env):
    _, _, info = env
    with pytest.raises(ServiceException, match="different content"):
        info.add_bundle_version(
            _analytics(local={"reuse": "decide"}), "hmd-stack-analytics", "0.1.4"
        )


def test_reregistering_with_the_same_local_block_is_unchanged(env):
    _, _, info = env
    _, status = info.add_bundle_version(
        _analytics(local=LOCAL), "hmd-stack-analytics", "0.1.4"
    )
    assert status == "unchanged"


# --- SPEC0003: membership ---------------------------------------------------


def _member_roles(client, info, environment):
    bv = info.get_bundle_version("analytics", "0.1.4")
    out = {}
    for rel in client.get_to_repo_instance_member_of_bundle_version_hmd_lang_deployment(
        bv
    ):
        instance = info.rs.ref_from(rel)
        out[rel.role] = (instance.name, rel.provisioned)
    return out


def test_attach_records_the_environment_edge_and_one_member_edge_per_role(env):
    client, environment, info = env

    row = info.attach_bundle_to_environment(
        environment,
        "analytics",
        version="0.1.4",
        bindings=BINDINGS,
        declared=["airflow", "trino"],
        profiles=["airflow", "trino"],
        release_version="0.1.4",
        source_ref="ghcr.io/neuronsphere/stacks/analytics",
        digest="sha256:abc",
    )

    assert row["bundle_name"] == "analytics"
    assert row["version"] == "0.1.4"
    assert row["bindings"] == BINDINGS
    assert row["declared"] == ["airflow", "trino"]
    assert info.list_environment_bundles(environment) == [row]
    assert _member_roles(client, info, environment) == {
        "airflow": ("airflow", "true"),
        "trino": ("trino", "true"),
        "compute": ("eks-cluster", "false"),
    }


def test_attach_again_replaces_the_previous_record(env):
    client, environment, info = env
    info.attach_bundle_to_environment(
        environment, "analytics", bindings=BINDINGS, declared=["airflow", "trino"]
    )
    info.attach_bundle_to_environment(
        environment,
        "analytics",
        bindings={"airflow": "airflow", "compute": "eks-cluster"},
        declared=["airflow"],
    )

    rows = info.list_environment_bundles(environment)
    assert len(rows) == 1
    assert rows[0]["declared"] == ["airflow"]
    assert _member_roles(client, info, environment) == {
        "airflow": ("airflow", "true"),
        "compute": ("eks-cluster", "false"),
    }


def test_attach_refuses_an_unknown_role(env):
    _, environment, info = env
    with pytest.raises(ServiceException, match="no role, warehouse"):
        info.attach_bundle_to_environment(
            environment, "analytics", bindings={"warehouse": "trino"}
        )


def test_attach_refuses_an_instance_the_environment_does_not_have(env):
    _, environment, info = env
    with pytest.raises(ServiceException, match="superset"):
        info.attach_bundle_to_environment(
            environment, "analytics", bindings={"airflow": "superset"}
        )


def test_attach_refuses_a_declared_instance_it_does_not_bind(env):
    _, environment, info = env
    with pytest.raises(ServiceException, match="declares superset"):
        info.attach_bundle_to_environment(
            environment,
            "analytics",
            bindings={"airflow": "airflow"},
            declared=["superset"],
        )


def test_detach_removes_both_edges_and_returns_what_was_declared(env):
    client, environment, info = env
    info.attach_bundle_to_environment(
        environment, "analytics", bindings=BINDINGS, declared=["airflow", "trino"]
    )

    removed = info.detach_bundle_from_environment(environment, "analytics")

    assert removed["declared"] == ["airflow", "trino"]
    assert info.list_environment_bundles(environment) == []
    assert _member_roles(client, info, environment) == {}


def test_detach_of_a_bundle_not_attached_is_an_error(env):
    _, environment, info = env
    with pytest.raises(ServiceException, match="not attached"):
        info.detach_bundle_from_environment(environment, "analytics")


def test_bundles_of_instance_answers_which_bundle_put_it_there(env):
    _, environment, info = env
    info.attach_bundle_to_environment(
        environment, "analytics", bindings=BINDINGS, declared=["airflow", "trino"]
    )
    assert info.bundles_of_instance(environment, "eks-cluster") == [
        {
            "bundle_name": "analytics",
            "version": "0.1.4",
            "role": "compute",
            "provisioned": "false",
        }
    ]
