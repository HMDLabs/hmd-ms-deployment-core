"""NERD0011: a dry run records a RepoInstanceDeployment but never changes the
instance's current deployment, its dependency edges, or a real deployment
that is still pending."""

from collections import Counter

from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_graphql_client.relationship_support import RelationshipSupport
from hmd_lang_deployment.environment import Environment
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_ms_deployment_core import DEPLOYED, DEPLOY_NEXT, FAILED, PLANNED
from hmd_ms_deployment_core.class_information import ClassInformation
from hmd_ms_deployment_core.environment_information import (
    EnvironmentInformation,
    is_dry_run_deployment,
)
from hmd_schema_loader import DefaultLoader


def make_env(data):
    loader = DefaultLoader("schemas/local")
    mem_client = MemoryClient(loader, data)
    client = HmdLangDeploymentClient(mem_client)
    rs = RelationshipSupport()
    rs.register_client(mem_client)
    client.upsert(
        Environment(type="dev", account_number="123456789", hmd_region="reg1")
    )
    env_info = EnvironmentInformation(
        client.search_environment_hmd_lang_deployment({})[0], client
    )
    return client, rs, ClassInformation(client), env_info


def rid_states(client, rs, ri):
    rels = (
        client.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment(
            ri
        )
    )
    return Counter((rel.current, rs.ref_to(rel).status) for rel in rels)


def deployed_datadog_lambda(class_info, env_info):
    dd_ri, rid = env_info.add_repo_instance(
        "base-datadog",
        "aaa",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog", "0.1.10"),
    )
    env_info.update_instance_deployment_status(rid, DEPLOYED)
    ddl_ri, rid = env_info.add_repo_instance(
        "datadog-lambda",
        "aaa",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog-lambdas", "0.1.5"),
        dependencies={"datadog": [dd_ri]},
    )
    env_info.update_instance_deployment_status(rid, DEPLOYED)
    return dd_ri, ddl_ri


def test_planned_dry_run_leaves_the_current_deployment(deployment_class_only):
    client, rs, class_info, env_info = make_env(deployment_class_only)
    dd_ri, ddl_ri = deployed_datadog_lambda(class_info, env_info)

    _, dry_rid = env_info.add_repo_instance(
        "datadog-lambda",
        "bbb",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog-lambdas", "0.1.5"),
        dependencies={"datadog": [dd_ri]},
        dry_run=True,
    )
    assert is_dry_run_deployment(dry_rid)

    env_info.update_instance_deployment_status(dry_rid, PLANNED)

    states = rid_states(client, rs, ddl_ri)
    assert states[("true", DEPLOYED)] == 1
    assert states[("false", PLANNED)] == 1
    planned = client.get_repo_instance_deployment_hmd_lang_deployment(
        dry_rid.identifier
    )
    assert planned.start is not None and planned.end is not None


def test_failed_dry_run_does_not_become_current(deployment_class_only):
    client, rs, class_info, env_info = make_env(deployment_class_only)
    dd_ri, ddl_ri = deployed_datadog_lambda(class_info, env_info)

    _, dry_rid = env_info.add_repo_instance(
        "datadog-lambda",
        "bbb",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog-lambdas", "0.1.5"),
        dependencies={"datadog": [dd_ri]},
        dry_run=True,
    )
    env_info.update_instance_deployment_status(dry_rid, FAILED)

    states = rid_states(client, rs, ddl_ri)
    assert states[("true", DEPLOYED)] == 1
    assert states[("false", FAILED)] == 1


def test_dry_run_does_not_skip_a_pending_real_deployment(deployment_class_only):
    client, rs, class_info, env_info = make_env(deployment_class_only)
    dd_ri, ddl_ri = deployed_datadog_lambda(class_info, env_info)
    rcv = class_info.get_repo_class_version("hmd-inf-datadog-lambdas", "0.1.5")

    env_info.add_repo_instance(
        "datadog-lambda", "ccc", {}, rcv, dependencies={"datadog": [dd_ri]}
    )
    env_info.add_repo_instance(
        "datadog-lambda",
        "ddd",
        {},
        rcv,
        dependencies={"datadog": [dd_ri]},
        dry_run=True,
    )

    # Both the real and the dry-run deployment are still pending.
    assert rid_states(client, rs, ddl_ri)[("false", DEPLOY_NEXT)] == 2


def test_dry_run_keeps_existing_dependency_edges(deployment_class_only):
    client, rs, class_info, env_info = make_env(deployment_class_only)
    dd_ri, ddl_ri = deployed_datadog_lambda(class_info, env_info)
    other_dd_ri, rid = env_info.add_repo_instance(
        "other-datadog",
        "aaa",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog", "0.1.10"),
    )
    env_info.update_instance_deployment_status(rid, DEPLOYED)

    # A dry run that re-points the datadog role must not move the real edge.
    env_info.add_repo_instance(
        "datadog-lambda",
        "bbb",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog-lambdas", "0.1.5"),
        dependencies={"datadog": [other_dd_ri]},
        dry_run=True,
    )
    edges = client.get_from_repo_instance_req_repo_instance_hmd_lang_deployment(ddl_ri)
    assert [rs.ref_to(e).name for e in edges] == ["base-datadog"]


def test_dry_run_of_a_new_instance_gets_its_edges(deployment_class_only):
    client, rs, class_info, env_info = make_env(deployment_class_only)
    dd_ri, rid = env_info.add_repo_instance(
        "base-datadog",
        "aaa",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog", "0.1.10"),
    )
    env_info.update_instance_deployment_status(rid, DEPLOYED)

    new_ri, dry_rid = env_info.add_repo_instance(
        "datadog-lambda",
        "aaa",
        {},
        class_info.get_repo_class_version("hmd-inf-datadog-lambdas", "0.1.5"),
        dependencies={"datadog": [dd_ri]},
        dry_run=True,
    )
    edges = client.get_from_repo_instance_req_repo_instance_hmd_lang_deployment(new_ri)
    assert len(edges) == 1

    env_info.update_instance_deployment_status(dry_rid, PLANNED)
    # Never current: the instance has no deployment the BOM would report.
    assert rid_states(client, rs, new_ri) == Counter({("false", PLANNED): 1})
