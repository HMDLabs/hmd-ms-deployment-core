from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_schema_loader import DefaultLoader

from hmd_ms_deployment_core.changeset_validation import (
    dependency_targets,
    validate_changes,
)
from hmd_ms_deployment_core.class_information import ClassInformation


def _new_client():
    loader = DefaultLoader("schemas/local")
    return HmdLangDeploymentClient(MemoryClient(loader, {}))


def _registered_client():
    client = _new_client()
    class_info = ClassInformation(client)
    class_info.add_repo_version_by_name("hmd-inf-db", "0.1.0", {}, {})
    class_info.add_repo_version_by_name(
        "hmd-ms-app",
        "0.1.0",
        {
            "db": {"repo_class_name": "hmd-inf-db", "required": "true"},
            "cache": {"repo_class_name": "hmd-inf-db", "required": "false"},
        },
        {},
    )
    return client


def _change(name, rc, deps=None):
    change = {
        "repo_instance_name": name,
        "repo_class_name": rc,
        "repo_class_version": "0.1.0",
        "deployment_id": "1",
    }
    if deps is not None:
        change["dependencies"] = deps
    return change


def _types(findings):
    return sorted(f["type"] for f in findings)


def test_dependency_targets_handles_names_shorthand_and_lists():
    assert dependency_targets("db") == ["db"]
    assert dependency_targets("ns:db:3") == ["db"]
    assert dependency_targets(["a", "ns:b:1"]) == ["a", "b"]


def test_empty_changes_is_schema_error():
    result = validate_changes(_new_client(), [])
    assert not result["valid"]
    assert _types(result["errors"]) == ["schema"]


def test_valid_changeset():
    result = validate_changes(
        _registered_client(),
        [_change("db", "hmd-inf-db"), _change("app", "hmd-ms-app", {"db": "db"})],
    )
    assert result["valid"], result
    assert _types(result["warnings"]) == ["missing_optional_role"]


def test_missing_required_role_and_unknown_version():
    result = validate_changes(
        _registered_client(),
        [
            _change("app", "hmd-ms-app", {}),
            {**_change("x", "hmd-inf-db"), "repo_class_version": "9.9.9"},
        ],
    )
    assert _types(result["errors"]) == [
        "missing_class_version",
        "missing_required_role",
    ]


def test_list_valued_dependencies_resolve_and_detect_cycles():
    result = validate_changes(
        _registered_client(),
        [
            _change("db", "hmd-inf-db", {"peer": ["app"]}),
            _change("app", "hmd-ms-app", {"db": ["db"]}),
        ],
    )
    assert "circular_dependency" in _types(result["errors"])
    assert "unresolved_dependency" not in _types(result["errors"])
