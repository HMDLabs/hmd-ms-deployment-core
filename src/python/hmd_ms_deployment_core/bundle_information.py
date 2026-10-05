"""Bundle registry (NERD0010).

A Bundle is a versioned, declarative group of member roles -- the same
``repo_class_name`` / ``resource`` / ``required`` / ``version_spec`` vocabulary a
RepoClassVersion already uses for its dependencies -- plus a configuration
schema. NeuronSphere's core Bundles are declared together in one repo class,
``hmd-bundle-core``, as ``meta-data/bundles/<bundle_name>.json``; each bundle
version is the version of that repo class which shipped it.

The full role declaration is stored on ``bundle_version.members`` (read whole by
resolution); the ``bundle_version_req_*`` edges are written alongside so the
graph can answer "which bundles use this class / resource".
"""

import copy
import logging
from typing import Dict, List, Optional, Tuple

from hmd_base_service.exceptions import ServiceException
from hmd_lang_deployment.bundle import Bundle
from hmd_lang_deployment.bundle_has_bundle_version import BundleHasBundleVersion
from hmd_lang_deployment.bundle_version import BundleVersion
from hmd_lang_deployment.bundle_version_req_repo_class import (
    BundleVersionReqRepoClass,
)
from hmd_lang_deployment.bundle_version_req_resource_definition import (
    BundleVersionReqResourceDefinition,
)
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient

from .class_information import ClassInformation, DependencyEdges
from .version import VersionSpecifier, sort_versions

logger = logging.getLogger(__name__)

BUNDLE_VERSION_EDGES = DependencyEdges(
    BundleVersionReqRepoClass,
    BundleVersionReqResourceDefinition,
    lambda client, bv: client.get_from_bundle_version_req_repo_class_hmd_lang_deployment(
        bv
    ),
    lambda client, bv: client.get_from_bundle_version_req_resource_definition_hmd_lang_deployment(
        bv
    ),
)

# Attributes compared to decide whether a re-POST of an existing version is
# identical (idempotent) or a content change (rejected).
_CONTENT_ATTRIBUTES = (
    "config_schema",
    "default_configuration",
    "members",
    "discovery",
    "source_repo_class_name",
    "source_repo_class_version",
)


