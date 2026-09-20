"""Instance configuration resolution.

Builds the configuration dictionary a RepoInstance is deployed with: the class
default configuration deep-updated with the instance's own configuration, plus
one entry per dependency role carrying the dependency's name, version,
deployment id and -- NERD0006 -- its produced Resource outputs (baked when the
dependency is already deployed, or a ``hmd_resource_ref`` pointer when it is
still DEPLOY_NEXT).

This used to be half of ``deploy_base.DeployBase``; the DAG/script-generation
half stays with the orchestrator, which subclasses :class:`InstanceConfigResolver`.
"""

import logging
from copy import deepcopy
from typing import Dict, List, Set

from hmd_cli_tools.hmd_cli_tools import deep_update
from hmd_graphql_client.relationship_support import RelationshipSupport
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_lang_deployment.repo_class import RepoClass
from hmd_lang_deployment.repo_class_version import RepoClassVersion
from hmd_lang_deployment.repo_instance import RepoInstance
from hmd_lang_deployment.repo_instance_req_repo_instance import (
    RepoInstanceReqRepoInstance,
)
from hmd_ms_deployment_core import DEPLOY_NEXT, DEPLOYED, FAILED
from hmd_ms_deployment_core.resource_information import ResourceInformation

logger = logging.getLogger(f"HMD.{__name__}")


class InstanceConfigResolver:
    def __init__(self, client: HmdLangDeploymentClient, rs: RelationshipSupport = None):
        self.client = client
        self.rs = rs if rs is not None else RelationshipSupport([client._base_client])
        self._instance_config_cache = {}

    def get_instance_config(
        self,
        instance: RepoInstance,
        destroy: bool,
        include_depenency_details: bool = True,
    ):
        cache_key = (instance.identifier, destroy, include_depenency_details)
        if cache_key in self._instance_config_cache:
            return self._instance_config_cache[cache_key]

        logger.debug(
            f"Pulling configuration for RepoInstance, {instance.name}, destroy, {destroy}"
        )
        repo_classes = (
            instance.get_from_repo_instance_isa_repo_class_hmd_lang_deployment()
        )
        logger.info(
            f"Repo classes for instance {instance.name} ({instance.identifier}): {repo_classes}"
        )

        # If repo_classes is not cached, query the database
        if not repo_classes:
            logger.debug(f"RepoClass not cached for {instance.name}, querying database")
            repo_classes = (
                self.client.get_from_repo_instance_isa_repo_class_hmd_lang_deployment(
                    instance
                )
            )
            logger.info(f"Repo classes from database: {repo_classes}")

        if not repo_classes:
            logger.error(
                f"RepoInstance {instance.name} ({instance.identifier}) has no associated RepoClass. "
                f"Cannot retrieve configuration."
            )
            self._instance_config_cache[cache_key] = {}
            return {}

        instance_class: RepoClass = self.rs.ref_to(repo_classes[0])
        if not destroy:
            instance_deployment = self._get_next_or_deployed(instance)
        else:
            instance_deployment = self._get_current_rid(instance)

        if not instance_deployment:
            logger.debug(f"No RepoInstanceDeployment for RepoInstance, {instance.name}")
            self._instance_config_cache[cache_key] = {}
            return {}

        logger.debug(
            f"RepoInstanceDeployment: id:{instance_deployment.identifier}, status: {instance_deployment.status}, "
            f"end: {instance_deployment.end}"
        )
        # Query the client for the RCV relationship rather than relying on
        # entity-cached relationships, which may be stale when multiple
        # RelationshipSupport instances are in play.
        rcv_rels = self.client.get_from_repo_instance_deployment_has_repo_class_version_hmd_lang_deployment(
            instance_deployment
        )
        self.rs.pull_relationship_nouns(rcv_rels)
        version: RepoClassVersion = self.rs.ref_to(rcv_rels[0])
        config = dict()
        config["dependencies"] = {}
        # config["instance_configuration"] = instance_deployment.instance_configuration
        config["instance_name"] = instance.name
        config["version"] = version.version
        config["deployment_id"] = instance_deployment.deployment_id
        if instance_deployment.hmd_region:
            config["hmd_region"] = instance_deployment.hmd_region
        config["repo_name"] = instance_class.repo_class_name

        if include_depenency_details:
            default_config = (
                deepcopy(version.default_configuration)
                if version.default_configuration
                else {}
            )

            if instance_deployment.instance_configuration:
                deep_update(default_config, instance_deployment.instance_configuration)

            config.update(default_config)

        dependencies: List[
            RepoInstanceReqRepoInstance
        ] = instance.get_from_repo_instance_req_repo_instance_hmd_lang_deployment()
        # If dependencies not cached on entity, query the database
        if not dependencies:
            dependencies = self.client.get_from_repo_instance_req_repo_instance_hmd_lang_deployment(
                instance
            )
            self.rs.pull_relationship_nouns(dependencies)
        for dep in dependencies:
            dep_instance = self.rs.ref_to(dep)
            role_cfg = self.get_instance_config(dep_instance, destroy, False)
            # NERD0006: attach the dependency's produced Resource outputs (baked when
            # already deployed, or a runtime pointer when deployed in this ChangeSet).
            self._attach_dependency_resources(role_cfg, dep_instance, destroy)
            if dep.role not in config["dependencies"]:
                config["dependencies"][dep.role] = role_cfg
            else:
                if not isinstance(config["dependencies"][dep.role], list):
                    new_list = list()
                    new_list.append(config["dependencies"][dep.role])
                    config["dependencies"][dep.role] = new_list
                config["dependencies"][dep.role].append(role_cfg)
        self._instance_config_cache[cache_key] = config
        return config

    def _attach_dependency_resources(
        self, role_cfg: Dict, dep_instance: RepoInstance, destroy: bool
    ) -> None:
        """Attach a dependency's produced Resource outputs to its role config (NERD0006).

        For an already-deployed dependency the outputs exist and are stable (the
        deployment mutex serializes deploys), so they are **baked** into
        ``hmd_resources`` now. For a dependency being deployed in this same ChangeSet
        the outputs do not exist yet, so only a runtime pointer ``hmd_resource_ref``
        (the dependency's not-yet-deployed RepoInstanceDeployment identifier) is
        stamped; ``hmd deploy`` resolves it to ``hmd_resources`` just before the deploy
        command runs. Both keys carry the ``hmd_`` prefix so they cannot collide with
        user-supplied configuration. Best-effort: never raises into config generation.
        """
        if not isinstance(role_cfg, dict):
            return
        # Idempotent: role_cfg objects are cached and shared across parents.
        if "hmd_resources" in role_cfg or "hmd_resource_ref" in role_cfg:
            return
        try:
            dep_rid = (
                self._get_current_rid(dep_instance)
                if destroy
                else self._get_next_or_deployed(dep_instance)
            )
            if dep_rid is None:
                return
            if dep_rid.status == DEPLOY_NEXT:
                # Pending in this ChangeSet -- outputs don't exist yet; resolve at deploy time.
                role_cfg["hmd_resource_ref"] = {
                    "repo_instance_deployment_id": dep_rid.identifier
                }
            else:
                # Already deployed -- bake the current outputs into the config now.
                role_cfg["hmd_resources"] = ResourceInformation(
                    self.client
                ).serialize_resources_for_deployment(dep_rid)
        except Exception as e:
            logger.warning(
                f"Could not attach resource outputs for dependency "
                f"{getattr(dep_instance, 'name', '?')}: {e}"
            )

    def _get_next_or_deployed(self, instance: RepoInstance):
        result = None
        rels = (
            instance.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment()
        )
        # If relationships not cached on entity, query the database
        if not rels:
            rels = self.client.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment(
                instance
            )
            self.rs.pull_relationship_nouns(rels)

        for rel in rels:
            if self.rs.ref_to(rel).status == DEPLOY_NEXT:
                result = self.rs.ref_to(rel)
                break

        if not result:
            for rel in rels:
                if rel.current.lower() == "true" and self.rs.ref_to(rel).status in [
                    DEPLOYED,
                    FAILED,
                ]:
                    result = self.rs.ref_to(rel)
                    break

        return result

    def _get_current_rid(self, instance):
        result = None
        rels = (
            instance.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment()
        )
        # If relationships not cached on entity, query the database
        if not rels:
            rels = self.client.get_from_repo_instance_has_repo_instance_deployment_hmd_lang_deployment(
                instance
            )
            self.rs.pull_relationship_nouns(rels)

        for rel in rels:
            if rel.current.lower() == "true":
                result = self.rs.ref_to(rel)
                break

        return result


