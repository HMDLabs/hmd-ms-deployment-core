"""BACON toolset deploy requirements (hmd-docs-bacon spec/toolset).

The tool set distribution that performs a deployment can require things of the
deployment's dependencies. The scenario is the one that prompted the spec: a
projectbuilder whose cdktf tool gives every API Gateway a route-scoped
authorizer cache, deploying a service against an authorizer that predates
route-enumerated grants.
"""

import os
from unittest.mock import patch

import pytest

from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_graphql_client.relationship_support import RelationshipSupport
from hmd_lang_deployment.environment import Environment
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_ms_deployment_core.class_information import ClassInformation
from hmd_ms_deployment_core.deploy_requirements import (
    DeployRequirementEvaluator,
    parse_image_reference,
    resolve_deploy_image,
)
from hmd_ms_deployment_core.environment_information import EnvironmentInformation
from hmd_ms_deployment_core.resource_information import ResourceInformation
from hmd_schema_loader import DefaultLoader

AUTHZ = {
    "resource_namespace": "auth.neuronsphere.io",
    "resource_definition_name": "api-gateway-authorizer",
}

REQUIREMENT = {
    "name": "route-scoped-authorizer-cache",
    "applies_to": {"tool": "cdktf", "consumes_resource": dict(AUTHZ)},
    "requires": dict(AUTHZ, version_spec=">=2.0.0"),
    "severity": "error",
    "reason": "API Gateways cache authorizer results per route.",
}

NEW_IMAGE = "ghcr.io/hmdlabs/hmd-img-projectbuilder:0.4.400"
OLD_IMAGE = "ghcr.io/hmdlabs/hmd-img-projectbuilder:0.4.326"

AUTHZ_DEP = {
    "authorizer": {
        "repo_class_name": "hmd-inf-opa-authorizer",
        "required": "false",
        "version_spec": "~= 0.1",
    }
}


def make_client():
    loader = DefaultLoader("schemas/local")
    mem_client = MemoryClient(loader, {})
    client = HmdLangDeploymentClient(mem_client)
    rs = RelationshipSupport()
    rs.register_client(client)
    return client


def _world(client, requirement=REQUIREMENT, authz_version="0.1.67"):
    """Registers the classes and a dev environment holding one authorizer
    instance, ``authz``, deployed at ``authz_version``."""
    ri = ResourceInformation(client)
    rd = ri.upsert_resource_definition(
        AUTHZ["resource_namespace"], AUTHZ["resource_definition_name"], "2.0.0"
    )
    ci = ClassInformation(client)

    ci.add_repo_version_by_name("hmd-inf-opa-authorizer", "0.1.67", {}, {})
    ci.add_repo_version_by_name("hmd-inf-opa-authorizer", "0.1.68", {}, {})
    ri.declare_produces(
        ci.get_repo_class_version("hmd-inf-opa-authorizer", "0.1.68"),
        rd,
        role="authorizer",
    )

    ci.add_repo_version_by_name(
        "hmd-img-projectbuilder",
        "0.4.400",
        {},
        {},
        toolset={"name": "hmd", "deploy_requirements": [requirement]},
    )
    ci.add_repo_version_by_name("hmd-img-projectbuilder", "0.4.326", {}, {})

    ci.add_repo_version_by_name(
        "hmd-ms-thing",
        "0.1.5",
        dict(AUTHZ_DEP),
        {},
        deploy_commands=[["docker"], ["cdktf"]],
    )
    ci.add_repo_version_by_name(
        "hmd-helm-thing", "0.1.5", dict(AUTHZ_DEP), {}, deploy_commands=[["helm"]]
    )
    ci.add_repo_version_by_name(
        "hmd-ms-plain", "0.1.5", {}, {}, deploy_commands=[["cdktf"]]
    )

    env = Environment(type="dev", account_number="1", hmd_region="reg1")
    client.upsert(env)
    env_info = EnvironmentInformation(env, client)
    env_info.add_repo_instance(
        "authz",
        "aaa",
        {},
        ci.get_repo_class_version("hmd-inf-opa-authorizer", authz_version),
        status="DEPLOYED",
    )
    env_info.load_environment()
    return env_info


def _thing(instance="thing", repo_class="hmd-ms-thing", dependencies=None):
    change = {
        "repo_instance_name": instance,
        "repo_class_name": repo_class,
        "repo_class_version": "0.1.5",
    }
    if dependencies is not None:
        change["dependencies"] = dependencies
    return change


