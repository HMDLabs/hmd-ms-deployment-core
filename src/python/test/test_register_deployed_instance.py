"""``register_deployed_instance`` records a plan as well as a fact (NERD0015 SPEC0003)."""

import pytest

from hmd_cli_tools import ServiceException
from hmd_ms_deployment_core import DEPLOYED, DEPLOY_NEXT
from hmd_ms_deployment_core import operations
from hmd_ms_deployment_core.environment_information import (
    EnvironmentInformation,
    get_current_repo_instance_deployment,
)


class _FakeService:
    """Captures the operations ``setup`` registers, keyed by function name."""

    def __init__(self):
        self.ops = {}
        self.context = {}

    def operation(self, **kwargs):
        def deco(fn):
            self.ops[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture()
def ops(base_environment, monkeypatch):
    client, rs = base_environment
    monkeypatch.setattr(operations, "_get_deploy_client", lambda evt, ctx: client)
    monkeypatch.setattr(operations, "seed_base_catalog_best_effort", lambda c: None)
    svc = _FakeService()
    operations.setup(svc)
    return client, svc.ops


def _call(op, payload):
    return op({"args": {"payload": payload}}, {})


def _env(client, env_type="dev"):
    env = client.search_environment_hmd_lang_deployment(
        {"attribute": "type", "operator": "=", "value": env_type}
    )[0]
    info = EnvironmentInformation(env, client)
    info.load_environment()
    return info


def test_default_status_is_deployed_and_current(ops):
    client, ops = ops
    result = _call(
        ops["register_deployed_instance"],
        {
            "environment": "dev",
            "repo_class_name": "rc2",
            "version": "0.1.1",
            "instance_name": "ri2b",
            "deployment_id": "aaa",
        },
    )
    info = _env(client)
    ri = info.get_repo_instance("ri2b")
    assert ri.identifier == result["repo_instance_id"]
    rel = get_current_repo_instance_deployment(ri, info.rel_support)
    assert rel is not None and rel.current == "true"
    assert info.rel_support.ref_to(rel).status == DEPLOYED


def test_deploy_next_with_dependencies_records_a_plan(ops):
    client, ops = ops
    result = _call(
        ops["register_deployed_instance"],
        {
            "environment": "dev",
            "repo_class_name": "rc1",
            "version": "0.1.1",
            "instance_name": "ri1b",
            "deployment_id": "aaa",
            "instance_configuration": {"k": "v"},
            "dependencies": {"rc1-rc2": "ri2", "rc1-rc3": ["ri3"]},
            "status": DEPLOY_NEXT,
        },
    )
    info = _env(client)
    ri = info.get_repo_instance("ri1b")
    # Not current yet: the runner flips it with set_deployment_status.
    assert get_current_repo_instance_deployment(ri, info.rel_support) is None
    rels = ri.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment()
    assert len(rels) == 1 and rels[0].current == "false"
    rid = info.rel_support.ref_to(rels[0])
    assert rid.identifier == result["repo_instance_deployment_id"]
    assert rid.status == DEPLOY_NEXT
    deps = {
        rel.role: info.rel_support.ref_to(rel).name
        for rel in ri.get_from_repo_instance_req_repo_instance_hmd_lang_deployment()
    }
    assert deps == {"rc1-rc2": "ri2", "rc1-rc3": "ri3"}

    # get_deployment_config resolves the DEPLOY_NEXT deployment and its dependencies.
    config = ops["get_deployment_config"]({"args": {"type": "dev", "name": "ri1b"}}, {})
    assert config["instance_name"] == "ri1b"
    assert config["k"] == "v"
    assert config["dependencies"]["rc1-rc2"]["instance_name"] == "ri2"

    # Flipping the status makes it current, as the runner does after the deploy.
    ops["set_deployment_status"](
        {"args": {"id": rid.identifier, "status": DEPLOYED}}, {}
    )
    info = _env(client)
    ri = info.get_repo_instance("ri1b")
    assert get_current_repo_instance_deployment(ri, info.rel_support) is not None


def test_unknown_dependency_is_refused(ops):
    client, ops = ops
    with pytest.raises(AssertionError):
        _call(
            ops["register_deployed_instance"],
            {
                "environment": "dev",
                "repo_class_name": "rc1",
                "version": "0.1.1",
                "instance_name": "ri1c",
                "dependencies": {"rc1-rc2": "nope"},
                "status": DEPLOY_NEXT,
            },
        )


def test_other_statuses_are_refused(ops):
    client, ops = ops
    with pytest.raises(ServiceException):
        _call(
            ops["register_deployed_instance"],
            {
                "environment": "dev",
                "repo_class_name": "rc2",
                "version": "0.1.1",
                "instance_name": "ri2c",
                "status": "DESTROYED",
            },
        )


def test_find_repo_class_instances_lists_pending_deployments(ops):
    client, ops = ops
    _call(
        ops["register_deployed_instance"],
        {
            "environment": "dev",
            "repo_class_name": "rc2",
            "version": "0.1.1",
            "instance_name": "ri2d",
            "deployment_id": "bbb",
            "status": DEPLOY_NEXT,
        },
    )
    rows = ops["find_repo_class_instances"](
        {"args": {"repo_class_name": "rc2", "type": "dev"}}, {}
    )
    by_name = {r["instance_name"]: r for r in rows}
    assert set(by_name) == {"ri2", "ri2d"}
    assert by_name["ri2"]["status"] == DEPLOYED
    assert by_name["ri2d"]["status"] == DEPLOY_NEXT
    assert by_name["ri2d"]["deployment_id"] == "bbb"
    assert by_name["ri2d"]["repo_instance_name"] == "ri2d"
    assert by_name["ri2d"]["deployment_identifier"]
    # The alias route serves the same rows.
    assert (
        ops["find_repo_class_instances_by_path"](
            {"args": {"repo_class_name": "rc2", "type": "dev"}}, {}
        )
        == rows
    )


def test_get_deployment_config_carries_dependency_details(ops):
    client, ops = ops
    config = ops["get_deployment_config"]({"args": {"type": "dev", "name": "ri1"}}, {})
    # ri1 depends on ri2 and ri3; details carries each one's own resolved config,
    # minus its dependencies, as helm charts read it (.Values.details.<instance>).
    assert set(config["details"]) == {"ri2", "ri3"}
    assert config["details"]["ri2"]["instance_name"] == "ri2"
    assert config["details"]["ri2"]["config_rc2"] == "value_rc2"
    assert "dependencies" not in config["details"]["ri2"]
    assert "ri1" not in config["details"]