def dependency_instance_names(config, names: Set[str]) -> Set[str]:
    """Every ``instance_name`` reachable through ``dependencies`` in a resolved config."""
    configs = config if isinstance(config, list) else [config]
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        if "instance_name" in cfg:
            names.add(cfg["instance_name"])
        for sub in cfg.get("dependencies", {}).values():
            dependency_instance_names(sub, names)
    return names


def with_details(
    resolver: InstanceConfigResolver, instance: RepoInstance, config: Dict, lookup
) -> Dict:
    """``config`` plus the ``details`` map a deploy expects.

    ``details`` carries, for every instance named anywhere in the dependency
    tree, that instance's own resolved configuration minus its ``dependencies``
    -- what helm charts read as ``.Values.details.<instance>``. This is what the
    orchestrator's ``InstanceConfigCollector`` produces for an Argo workflow, so
    a client that deploys from ``get_deployment_config`` sees the same values.
    ``lookup(name)`` resolves an instance name to a RepoInstance (or None).
    """
    result = dict(config)
    names: Set[str] = set()
    dependency_instance_names(config, names)
    names.discard(instance.name)
    details = {}
    for name in sorted(names):
        dep = lookup(name)
        if dep is None:
            continue
        dep_config = resolver.get_instance_config(dep, False)
        details[name] = {k: v for k, v in dep_config.items() if k != "dependencies"}
    result["details"] = details
    return result