def _evaluate(client, changes, env_info=None, image=NEW_IMAGE, **kw):
    with patch.dict(os.environ, {"HMD_APP_IMAGE": image, "HMD_APP_IMAGE_MAP": "{}"}):
        return DeployRequirementEvaluator(client).evaluate(changes, env_info, **kw)


# --------------------------------------------------------------------------- #
# The incident
# --------------------------------------------------------------------------- #


def test_old_authorizer_is_an_error_naming_everything_needed_to_act():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(client, [_thing(dependencies={"authorizer": "authz"})], env_info)

    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error["type"] == "deploy_requirement"
    assert error["requirement"] == "route-scoped-authorizer-cache"
    assert error["instance"] == "thing"
    for fragment in (
        "route-scoped-authorizer-cache",
        "hmd-img-projectbuilder 0.4.400",
        "authz",
        "hmd-inf-opa-authorizer 0.1.67",
        "provides no auth.neuronsphere.io/api-gateway-authorizer",
        ">=2.0.0",
        "API Gateways cache authorizer results per route.",
    ):
        assert fragment in error["message"], fragment


def test_upgrading_the_authorizer_in_the_same_change_set_satisfies_it():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(
        client,
        [
            {
                "repo_instance_name": "authz",
                "repo_class_name": "hmd-inf-opa-authorizer",
                "repo_class_version": "0.1.68",
            },
            _thing(dependencies={"authorizer": "authz"}),
        ],
        env_info,
    )

    assert result == {"errors": [], "warnings": []}


def test_an_already_upgraded_authorizer_satisfies_it():
    client = make_client()
    env_info = _world(client, authz_version="0.1.68")

    result = _evaluate(client, [_thing(dependencies={"authorizer": "authz"})], env_info)

    assert result == {"errors": [], "warnings": []}


def test_dependencies_default_to_the_existing_instance_edges():
    client = make_client()
    env_info = _world(client)
    env_info.add_repo_instance(
        "thing",
        "aaa",
        {},
        ClassInformation(client).get_repo_class_version("hmd-ms-thing", "0.1.5"),
        dependencies={"authorizer": [env_info.get_repo_instance("authz")]},
        status="DEPLOYED",
    )
    env_info.load_environment()

    # A redeploy that names no dependencies keeps the ones it has.
    result = _evaluate(client, [_thing()], env_info)

    assert [e["requirement"] for e in result["errors"]] == [
        "route-scoped-authorizer-cache"
    ]


def test_dry_run_without_an_environment_resolves_instances_by_name():
    client = make_client()
    _world(client)

    result = _evaluate(client, [_thing(dependencies={"authorizer": "ns:authz:aaa"})])

    assert len(result["errors"]) == 1


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def test_a_deployment_without_the_dependency_is_out_of_scope():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(client, [_thing("plain", "hmd-ms-plain")], env_info)

    assert result == {"errors": [], "warnings": []}


def test_a_deployment_that_never_runs_the_tool_is_out_of_scope():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(
        client,
        [_thing("helm", "hmd-helm-thing", dependencies={"authorizer": "authz"})],
        env_info,
    )

    assert result == {"errors": [], "warnings": []}


def test_a_resource_declared_dependency_is_in_scope_whatever_its_class():
    client = make_client()
    env_info = _world(client)
    ci = ClassInformation(client)
    ci.add_repo_version_by_name("custom-authorizer", "1.0.0", {}, {})
    ci.add_repo_version_by_name(
        "hmd-ms-resource-thing",
        "0.1.5",
        {"authorizer": {"required": "true", "resource": dict(AUTHZ, version="2.0.0")}},
        {},
        deploy_commands=[["cdktf"]],
    )
    env_info.add_repo_instance(
        "custom",
        "aaa",
        {},
        ci.get_repo_class_version("custom-authorizer", "1.0.0"),
        status="DEPLOYED",
    )
    env_info.load_environment()

    # The deployment's resolver may have accepted "custom" by some fallback;
    # the requirement does not.
    result = _evaluate(
        client,
        [_thing("rt", "hmd-ms-resource-thing", dependencies={"authorizer": "custom"})],
        env_info,
    )

    assert len(result["errors"]) == 1
    assert "custom-authorizer 1.0.0" in result["errors"][0]["message"]


# --------------------------------------------------------------------------- #
# Which tool set
# --------------------------------------------------------------------------- #


def test_a_tool_set_that_declares_nothing_imposes_nothing():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(
        client,
        [_thing(dependencies={"authorizer": "authz"})],
        env_info,
        image=OLD_IMAGE,
    )

    assert result == {"errors": [], "warnings": []}


def test_an_unregistered_image_is_a_warning_not_a_pass():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(
        client,
        [_thing(dependencies={"authorizer": "authz"})],
        env_info,
        image="ghcr.io/hmdlabs/hmd-img-projectbuilder:9.9.9",
    )

    assert result["errors"] == []
    assert [w["type"] for w in result["warnings"]] == ["toolset_unregistered"]
    assert "hmd-img-projectbuilder 9.9.9" in result["warnings"][0]["message"]


def test_the_environment_image_map_selects_the_tool_set():
    client = make_client()
    env_info = _world(client)

    with patch.dict(
        os.environ,
        {"HMD_APP_IMAGE": OLD_IMAGE, "HMD_APP_IMAGE_MAP": '{"dev": "%s"}' % NEW_IMAGE},
    ):
        result = DeployRequirementEvaluator(client).evaluate(
            [_thing(dependencies={"authorizer": "authz"})], env_info
        )

    assert len(result["errors"]) == 1


# --------------------------------------------------------------------------- #
# Severity and overrides
# --------------------------------------------------------------------------- #


def test_warn_severity_reports_and_passes():
    client = make_client()
    env_info = _world(client, requirement=dict(REQUIREMENT, severity="warn"))

    result = _evaluate(client, [_thing(dependencies={"authorizer": "authz"})], env_info)

    assert result["errors"] == []
    assert [w["requirement"] for w in result["warnings"]] == [
        "route-scoped-authorizer-cache"
    ]


def test_an_acknowledged_requirement_is_reported_but_does_not_block():
    client = make_client()
    env_info = _world(client)

    result = _evaluate(
        client,
        [_thing(dependencies={"authorizer": "authz"})],
        env_info,
        acknowledged=["route-scoped-authorizer-cache"],
    )

    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert "acknowledged" in result["warnings"][0]["message"].lower()


def test_a_requirement_nothing_can_satisfy_is_flagged():
    client = make_client()
    unknown = dict(
        REQUIREMENT,
        name="needs-unknown",
        applies_to={"tool": "cdktf"},
        requires={
            "resource_namespace": "nowhere.io",
            "resource_definition_name": "nothing",
            "version_spec": ">=1.0.0",
        },
    )
    env_info = _world(client, requirement=unknown)

    result = _evaluate(client, [_thing("plain", "hmd-ms-plain")], env_info)

    assert result["errors"] == []
    assert [w["type"] for w in result["warnings"]] == ["requirement_unsatisfiable"]


def test_a_version_spec_the_resolver_cannot_read_is_flagged_not_ignored():
    client = make_client()
    # Ordered specs need three components here; ">=2.0" matches nothing.
    env_info = _world(
        client,
        requirement=dict(REQUIREMENT, requires=dict(AUTHZ, version_spec=">=2.0")),
    )

    result = _evaluate(client, [_thing(dependencies={"authorizer": "authz"})], env_info)

    assert result["errors"] == []
    assert [w["type"] for w in result["warnings"]] == ["requirement_invalid"]
    assert ">=2.0" in result["warnings"][0]["message"]


# --------------------------------------------------------------------------- #
# Image resolution
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "image, expected",
    [
        (OLD_IMAGE, ("hmd-img-projectbuilder", "0.4.326")),
        (
            "123.dkr.ecr.us-east-1.amazonaws.com/ghcr/hmdlabs/hmd-img-projectbuilder:0.4.326",
            ("hmd-img-projectbuilder", "0.4.326"),
        ),
        (
            "ghcr.io/hmdlabs/hmd-img-projectbuilder:0.4.326@sha256:abc",
            ("hmd-img-projectbuilder", "0.4.326"),
        ),
        (
            "localhost:5000/hmd-img-projectbuilder:0.4.1",
            ("hmd-img-projectbuilder", "0.4.1"),
        ),
        ("ghcr.io/hmdlabs/hmd-img-projectbuilder", None),
        ("", None),
    ],
)
def test_parse_image_reference(image, expected):
    assert parse_image_reference(image) == expected


def test_resolve_deploy_image_prefers_the_environment_map():
    env = {
        "HMD_APP_IMAGE": OLD_IMAGE,
        "HMD_APP_IMAGE_MAP": '{"prod": "%s"}' % NEW_IMAGE,
    }
    with patch.dict(os.environ, env):
        assert resolve_deploy_image("prod") == NEW_IMAGE
        assert resolve_deploy_image("dev") == OLD_IMAGE
        assert resolve_deploy_image(None) == OLD_IMAGE
