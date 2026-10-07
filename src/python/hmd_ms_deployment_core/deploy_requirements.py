"""BACON toolset deploy requirements (hmd-docs-bacon ``spec/toolset``).

A deployment is the product of two versions: the repo class being deployed and
the tool set distribution deploying it. A distribution declares, in its
manifest's ``toolset.deploy_requirements``, what every deployment it performs
needs of that deployment's dependencies -- for example, that a projectbuilder
whose ``cdktf`` tool gives API Gateways a route-scoped authorizer cache needs an
authorizer providing ``auth.neuronsphere.io/api-gateway-authorizer >=2.0``.

This module evaluates those requirements for a proposed change set before
anything is written, so the same evaluation serves the dry-run
``validate_changeset`` and the gate at the top of ``apply_changeset``.
"""

import logging
from typing import Dict, Iterable, List, Optional, Set, Tuple

from hmd_base_service.exceptions import ServiceException
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_lang_deployment.repo_class_version import RepoClassVersion
from hmd_lang_deployment.repo_instance import RepoInstance
from hmd_lang_deployment.resource_definition import ResourceDefinition

from hmd_ms_deployment_core.class_information import ClassInformation
from hmd_ms_deployment_core.deploy_image import (  # noqa: F401 (re-exported)
    parse_image_reference,
    resolve_deploy_image,
)
from hmd_ms_deployment_core.instance_config import InstanceConfigResolver
from hmd_ms_deployment_core.resource_information import ResourceInformation
from hmd_ms_deployment_core.resource_name import parse_resource_name
from hmd_ms_deployment_core.version import VersionSpecifier, VersionSpecifierException

logger = logging.getLogger(f"HMD.{__name__}")


def _tool_identifiers(deploy_commands: Optional[List]) -> Optional[Set[str]]:
    """The tool identifiers ``deploy.commands`` invokes, or None when unknown."""
    if deploy_commands is None:
        return None
    tools = set()
    for command in deploy_commands:
        if isinstance(command, list) and command:
            tools.add(command[0])
        elif isinstance(command, str):
            tools.add(command)
    return tools


def _resource_label(ref: Dict) -> str:
    return f"{ref['resource_namespace']}/{ref['resource_definition_name']}"


