"""Tests for the DAG-free environment BOM (NERD0015 SPEC0001).

``build_environment_bom`` replaces ``build_deployment_dag`` + ``DeployBomCreator``
for read-only consumers (``get_deployment_bom``, ``find_repo_class_instances``,
``compare_environments``). The node shape and the error messages are the
contract nsctl and the GUI rely on.
"""

import pytest
from hmd_cli_tools import ServiceException

from hmd_ms_deployment_core.bom import (
    NO_DATA_MESSAGE,
    NO_ROOTS_MESSAGE,
    build_environment_bom,
    build_environment_bom_or_empty,
    order_repo_instances,
)
from hmd_ms_deployment_core.environment_information import EnvironmentInformation


class _Rel:
    def __init__(self, ref_to):
        self.ref_to = ref_to


class _RI:
    """Just enough of a RepoInstance for the sort: identity, name, req edges."""

    def __init__(self, name, deps=()):
        self.identifier = f"id-{name}"
        self.name = name
        self._deps = list(deps)

    def get_from_repo_instance_req_repo_instance_hmd_lang_deployment(self):
        return [_Rel(f"id-{d}") for d in self._deps]


def _names(ordered):
    return [ri.name for ri in ordered]


def test_dependencies_come_before_dependents():
    a, b, c = _RI("a", ["b", "c"]), _RI("b", ["c"]), _RI("c")
    ordered = _names(order_repo_instances([a, b, c], rs=None))
    assert ordered.index("c") < ordered.index("b") < ordered.index("a")


def test_edges_outside_the_set_are_ignored():
    # "b" depends on "zzz", which is not in the batch (already deployed elsewhere).
    a, b = _RI("a", ["b"]), _RI("b", ["zzz"])
    assert _names(order_repo_instances([a, b], rs=None)) == ["b", "a"]


def test_order_is_deterministic_for_independent_nodes():
    ris = [_RI(n) for n in ["m", "k", "z", "a"]]
    first = _names(order_repo_instances(ris, rs=None))
    second = _names(order_repo_instances(list(reversed(ris)), rs=None))
    # Input order is preserved for independent nodes; callers sort by name first.
    assert first == ["m", "k", "z", "a"]
    assert second == ["a", "z", "k", "m"]


def test_empty_set_raises_the_dag_builder_message():
    with pytest.raises(ServiceException) as e:
        order_repo_instances([], rs=None)
    assert e.value.message == NO_DATA_MESSAGE


def test_full_cycle_has_no_independent_node():
    a, b = _RI("a", ["b"]), _RI("b", ["a"])
    with pytest.raises(ServiceException) as e:
        order_repo_instances([a, b], rs=None)
    assert e.value.message == NO_ROOTS_MESSAGE


def test_partial_cycle_names_the_stuck_instances():
    # "leaf" is the only node nothing depends on, so the roots check passes and
    # Kahn's algorithm is what detects the a<->b cycle.
    root, leaf = _RI("root"), _RI("leaf", ["root"])
    a, b = _RI("a", ["b", "root"]), _RI("b", ["a"])
    with pytest.raises(ServiceException) as e:
        order_repo_instances([root, leaf, a, b], rs=None)
    assert "Graph is not acyclic" in e.value.message
    assert "a" in e.value.message and "b" in e.value.message


def test_environment_bom_shape_and_order(base_environment):
    client, rs = base_environment
    env = client.search_environment_hmd_lang_deployment(
        {"attribute": "type", "operator": "=", "value": "dev"}
    )[0]
    bom = build_environment_bom(EnvironmentInformation(env, client))

    names = [e["repo_instance_name"] for e in bom]
    assert set(names) == {"ri1", "ri2", "ri3"}
    assert names.index("ri2") < names.index("ri1")
    assert names.index("ri3") < names.index("ri1")

    ri1 = [e for e in bom if e["repo_instance_name"] == "ri1"][0]
    assert (
        list(ri1.keys())
        == [
            "repo_instance_name",
            "repo_class_name",
            "repo_class_version",
            "deployment_id",
            "auto_deploy",  # the model default is the string "false", which is truthy -- as before
            "status",
            "instance_configuration",
            "dependencies",
        ]
    )
    assert ri1["repo_class_name"] == "rc1"
    assert ri1["repo_class_version"] == "0.1.1"
    assert ri1["deployment_id"] == "aaa"
    assert ri1["status"] == "DEPLOYED"
    assert ri1["instance_configuration"] == {"config_ri2": "value_ri2"}
    assert ri1["dependencies"] == {"rc1-rc2": "ri2", "rc1-rc3": "ri3"}

    ri2 = [e for e in bom if e["repo_instance_name"] == "ri2"][0]
    assert ri2["dependencies"] == {}


def test_or_empty_only_swallows_the_no_data_case(base_environment):
    client, rs = base_environment
    from hmd_lang_deployment.environment import Environment

    empty = Environment(type="empty", account_number="1", hmd_region="reg1")
    client.upsert(empty)
    assert build_environment_bom_or_empty(EnvironmentInformation(empty, client)) == []
    with pytest.raises(ServiceException):
        build_environment_bom(EnvironmentInformation(empty, client))
