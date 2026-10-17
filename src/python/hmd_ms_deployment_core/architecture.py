"""Architecture views (NERD036, hmd-cli-neuronsphere).

Three things live here:

* the **rendering template** registry -- templates a deployed repo instance
  registers, any number of names and versions side by side;
* **architecture views** -- a saved scope and template, which documentation
  refers to by name;
* the **architecture model** -- the neutral ``architecture.neuronsphere.io/v1``
  document a template renders, built from what the registry already records:
  each class version's dependency edges and the ``deploy.resources`` it
  declared.

The model carries no audience, layout or styling; those belong to templates.
"""

import logging
from collections import deque
from typing import Dict, List, Optional, Tuple

from hmd_base_service.exceptions import ServiceException
from hmd_lang_deployment.architecture_view import ArchitectureView
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_lang_deployment.rendering_template import RenderingTemplate
from hmd_lang_deployment.repo_class_version import RepoClassVersion
from hmd_lang_deployment.repo_instance_provides_rendering_template import (
    RepoInstanceProvidesRenderingTemplate,
)

from .class_information import ClassInformation, build_relationship_support
from .resource_information import ResourceInformation, version_matches
from .version import sort_versions

logger = logging.getLogger(__name__)

MODEL_SCHEMA = "architecture.neuronsphere.io/v1"

_TEMPLATE_FIELDS = (
    "template_name",
    "version",
    "title",
    "description",
    "descriptor",
    "artifact_location",
    "files",
)

_VIEW_FIELDS = (
    "view_name",
    "title",
    "roots",
    "depth",
    "include",
    "exclude",
    "environment",
    "template_name",
    "template_version_spec",
    "params",
)


def _to_dict(entity, fields) -> Dict:
    out = {"identifier": entity.identifier}
    for field in fields:
        out[field] = getattr(entity, field, None)
    return out


# --------------------------------------------------------------------------- #
# Rendering templates (SPEC004)
# --------------------------------------------------------------------------- #


def _templates_named(client: HmdLangDeploymentClient, name: str) -> List:
    return client.search_rendering_template_hmd_lang_deployment(
        {"attribute": "template_name", "operator": "=", "value": name}
    )


def register_rendering_template(client: HmdLangDeploymentClient, payload: Dict) -> Dict:
    """Register (or replace) one version of a template.

    The storage layer does not enforce composite business ids, so an existing
    registration of the same name and version is found and updated in place
    rather than duplicated. ``repo_instance_id``, when given, records the
    instance that deployed the template.
    """
    for key in ("template_name", "version", "descriptor"):
        if not payload.get(key):
            raise ServiceException(f"A rendering template needs {key}.")

    existing = [
        t
        for t in _templates_named(client, payload["template_name"])
        if t.version == payload["version"]
    ]
    template = (
        existing[0]
        if existing
        else RenderingTemplate(
            template_name=payload["template_name"],
            version=payload["version"],
            descriptor=payload["descriptor"],
        )
    )
    for field in _TEMPLATE_FIELDS:
        if field in payload:
            setattr(template, field, payload[field])
    template = client.upsert(template)

    instance_id = payload.get("repo_instance_id")
    if instance_id:
        instance = client.get_repo_instance_hmd_lang_deployment(instance_id)
        if instance is None:
            raise ServiceException(f"RepoInstance, {instance_id}, not found.")
        already = any(
            ClassInformation._ref_to_id(rel) == template.identifier
            for rel in client.get_from_repo_instance_provides_rendering_template_hmd_lang_deployment(
                instance
            )
        )
        if not already:
            client.upsert(
                RepoInstanceProvidesRenderingTemplate(
                    ref_from=instance.identifier, ref_to=template.identifier
                )
            )
    return _to_dict(template, _TEMPLATE_FIELDS)


def list_rendering_templates(client: HmdLangDeploymentClient) -> List[Dict]:
    templates = client.search_rendering_template_hmd_lang_deployment({})
    rows = [_to_dict(t, _TEMPLATE_FIELDS) for t in templates]
    return sorted(rows, key=lambda r: (r["template_name"], r["version"]))


