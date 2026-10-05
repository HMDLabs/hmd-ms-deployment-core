"""Dry-run validation of a proposed ChangeSet definition.

``validate_changes`` is the body of ``POST /apiop/validate_changeset``, extracted
so the operations that *produce* a ChangeSet definition -- Bundle resolution
(NERD0010 SPEC0008) and Release resolution (NERD0016 SPEC0009) -- run exactly
the checks a hand-written ChangeSet gets. It performs no DB writes.
"""

import logging
from typing import Iterable, List, Optional, Tuple

from hmd_base_service.exceptions import ServiceException
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient

from .class_information import ClassInformation
from .deploy_requirements import DeployRequirementEvaluator
from .environment_information import EnvironmentInformation

logger = logging.getLogger(__name__)


def dependency_targets(dep_target) -> List[str]:
    """Instance names a ``dependencies`` value refers to.

    A value is an instance name, the NERD0002 ``ns:<instance>:<deployment_id>``
    shorthand, or a list of either (a role bound to several instances, as the
    BOM emits it).
    """
    targets = dep_target if isinstance(dep_target, list) else [dep_target]
    names = []
    for target in targets:
        if isinstance(target, str) and target.startswith("ns:"):
            parts = target.split(":")
            if len(parts) >= 2:
                target = parts[1]
        names.append(target)
    return names


def validate_changes(
    deploy_client: HmdLangDeploymentClient,
    changes: list,
    environment_types: Optional[Iterable[str]] = None,
    acknowledged: Iterable[str] = (),
) -> dict:
    """Validate ``changes`` (``ChangeSet.definition`` items).

    Returns ``{"valid": bool, "errors": [...], "warnings": [...]}`` where each
    error/warning is ``{"type", "instance", "message"}``. See
    ``validate_changeset`` for the checks performed.
    """
    errors: List[dict] = []
    warnings: List[dict] = []

    def _err(kind: str, instance: str, message: str) -> None:
        errors.append({"type": kind, "instance": instance, "message": message})

    def _warn(kind: str, instance: str, message: str) -> None:
        warnings.append({"type": kind, "instance": instance, "message": message})

    if not isinstance(changes, list) or not changes:
        return {
            "valid": False,
            "errors": [
                {
                    "type": "schema",
                    "instance": "",
                    "message": "'changes' must be a non-empty list.",
                }
            ],
            "warnings": [],
        }

    class_info = ClassInformation(deploy_client)

    # Index changes by repo_instance_name so dependency references can be
    # resolved against siblings in the same proposed changeset.
    instances_by_name: dict = {}
    for change in changes:
        name = change.get("repo_instance_name")
        if not name:
            _err(
                "schema",
                "",
                "Each change must include 'repo_instance_name'.",
            )
            continue
        if name in instances_by_name:
            _err(
                "duplicate",
                name,
                f"Repo instance '{name}' appears more than once in the change set.",
            )
        instances_by_name[name] = change

    # Per-instance validation: class + version exists, version_spec on each
    # required role on the class version is supplied, dependency targets
    # resolve.
    for name, change in instances_by_name.items():
        repo_class_name = change.get("repo_class_name")
        repo_class_version = change.get("repo_class_version")
        if not repo_class_name or not repo_class_version:
            _err(
                "schema",
                name,
                "Each change must include 'repo_class_name' and 'repo_class_version'.",
            )
            continue

        try:
            rcv = class_info.get_repo_class_version(repo_class_name, repo_class_version)
        except ServiceException as e:
            _err("missing_class_version", name, str(e))
            continue

        required_roles: List[Tuple[str, str, bool]] = []
        for (
            rel
        ) in deploy_client.get_from_repo_class_version_req_repo_class_hmd_lang_deployment(
            rcv
        ):
            role = rel.role
            version_spec = rel.version_spec
            required = str(rel.required).lower() == "true"
            required_roles.append((role, version_spec, required))

        supplied_deps = change.get("dependencies") or {}
        for role, version_spec, required in required_roles:
            if role not in supplied_deps:
                if required:
                    _err(
                        "missing_required_role",
                        name,
                        f"Required dependency role '{role}' (spec {version_spec}) is not supplied.",
                    )
                else:
                    _warn(
                        "missing_optional_role",
                        name,
                        f"Optional dependency role '{role}' (spec {version_spec}) is not supplied.",
                    )

        for role, dep_target in supplied_deps.items():
            for target_name in dependency_targets(dep_target):
                if target_name in instances_by_name:
                    continue
                # Allow already-deployed instances (best-effort check via
                # RepoInstance search) — only flag if it isn't a sibling
                # change AND no matching RepoInstance exists.
                existing = deploy_client.search_repo_instance_hmd_lang_deployment(
                    {"attribute": "name", "operator": "=", "value": target_name}
                )
                if not existing:
                    _err(
                        "unresolved_dependency",
                        name,
                        f"Dependency '{role}' -> '{target_name}' is neither in the change set "
                        f"nor an existing deployed RepoInstance.",
                    )

    # BACON toolset deploy requirements: what the tool set that will run
    # these deploys needs of their dependencies.
    env_infos: List[Optional[EnvironmentInformation]] = []
    environment_types = list(environment_types or [])
    if environment_types:
        known = {
            env.type: env
            for env in deploy_client.search_environment_hmd_lang_deployment({})
        }
        for environment_type in environment_types:
            if environment_type not in known:
                _err(
                    "unknown_environment",
                    "",
                    f"Environment '{environment_type}' does not exist.",
                )
                continue
            env_infos.append(
                EnvironmentInformation(known[environment_type], deploy_client)
            )
    else:
        env_infos.append(None)

    evaluator = DeployRequirementEvaluator(deploy_client)
    seen_findings: set = set()
    for env_info in env_infos:
        findings = evaluator.evaluate(
            list(instances_by_name.values()),
            env_info,
            acknowledged=acknowledged or (),
        )
        for kind, target in (("errors", errors), ("warnings", warnings)):
            for finding in findings[kind]:
                key = (finding["type"], finding["instance"], finding["message"])
                if key not in seen_findings:
                    seen_findings.add(key)
                    target.append(finding)

    # Cycle detection over instances supplied in this changeset.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in instances_by_name}

    def _visit(name: str, stack: List[str]) -> Optional[List[str]]:
        color[name] = GRAY
        stack.append(name)
        for target in (instances_by_name[name].get("dependencies") or {}).values():
            for target_name in dependency_targets(target):
                if target_name not in color:
                    continue
                if color[target_name] == GRAY:
                    return stack[stack.index(target_name) :] + [target_name]
                if color[target_name] == WHITE:
                    cycle = _visit(target_name, stack)
                    if cycle is not None:
                        return cycle
        stack.pop()
        color[name] = BLACK
        return None

    reported_cycles: set = set()
    for name in list(color.keys()):
        if color[name] == WHITE:
            cycle = _visit(name, [])
            if cycle is not None:
                key = tuple(sorted(set(cycle)))
                if key not in reported_cycles:
                    reported_cycles.add(key)
                    _err(
                        "circular_dependency",
                        cycle[0],
                        "Circular dependency detected: " + " -> ".join(cycle),
                    )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }
