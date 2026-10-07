"""Environment bill of materials in dependency order.

The orchestrator builds a ``DeploymentDag`` (with transitive reduction, per-node
scripts and so on) to run a ChangeSet. Reading an environment back needs none of
that: the BOM is the environment's non-destroyed RepoInstances, each serialised
with its current deployment, listed so that every instance comes after the
instances it depends on. That is a topological sort over the
``RepoInstanceReqRepoInstance`` edges -- Kahn's algorithm, nothing more.

The node shape is unchanged from the former ``DeployBomCreator``; nsctl
(``internal/msdeploy/bom.go``) and the GUI parse it.
"""

import logging
from collections import OrderedDict, defaultdict, deque
from typing import Dict, List, Set

from hmd_base_service.exceptions import ServiceException
from hmd_graphql_client.relationship_support import RelationshipSupport
from hmd_lang_deployment.repo_class import RepoClass
from hmd_lang_deployment.repo_class_version import RepoClassVersion
from hmd_lang_deployment.repo_instance import RepoInstance
from hmd_lang_deployment.repo_instance_deployment import RepoInstanceDeployment
from hmd_lang_deployment.repo_instance_req_repo_instance import (
    RepoInstanceReqRepoInstance,
)
from hmd_ms_deployment_core import DEPLOYED, DEPLOY_NEXT, DESTROYED, FAILED
from hmd_ms_deployment_core.environment_information import (
    EnvironmentInformation,
    get_current_repo_instance_deployment,
)

logger = logging.getLogger(f"HMD.{__name__}")

NO_DATA_MESSAGE = "No data found when creating the deployment dag."
NO_ROOTS_MESSAGE = "No independent nodes in graph."


def _identifier(ref) -> str:
    return ref if isinstance(ref, str) else ref.identifier


def active_repo_instances(env_info: EnvironmentInformation) -> List[RepoInstance]:
    """Every RepoInstance of the environment whose current deployment is not DESTROYED."""
    env_info.load_environment()
    rs = env_info.rel_support
    result = []
    for ri in env_info.nouns.get(RepoInstance, {}).values():
        ri_rid = get_current_repo_instance_deployment(ri, rs)
        if not ri_rid or rs.ref_to(ri_rid).status != DESTROYED:
            result.append(ri)
    # Deterministic output regardless of dict/set iteration order.
    result.sort(key=lambda ri: ri.name)
    return result


def order_repo_instances(
    repo_instances: List[RepoInstance], rs: RelationshipSupport
) -> List[RepoInstance]:
    """Kahn's algorithm over ``RepoInstanceReqRepoInstance`` edges restricted to ``repo_instances``.

    Dependencies come before their dependents. Raises the same
    ``ServiceException`` messages the DAG builder raised for an empty set and a
    graph with no independent node, and a cycle error naming the instances left.
    """
    if not repo_instances:
        raise ServiceException(NO_DATA_MESSAGE)

    ids: Set[str] = {ri.identifier for ri in repo_instances}
    by_id: Dict[str, RepoInstance] = {ri.identifier: ri for ri in repo_instances}
    remaining: Dict[str, int] = {ri.identifier: 0 for ri in repo_instances}
    dependents: Dict[str, List[str]] = defaultdict(list)

    for ri in repo_instances:
        for rel in ri.get_from_repo_instance_req_repo_instance_hmd_lang_deployment():
            dep_id = _identifier(rel.ref_to)
            if dep_id in ids:
                remaining[ri.identifier] += 1
                dependents[dep_id].append(ri.identifier)

    if not any(dependents[i] == [] for i in ids):
        raise ServiceException(NO_ROOTS_MESSAGE)

    queue = deque(
        ri.identifier for ri in repo_instances if remaining[ri.identifier] == 0
    )
    ordered: List[RepoInstance] = []
    while queue:
        current = queue.popleft()
        ordered.append(by_id[current])
        for parent in dependents[current]:
            remaining[parent] -= 1
            if remaining[parent] == 0:
                queue.append(parent)

    if len(ordered) != len(repo_instances):
        stuck = sorted(by_id[i].name for i, n in remaining.items() if n > 0)
        raise ServiceException(f"Graph is not acyclic: {', '.join(stuck)}")
    return ordered


def serialize_repo_instance(ri: RepoInstance, rs: RelationshipSupport) -> OrderedDict:
    """One BOM entry, or ``None`` when the instance has no DEPLOYED/FAILED current deployment."""
    rc: RepoClass = rs.ref_to(
        ri.get_from_repo_instance_isa_repo_class_hmd_lang_deployment()[0]
    )
    ri_rid = get_current_repo_instance_deployment(ri, rs)
    if not ri_rid or rs.ref_to(ri_rid).status not in [DEPLOYED, FAILED]:
        return None

    rid: RepoInstanceDeployment = rs.ref_to(ri_rid)
    rcv: RepoClassVersion = rs.ref_to(
        rid.get_from_repo_instance_deployment_has_repo_class_version_hmd_lang_deployment()[
            0
        ]
    )
    instance_config = rid.instance_configuration if rid.instance_configuration else {}

    data = [
        ("repo_instance_name", ri.name),
        ("repo_class_name", rc.repo_class_name),
        ("repo_class_version", rcv.version),
        ("deployment_id", rid.deployment_id),
    ]
    if rid.hmd_region:
        data.append(("hmd_region", rid.hmd_region))
    if ri.auto_deploy:
        data.append(("auto_deploy", ri.auto_deploy))
    data += [("status", rid.status)]
    if rid.image_only:
        data.append(("image_only", True))
        return OrderedDict(data)

    data += [("instance_configuration", instance_config)]
    data = OrderedDict(data)
    ri_ris: List[RepoInstanceReqRepoInstance] = (
        ri.get_from_repo_instance_req_repo_instance_hmd_lang_deployment()
    )
    dependency_dict = defaultdict(list)
    for ri_ri in ri_ris:
        dependency_dict[ri_ri.role].append(rs.ref_to(ri_ri).name)
    data["dependencies"] = OrderedDict(
        sorted(
            [
                (role, deps if len(deps) > 1 else deps[0])
                for role, deps in dependency_dict.items()
            ],
            key=lambda x: x[0],
        )
    )
    if rid.config_artifact_spec:
        data["config_artifact_spec"] = rid.config_artifact_spec
    return data


def build_environment_bom(env_info: EnvironmentInformation) -> List[OrderedDict]:
    """The environment's deployed instances in dependency order."""
    rs = env_info.rel_support
    ordered = order_repo_instances(active_repo_instances(env_info), rs)
    bom = []
    for ri in ordered:
        entry = serialize_repo_instance(ri, rs)
        if entry is not None:
            bom.append(entry)
    return bom


def build_environment_bom_or_empty(
    env_info: EnvironmentInformation,
) -> List[OrderedDict]:
    """As :func:`build_environment_bom`, but an environment with no instances is ``[]``."""
    try:
        return build_environment_bom(env_info)
    except ServiceException as e:
        if e.message != NO_DATA_MESSAGE:
            raise
        return []


def _next_or_current_rid(
    ri: RepoInstance, rs: RelationshipSupport
) -> RepoInstanceDeployment:
    """The DEPLOY_NEXT deployment if one is pending, else the current one, else ``None``."""
    rels = ri.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment()
    for rel in rels:
        if rs.ref_to(rel).status == DEPLOY_NEXT:
            return rs.ref_to(rel)
    for rel in rels:
        if rel.current == "true":
            return rs.ref_to(rel)
    return None


def find_class_instances(
    env_info: EnvironmentInformation, repo_class_name: str
) -> List[Dict]:
    """Every RepoInstance of ``repo_class_name`` in the environment with its pending-or-current deployment.

    Unlike the BOM this includes instances whose deployment is still DEPLOY_NEXT
    and names the deployment record (``deployment_identifier``), which is what a
    client needs to drive ``set_deployment_status``. Both ``instance_name`` and
    ``repo_instance_name`` are present so BOM-shaped and instance-shaped readers
    agree.
    """
    env_info.load_environment()
    rs = env_info.rel_support
    result = []
    for ri in env_info.nouns.get(RepoInstance, {}).values():
        rc: RepoClass = rs.ref_to(
            ri.get_from_repo_instance_isa_repo_class_hmd_lang_deployment()[0]
        )
        if rc.repo_class_name != repo_class_name:
            continue
        rid = _next_or_current_rid(ri, rs)
        if rid is None or rid.status == DESTROYED:
            continue
        rcv: RepoClassVersion = rs.ref_to(
            rid.get_from_repo_instance_deployment_has_repo_class_version_hmd_lang_deployment()[
                0
            ]
        )
        dependencies = defaultdict(list)
        for rel in ri.get_from_repo_instance_req_repo_instance_hmd_lang_deployment():
            dependencies[rel.role].append(rs.ref_to(rel).name)
        result.append(
            OrderedDict(
                [
                    ("instance_name", ri.name),
                    ("repo_instance_name", ri.name),
                    ("repo_class_name", rc.repo_class_name),
                    ("repo_class_version", rcv.version),
                    ("deployment_id", rid.deployment_id),
                    ("deployment_identifier", rid.identifier),
                    ("status", rid.status),
                    ("instance_configuration", rid.instance_configuration or {}),
                    (
                        "dependencies",
                        OrderedDict(
                            sorted(
                                (role, deps if len(deps) > 1 else deps[0])
                                for role, deps in dependencies.items()
                            )
                        ),
                    ),
                ]
            )
        )
    result.sort(key=lambda e: e["instance_name"])
    return result