def get_rendering_template(
    client: HmdLangDeploymentClient, name: str, version: str
) -> Dict:
    for template in _templates_named(client, name):
        if template.version == version:
            return _to_dict(template, _TEMPLATE_FIELDS)
    raise ServiceException(f"Rendering template {name} {version} not found.")


def resolve_rendering_template(
    client: HmdLangDeploymentClient, name: str, version_spec: Optional[str]
) -> RenderingTemplate:
    """The newest registered version of ``name`` that satisfies ``version_spec``."""
    candidates = [
        t
        for t in _templates_named(client, name)
        if version_matches(t.version, version_spec)
    ]
    candidates = sort_versions(candidates, lambda t: t.version)
    if not candidates:
        raise ServiceException(
            f"No registered rendering template {name} satisfies "
            f"{version_spec or 'any version'}."
        )
    return candidates[0]


# --------------------------------------------------------------------------- #
# Architecture views (SPEC005)
# --------------------------------------------------------------------------- #


def _views_named(client: HmdLangDeploymentClient, name: str) -> List:
    return client.search_architecture_view_hmd_lang_deployment(
        {"attribute": "view_name", "operator": "=", "value": name}
    )


def upsert_architecture_view(client: HmdLangDeploymentClient, payload: Dict) -> Dict:
    for key in ("view_name", "roots", "template_name"):
        if not payload.get(key):
            raise ServiceException(f"An architecture view needs {key}.")
    existing = _views_named(client, payload["view_name"])
    view = (
        existing[0]
        if existing
        else ArchitectureView(
            view_name=payload["view_name"],
            roots=payload["roots"],
            template_name=payload["template_name"],
        )
    )
    for field in _VIEW_FIELDS:
        if field in payload:
            setattr(view, field, payload[field])
    view = client.upsert(view)
    return _to_dict(view, _VIEW_FIELDS)


def list_architecture_views(client: HmdLangDeploymentClient) -> List[Dict]:
    views = client.search_architecture_view_hmd_lang_deployment({})
    return sorted(
        (_to_dict(v, _VIEW_FIELDS) for v in views), key=lambda r: r["view_name"]
    )


def get_architecture_view(
    client: HmdLangDeploymentClient, name: str
) -> ArchitectureView:
    views = _views_named(client, name)
    if not views:
        raise ServiceException(f"Architecture view {name} not found.")
    return views[0]


# --------------------------------------------------------------------------- #
# The architecture model (SPEC001, SPEC006)
# --------------------------------------------------------------------------- #


def _definition_ref(declaration: Dict) -> str:
    return (
        f"{declaration.get('resource_namespace')}/"
        f"{declaration.get('resource_definition_name')}@{declaration.get('version')}"
    )