class BundleInformation:
    def __init__(self, client: HmdLangDeploymentClient):
        self.client = client
        self.class_information = ClassInformation(client)
        self.rs = self.class_information.relationship_support

    def get_bundle(self, bundle_name: str) -> Bundle:
        bundles = self.client.search_bundle_hmd_lang_deployment(
            {"attribute": "bundle_name", "operator": "=", "value": bundle_name}
        )
        if len(bundles) != 1:
            raise ServiceException(f"Bundle, {bundle_name}, not found.")
        return bundles[0]

    def get_bundle_versions(self, bundle: Bundle) -> List[BundleVersion]:
        return [
            self.rs.ref_to(rel)
            for rel in self.client.get_from_bundle_has_bundle_version_hmd_lang_deployment(
                bundle
            )
        ]

    def get_bundle_version(
        self,
        bundle_name: str,
        version: Optional[str] = None,
        version_spec: Optional[str] = None,
    ) -> BundleVersion:
        """Exact ``version`` if given, else the newest version satisfying
        ``version_spec``, else the newest version."""
        versions = self.get_bundle_versions(self.get_bundle(bundle_name))
        if version:
            matches = [bv for bv in versions if bv.version == version]
            if not matches:
                raise ServiceException(
                    f"No version found for bundle {bundle_name} : {version}."
                )
            return matches[0]

        if version_spec:
            spec = VersionSpecifier(version_spec)

            def _satisfies(bv) -> bool:
                try:
                    spec.validate(bv.version)
                    return True
                except Exception:
                    return False

            versions = [bv for bv in versions if _satisfies(bv)]
        if not versions:
            raise ServiceException(
                f"No version of bundle {bundle_name} satisfies {version_spec or 'any'}."
            )
        return sort_versions(versions, lambda bv: bv.version)[0]

    def add_bundle_version(
        self,
        payload: Dict,
        source_repo_class_name: Optional[str] = None,
        source_version: Optional[str] = None,
    ) -> Tuple[BundleVersion, str]:
        """Register one bundle declaration (the ``meta-data/bundles/*.json``
        shape). Returns ``(bundle_version, "created" | "unchanged")``.

        ``version`` defaults to ``source_version``, the version of the repo
        class that declared the bundle. Re-registering an existing version is a
        no-op when its content is identical and an error when it differs.
        """
        bundle_name = payload.get("bundle_name")
        if not bundle_name:
            raise ServiceException("A bundle declaration requires 'bundle_name'.")
        version = payload.get("version") or source_version
        if not version:
            raise ServiceException(
                f"Bundle, {bundle_name}, has no 'version' and no source version was given."
            )
        VersionSpecifier.validate_version_number(version)

        roles = payload.get("roles") or {}
        if not roles:
            raise ServiceException(f"Bundle, {bundle_name}, declares no roles.")
        for role, declaration in roles.items():
            if not declaration.get("repo_class_name") and not declaration.get(
                "resource"
            ):
                raise ServiceException(
                    f"Bundle, {bundle_name}, role {role}: neither 'repo_class_name' nor 'resource' provided."
                )

        candidate = BundleVersion(
            version=version,
            source_repo_class_name=payload.get("source_repo_class_name")
            or source_repo_class_name,
            source_repo_class_version=payload.get("source_repo_class_version")
            or (source_version if source_repo_class_name else None),
            config_schema=payload.get("config_schema"),
            default_configuration=payload.get("default_configuration"),
            members=copy.deepcopy(roles),
            discovery=payload.get("discovery"),
        )

        try:
            bundle = self.get_bundle(bundle_name)
        except ServiceException:
            bundle = Bundle(bundle_name=bundle_name)
            self.client.upsert(bundle)

        for existing in self.get_bundle_versions(bundle):
            if existing.version != version:
                continue
            if all(
                getattr(existing, a) == getattr(candidate, a)
                for a in _CONTENT_ATTRIBUTES
            ):
                return existing, "unchanged"
            raise ServiceException(
                f"Bundle, {bundle_name}, already has version {version} with different content."
            )

        self.client.upsert(candidate)
        self.client.upsert(
            BundleHasBundleVersion(
                ref_from=bundle.identifier, ref_to=candidate.identifier
            )
        )

        dependencies = {
            role: {
                k: v
                for k, v in declaration.items()
                if k in ("repo_class_name", "required", "version_spec", "resource")
            }
            for role, declaration in roles.items()
        }
        self.class_information._resolve_dependency_classes(dependencies)
        self.class_information._wire_dependencies(
            candidate, dependencies, edges=BUNDLE_VERSION_EDGES
        )
        return candidate, "created"

    def add_bundle_versions(
        self, source_repo_class_name: str, source_version: str, bundles: List[Dict]
    ) -> List[Dict]:
        """Register every bundle a repo class declares. Each bundle succeeds or
        fails on its own; the result reports each one."""
        results = []
        for payload in bundles:
            name = payload.get("bundle_name")
            try:
                bv, status = self.add_bundle_version(
                    payload, source_repo_class_name, source_version
                )
                results.append(
                    {"bundle_name": name, "version": bv.version, "status": status}
                )
            except Exception as ex:
                logger.warning("Bundle %s failed to register: %s", name, ex)
                results.append(
                    {
                        "bundle_name": name,
                        "version": payload.get("version") or source_version,
                        "status": "error",
                        "error": str(ex),
                    }
                )
        return results


def bundle_version_to_dict(bundle_name: str, bv: BundleVersion) -> Dict:
    return {
        "bundle_name": bundle_name,
        "version": bv.version,
        "identifier": bv.identifier,
        "source_repo_class_name": bv.source_repo_class_name,
        "source_repo_class_version": bv.source_repo_class_version,
        "config_schema": bv.config_schema,
        "default_configuration": bv.default_configuration,
        "roles": bv.members,
        "discovery": bv.discovery,
    }