class DeployRequirementEvaluator:
    def __init__(self, client: HmdLangDeploymentClient):
        self.client = client
        self.class_info = ClassInformation(client)
        self.resource_info = ResourceInformation(client)
        self.rs = self.resource_info.relationship_support
        self._producing_classes: Dict[Tuple[str, str], Set[str]] = {}
        self._producer_ids: Dict[Tuple[str, str, str], Set[str]] = {}

    def evaluate(
        self,
        changes: List[Dict],
        env_info=None,
        acknowledged: Iterable[str] = (),
    ) -> Dict[str, List[Dict]]:
        """Evaluate the deploying tool set's requirements against ``changes``.

        ``changes`` have the ``ChangeSet.definition`` item shape. With
        ``env_info`` the image is the one mapped to that environment's type and
        instances are resolved within it; without it (a dry run with no
        environment) the default image is used and instances are found by name.

        Returns ``{"errors": [...], "warnings": [...]}``. An unmet ``error``
        requirement is an error unless its name is in ``acknowledged``, in which
        case it is reported as a warning.
        """
        errors: List[Dict] = []
        warnings: List[Dict] = []
        environment_type = env_info.environment.type if env_info else None
        image = resolve_deploy_image(environment_type)
        if not image:
            return {"errors": errors, "warnings": warnings}

        toolset_rcv, coordinates = self._toolset_version(image)
        if toolset_rcv is None:
            label = " ".join(coordinates) if coordinates else image
            warnings.append(
                {
                    "type": "toolset_unregistered",
                    "instance": "",
                    "message": (
                        f"The deploy image {image} resolves to {label}, which is not a "
                        f"registered repo class version; its deploy requirements were "
                        f"not evaluated."
                    ),
                }
            )
            return {"errors": errors, "warnings": warnings}

        toolset = toolset_rcv.toolset or {}
        requirements = toolset.get("deploy_requirements") or []
        if not requirements:
            return {"errors": errors, "warnings": warnings}

        imposed_by = f"{coordinates[0]} {coordinates[1]}"
        if toolset.get("name"):
            imposed_by += f" (tool set {toolset['name']})"
        acknowledged = set(acknowledged or ())
        planned = {
            change.get("repo_instance_name"): change
            for change in changes
            if change.get("repo_instance_name")
        }

        readable = []
        for requirement in requirements:
            requires = requirement["requires"]
            try:
                VersionSpecifier(requires["version_spec"])
            except (VersionSpecifierException, AssertionError, ValueError) as ex:
                warnings.append(
                    {
                        "type": "requirement_invalid",
                        "requirement": requirement["name"],
                        "instance": "",
                        "message": (
                            f"Deploy requirement '{requirement['name']}' (imposed by "
                            f"{imposed_by}) has a version_spec, "
                            f"{requires['version_spec']!r}, this deployment service "
                            f"cannot read ({ex}); it was not evaluated."
                        ),
                    }
                )
                continue
            readable.append(requirement)
            if not self._producer_version_ids(requires):
                warnings.append(
                    {
                        "type": "requirement_unsatisfiable",
                        "requirement": requirement["name"],
                        "instance": "",
                        "message": (
                            f"Deploy requirement '{requirement['name']}' (imposed by "
                            f"{imposed_by}) needs {_resource_label(requires)} "
                            f"{requires['version_spec']}, which no registered repo "
                            f"class version produces."
                        ),
                    }
                )

        for change in changes:
            name = change.get("repo_instance_name")
            consumer_rcv = self._planned_rcv(change)
            if consumer_rcv is None:
                # validate_changeset reports the missing class version itself.
                continue
            tools = _tool_identifiers(consumer_rcv.deploy_commands)
            dependencies = self._dependencies(change, env_info)

            for requirement in readable:
                applies_to = requirement.get("applies_to") or {}
                # A version registered before deploy_commands was recorded has
                # unknown tools; evaluating it can only cost a warning.
                if (
                    applies_to.get("tool")
                    and tools is not None
                    and applies_to["tool"] not in tools
                ):
                    continue
                consumed = (
                    applies_to.get("consumes_resource") or requirement["requires"]
                )
                for role, target in dependencies:
                    if not self._is_consuming(
                        consumer_rcv, role, target, planned, consumed
                    ):
                        continue
                    finding = self._check(
                        requirement, imposed_by, name, role, target, planned, env_info
                    )
                    if finding is None:
                        continue
                    severity = requirement.get("severity", "error")
                    if severity == "error" and requirement["name"] not in acknowledged:
                        errors.append(finding)
                    else:
                        if severity == "error":
                            finding["message"] = (
                                "Acknowledged by the operator: " + finding["message"]
                            )
                        warnings.append(finding)

        return {"errors": errors, "warnings": warnings}

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    def _toolset_version(
        self, image: str
    ) -> Tuple[Optional[RepoClassVersion], Optional[Tuple[str, str]]]:
        coordinates = parse_image_reference(image)
        if coordinates is None:
            return None, None
        try:
            return self.class_info.get_repo_class_version(*coordinates), coordinates
        except ServiceException:
            return None, coordinates

    def _planned_rcv(self, change: Dict) -> Optional[RepoClassVersion]:
        try:
            return self.class_info.get_repo_class_version(
                change.get("repo_class_name"), change.get("repo_class_version")
            )
        except (ServiceException, AssertionError):
            return None

    def _find_instance(self, name: str, env_info) -> Optional[RepoInstance]:
        if env_info is not None:
            return env_info.get_repo_instance(name)
        found = self.client.search_repo_instance_hmd_lang_deployment(
            {"attribute": "name", "operator": "=", "value": name}
        )
        return found[0] if found else None

    def _dependencies(self, change: Dict, env_info) -> List[Tuple[str, str]]:
        """``(role, instance_name)`` pairs: the change's own, or else the edges
        the instance already has (a redeploy that names none keeps its own)."""
        supplied = change.get("dependencies")
        if supplied is not None:
            pairs = []
            for role, targets in supplied.items():
                for target in targets if isinstance(targets, list) else [targets]:
                    pairs.append((role, parse_resource_name(target).instance_name))
            return pairs

        instance = self._find_instance(change.get("repo_instance_name"), env_info)
        if instance is None:
            return []
        return [
            (rel.role, self.rs.ref_to(rel).name)
            for rel in self.client.get_from_repo_instance_req_repo_instance_hmd_lang_deployment(
                instance
            )
        ]

    def _bound_version(
        self, target: str, planned: Dict, env_info
    ) -> Optional[RepoClassVersion]:
        """The version ``target`` will run once the change set is applied."""
        if target in planned:
            return self._planned_rcv(planned[target])
        instance = self._find_instance(target, env_info)
        if instance is None:
            return None
        rid = InstanceConfigResolver(self.client, self.rs)._get_next_or_deployed(
            instance
        )
        if rid is None:
            return None
        rels = self.client.get_from_repo_instance_deployment_has_repo_class_version_hmd_lang_deployment(
            rid
        )
        return self.rs.ref_to(rels[0]) if rels else None

    def _class_name(self, rcv: RepoClassVersion) -> str:
        rels = self.client.get_to_repo_class_has_repo_class_version_hmd_lang_deployment(
            rcv
        )
        return self.rs.ref_from(rels[0]).repo_class_name if rels else "?"

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #

    def _definitions_named(self, ref: Dict) -> List[ResourceDefinition]:
        return [
            rd
            for rd in self.client.search_resource_definition_hmd_lang_deployment(
                {
                    "attribute": "resource_namespace",
                    "operator": "=",
                    "value": ref["resource_namespace"],
                }
            )
            if rd.resource_definition_name == ref["resource_definition_name"]
        ]

    def _is_or_isa(self, rd: ResourceDefinition, ref: Dict) -> bool:
        return any(
            ancestor.resource_namespace == ref["resource_namespace"]
            and ancestor.resource_definition_name == ref["resource_definition_name"]
            for ancestor in self.resource_info.get_ancestry(rd)
        )

    def _classes_producing(self, ref: Dict) -> Set[str]:
        """Repo classes with any version producing ``ref`` (or a subtype)."""
        key = (ref["resource_namespace"], ref["resource_definition_name"])
        if key not in self._producing_classes:
            self._producing_classes[key] = {
                self._class_name(rcv)
                for rd in self._definitions_named(ref)
                for rcv in self.resource_info.get_producers(rd, include_subtypes=True)
            }
        return self._producing_classes[key]

    def _producer_version_ids(self, requires: Dict) -> Set[str]:
        key = (
            requires["resource_namespace"],
            requires["resource_definition_name"],
            requires["version_spec"],
        )
        if key not in self._producer_ids:
            self._producer_ids[key] = self.resource_info.get_producer_class_version_ids(
                dict(
                    resource_namespace=requires["resource_namespace"],
                    resource_definition_name=requires["resource_definition_name"],
                    version="",
                ),
                requires["version_spec"],
            )
        return self._producer_ids[key]

    def _is_consuming(
        self,
        consumer_rcv: RepoClassVersion,
        role: str,
        target: str,
        planned: Dict,
        ref: Dict,
    ) -> bool:
        """BACON spec/toolset "Consuming dependencies": the role's ``resource``
        block names ``ref``, or the bound instance's class has any version
        producing it."""
        for rel in self.client.get_from_repo_class_version_req_resource_definition_hmd_lang_deployment(
            consumer_rcv
        ):
            if rel.role == role and self._is_or_isa(self.rs.ref_to(rel), ref):
                return True

        if target in planned:
            class_name = planned[target].get("repo_class_name")
        else:
            instance = self._find_instance(target, None)
            if instance is None:
                return False
            rels = (
                self.client.get_from_repo_instance_isa_repo_class_hmd_lang_deployment(
                    instance
                )
            )
            class_name = self.rs.ref_to(rels[0]).repo_class_name if rels else None
        return class_name in self._classes_producing(ref)

    def _provided(self, rcv: RepoClassVersion, ref: Dict) -> List[str]:
        """The versions of ``ref`` (or its subtypes) ``rcv`` declares it produces."""
        return sorted(
            rd.version
            for rd in (
                self.rs.ref_to(rel)
                for rel in self.client.get_from_repo_class_version_produces_resource_definition_hmd_lang_deployment(
                    rcv
                )
            )
            if self._is_or_isa(rd, ref)
        )

    def _check(
        self,
        requirement: Dict,
        imposed_by: str,
        instance: str,
        role: str,
        target: str,
        planned: Dict,
        env_info,
    ) -> Optional[Dict]:
        requires = requirement["requires"]
        producer_ids = self._producer_version_ids(requires)
        bound = self._bound_version(target, planned, env_info)
        if bound is not None and bound.identifier in producer_ids:
            return None

        label = _resource_label(requires)
        if bound is None:
            bound_text = "has no deployed version"
        else:
            class_name = self._class_name(bound)
            provided = self._provided(bound, requires)
            bound_text = f"is {class_name} {bound.version}, which " + (
                f"provides {label} {', '.join(provided)}"
                if provided
                else f"provides no {label}"
            )

        remedy = f"deploy a version of '{target}' that provides {label} {requires['version_spec']}"
        if bound is not None:
            candidates = sorted(
                rcv.version
                for rcv in (
                    self.rs.ref_to(rel)
                    for rel in self.client.get_from_repo_class_has_repo_class_version_hmd_lang_deployment(
                        self.class_info.get_repo_class(self._class_name(bound))
                    )
                )
                if rcv.identifier in producer_ids
            )
            if candidates:
                remedy = (
                    f"add '{target}' at {self._class_name(bound)} "
                    f"{' or '.join(candidates)} to this change set"
                )

        return {
            "type": "deploy_requirement",
            "severity": requirement.get("severity", "error"),
            "requirement": requirement["name"],
            "instance": instance,
            "message": (
                f"Deploy requirement '{requirement['name']}' (imposed by {imposed_by}) "
                f"is not met for '{instance}': dependency '{role}' -> '{target}' "
                f"{bound_text}; required {label} {requires['version_spec']}. "
                f"Why: {requirement.get('reason', '')} Fix: {remedy}."
            ),
        }