class ModelBuilder:
    """Builds the class-level model for a scope from the registry."""

    def __init__(self, client: HmdLangDeploymentClient):
        self.client = client
        self.class_information = ClassInformation(client)
        self.resource_information = ResourceInformation(client)
        self.rs = build_relationship_support(client._base_client)
        self.nodes: Dict[str, Dict] = {}
        self.edges: Dict[Tuple, Dict] = {}
        self.findings: List[Dict] = []
        self._latest: Dict[str, Optional[RepoClassVersion]] = {}
        self._roles: Dict[str, Dict[str, Optional[str]]] = {}

    # -- lookups ----------------------------------------------------------- #

    def latest_version(self, repo_class_name: str) -> Optional[RepoClassVersion]:
        if repo_class_name not in self._latest:
            try:
                rc = self.class_information.get_repo_class(repo_class_name)
            except ServiceException:
                self._latest[repo_class_name] = None
                return None
            versions = [
                self.rs.ref_to(rel)
                for rel in self.client.get_from_repo_class_has_repo_class_version_hmd_lang_deployment(
                    rc
                )
            ]
            versions = sort_versions(
                [v for v in versions if v is not None], lambda v: v.version
            )
            self._latest[repo_class_name] = versions[0] if versions else None
        return self._latest[repo_class_name]

    def _class_of(self, rcv: RepoClassVersion) -> Optional[str]:
        for (
            rel
        ) in self.client.get_to_repo_class_has_repo_class_version_hmd_lang_deployment(
            rcv
        ):
            rc = self.rs.ref_from(rel)
            if rc is not None:
                return rc.repo_class_name
        return None

    # -- graph construction ----------------------------------------------- #

    def _finding(self, code: str, subject: str, message: str) -> None:
        self.findings.append(
            {
                "severity": "warning",
                "code": code,
                "subject": subject,
                "message": message,
            }
        )

    def _edge(self, src: str, dst: str, kind: str, **attrs) -> None:
        key = (src, dst, kind, attrs.get("role"))
        edge = {"from": src, "to": dst, "kind": kind}
        edge.update({k: v for k, v in attrs.items() if v is not None})
        self.edges[key] = edge

    def _add_class(self, name: str, rcv: Optional[RepoClassVersion]) -> str:
        node_id = f"rc:{name}"
        if node_id not in self.nodes:
            self.nodes[node_id] = {
                "id": node_id,
                "kind": "repo_class",
                "label": name,
                "repo_class": name,
                "version": rcv.version if rcv else None,
                "group": None,
                "attributes": {},
            }
            if rcv is None:
                self._finding(
                    "unregistered-class",
                    name,
                    f"{name} is not registered, so its resources are unknown.",
                )
            else:
                self._add_resources(name, rcv)
        return node_id

    def _add_resources(self, name: str, rcv: RepoClassVersion) -> None:
        for decl_name, declaration in sorted((rcv.resources or {}).items()):
            node_id = f"res:{name}/{decl_name}"
            namespace = declaration.get("resource_namespace", "")
            self.nodes[node_id] = {
                "id": node_id,
                "kind": "resource",
                "label": decl_name,
                "resource_definition": _definition_ref(declaration),
                "provider": namespace.split(".")[0] if namespace else None,
                "group": f"rc:{name}",
                "attributes": {
                    k: v
                    for k, v in declaration.items()
                    if k in ("description", "role", "metadata")
                },
            }
            produces = declaration.get("produces", True)
            self._edge(f"rc:{name}", node_id, "produces" if produces else "consumes")

    def _dependencies(
        self, name: str, rcv: RepoClassVersion
    ) -> List[Tuple[str, str, bool, str]]:
        """``(role, target class, required, status)`` for each role of ``rcv``.

        A resource requirement is authoritative; its producers are looked up
        through the definition's subtypes. One producer resolves the role, the
        class the manifest also names wins among several, and otherwise every
        producer is a candidate. A role naming only a class resolves to it.
        """
        by_role: Dict[str, Dict] = {}
        for (
            rel
        ) in self.client.get_from_repo_class_version_req_repo_class_hmd_lang_deployment(
            rcv
        ):
            target = self.rs.ref_to(rel)
            if target is None:
                continue
            by_role.setdefault(rel.role, {})["class"] = target.repo_class_name
            by_role[rel.role]["required"] = str(rel.required).lower() != "false"
        for rel in self.client.get_from_repo_class_version_req_resource_definition_hmd_lang_deployment(
            rcv
        ):
            rd = self.rs.ref_to(rel)
            if rd is None:
                continue
            entry = by_role.setdefault(rel.role, {})
            entry["required"] = str(rel.required).lower() != "false"
            producers = {
                self._class_of(p)
                for p in self.resource_information.get_producers(
                    rd, include_subtypes=True
                )
            }
            entry["producers"] = sorted(p for p in producers if p and p != name)

        out = []
        for role, entry in sorted(by_role.items()):
            named = entry.get("class")
            producers = entry.get("producers")
            required = entry.get("required", True)
            if producers is None:
                out.append((role, named, required, "resolved"))
            elif len(producers) == 1:
                out.append((role, producers[0], required, "resolved"))
            elif named and named in producers:
                out.append((role, named, required, "resolved"))
            elif producers:
                self._finding(
                    "ambiguous-role",
                    name,
                    f"{name}'s {role} could be filled by {', '.join(producers)}.",
                )
                out.extend((role, p, required, "candidate") for p in producers)
            elif named:
                out.append((role, named, required, "resolved"))
            else:
                self._finding(
                    "unresolved-role",
                    name,
                    f"Nothing registered produces {name}'s {role}.",
                )
        return out

    def _flows(self, name: str, rcv: RepoClassVersion) -> None:
        """``flows_to`` edges from each declaration's ``metadata.connects_to``."""
        roles = self._roles.get(name, {})
        for decl_name, declaration in sorted((rcv.resources or {}).items()):
            src = f"res:{name}/{decl_name}"
            for target in (declaration.get("metadata") or {}).get("connects_to") or []:
                if target.startswith("dep:"):
                    role = target[len("dep:") :]
                    dst = self._role_target(name, role, roles.get(role))
                else:
                    dst = f"res:{name}/{target}"
                    if dst not in self.nodes:
                        self._finding(
                            "unknown-flow-target",
                            name,
                            f"{name}/{decl_name} connects to {target}, which it does not declare.",
                        )
                        dst = None
                if dst:
                    self._edge(src, dst, "flows_to")

    def _role_target(
        self, name: str, role: str, target_class: Optional[str]
    ) -> Optional[str]:
        """The node a ``dep:<role>`` flow lands on: the filling class's one
        produced resource when it declares exactly one, else the class itself."""
        if target_class is None:
            self._finding(
                "unknown-flow-role",
                name,
                f"{name} connects to dep:{role}, but the role is outside the scope or unfilled.",
            )
            return None
        produced = [
            node_id
            for node_id, node in self.nodes.items()
            if node.get("group") == f"rc:{target_class}"
            and node["kind"] == "resource"
            and (f"rc:{target_class}", node_id, "produces", None) in self.edges
        ]
        if len(produced) == 1:
            return produced[0]
        return f"rc:{target_class}"

    def build(
        self,
        roots: List[str],
        depth: Optional[int] = 1,
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
    ) -> Dict:
        if not roots:
            raise ServiceException("An architecture model needs at least one root.")
        excluded = set(exclude or [])
        max_depth = 1 if depth is None else int(depth)

        queue = deque((root, 0) for root in list(roots) + list(include or []))
        seen = set()
        visited: Dict[str, RepoClassVersion] = {}
        while queue:
            name, hops = queue.popleft()
            if name in seen or name in excluded:
                continue
            seen.add(name)
            rcv = self.latest_version(name)
            self._add_class(name, rcv)
            if rcv is None:
                continue
            visited[name] = rcv
            roles = self._roles.setdefault(name, {})
            for role, target, required, status in self._dependencies(name, rcv):
                if target is None or target in excluded:
                    continue
                if hops >= max_depth:
                    continue
                self._add_class(target, self.latest_version(target))
                self._edge(
                    f"rc:{name}",
                    f"rc:{target}",
                    "depends_on",
                    role=role,
                    required=required,
                    status=status,
                )
                if status == "resolved":
                    roles[role] = target
                queue.append((target, hops + 1))

        for name, rcv in sorted(visited.items()):
            self._flows(name, rcv)

        return {
            "schema": MODEL_SCHEMA,
            "scope": {
                "roots": list(roots),
                "depth": max_depth,
                "include": list(include or []),
                "exclude": sorted(excluded),
                "source": "control-plane",
                "environment": None,
            },
            "nodes": [self.nodes[k] for k in sorted(self.nodes)],
            "edges": [
                self.edges[k]
                for k in sorted(
                    self.edges, key=lambda k: (k[0], k[1], k[2], k[3] or "")
                )
            ],
            "findings": self.findings,
        }


def build_architecture_model(client: HmdLangDeploymentClient, payload: Dict) -> Dict:
    if payload.get("environment"):
        # The instance overlay is a later slice of SPEC006; say so rather than
        # quietly returning a class-level model for an environment request.
        raise ServiceException(
            "Environment-scoped architecture models are not implemented yet; "
            "omit environment for the class-level model."
        )
    return ModelBuilder(client).build(
        roots=payload.get("roots") or [],
        depth=payload.get("depth", 1),
        include=payload.get("include"),
        exclude=payload.get("exclude"),
    )
