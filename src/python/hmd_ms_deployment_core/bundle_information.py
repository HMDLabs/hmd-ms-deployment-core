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

A bundle added to an environment is recorded by ``environment_has_bundle_version``
(bindings, declared instances, profiles and where it came from) and one
``repo_instance_member_of_bundle_version`` edge per bound role (NERD0019 SPEC0003),
so "which bundle put this instance here" is a graph question, not a local file's.
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
from hmd_lang_deployment.environment import Environment
from hmd_lang_deployment.environment_has_bundle_version import (
    EnvironmentHasBundleVersion,
)
from hmd_lang_deployment.repo_instance import RepoInstance
from hmd_lang_deployment.repo_instance_member_of_bundle_version import (
    RepoInstanceMemberOfBundleVersion,
)
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient

from .class_information import ClassInformation, DependencyEdges
from .version import VersionSpecifier, sort_versions

logger = logging.getLogger(__name__)

BUNDLE_VERSION_EDGES = DependencyEdges(
    BundleVersionReqRepoClass,
    BundleVersionReqResourceDefinition,
    lambda client, bv: (
        client.get_from_bundle_version_req_repo_class_hmd_lang_deployment(bv)
    ),
    lambda client, bv: (
        client.get_from_bundle_version_req_resource_definition_hmd_lang_deployment(bv)
    ),
)

# Attributes compared to decide whether a re-POST of an existing version is
# identical (idempotent) or a content change (rejected).
_CONTENT_ATTRIBUTES = (
    "config_schema",
    "default_configuration",
    "members",
    "discovery",
    "local",
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

    def list_bundle_versions(self, bundle_name: str) -> List[Dict]:
        """Every version of a bundle as full rows, newest first."""
        versions = sort_versions(
            self.get_bundle_versions(self.get_bundle(bundle_name)),
            lambda bv: bv.version,
        )
        return [bundle_version_to_dict(bundle_name, bv) for bv in versions]

    def list_bundles(self) -> List[Dict]:
        """The catalogue: one summary row per bundle, by name."""
        rows = []
        for bundle in sorted(
            self.client.search_bundle_hmd_lang_deployment({}),
            key=lambda b: b.bundle_name,
        ):
            versions = sort_versions(
                self.get_bundle_versions(bundle), lambda bv: bv.version
            )
            newest = versions[0] if versions else None
            rows.append(
                {
                    "bundle_name": bundle.bundle_name,
                    "versions": [bv.version for bv in versions],
                    "latest_version": newest.version if newest else None,
                    "summary": ((newest.discovery or {}).get("summary"))
                    if newest
                    else None,
                }
            )
        return rows

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
            local=payload.get("local"),
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

    # --- membership in an environment (NERD0019 SPEC0003) -------------------

    def bundle_name_of(self, bv: BundleVersion) -> str:
        rels = self.client.get_to_bundle_has_bundle_version_hmd_lang_deployment(bv)
        return self.rs.ref_from(rels[0]).bundle_name

    def _environment_instances(
        self, environment: Environment
    ) -> Dict[str, RepoInstance]:
        rels = self.client.get_from_environment_has_repo_instance_hmd_lang_deployment(
            environment
        )
        self.rs.pull_relationship_nouns(rels)
        instances = (self.rs.ref_to(rel) for rel in rels)
        return {ri.name: ri for ri in instances}

    def _attached(
        self, environment: Environment
    ) -> List[Tuple[EnvironmentHasBundleVersion, BundleVersion, str]]:
        rels = self.client.get_from_environment_has_bundle_version_hmd_lang_deployment(
            environment
        )
        self.rs.pull_relationship_nouns(rels)
        out = []
        for rel in rels:
            bv = self.rs.ref_to(rel)
            out.append((rel, bv, self.bundle_name_of(bv)))
        return out

    def _remove_attachment(
        self,
        rel: EnvironmentHasBundleVersion,
        bv: BundleVersion,
        instances: Dict[str, RepoInstance],
    ) -> None:
        ids = {ri.identifier for ri in instances.values()}
        for member in self.client.get_to_repo_instance_member_of_bundle_version_hmd_lang_deployment(
            bv
        ):
            ref_from = member.ref_from
            if (ref_from if isinstance(ref_from, str) else ref_from.identifier) in ids:
                self.client.delete(member)
        self.client.delete(rel)

    def attach_bundle_to_environment(
        self,
        environment: Environment,
        bundle_name: str,
        version: Optional[str] = None,
        bindings: Optional[Dict[str, str]] = None,
        declared: Optional[List[str]] = None,
        profiles: Optional[List[str]] = None,
        release_version: Optional[str] = None,
        source_ref: Optional[str] = None,
        digest: Optional[str] = None,
    ) -> Dict:
        """Record that a bundle is in an environment. ``bindings`` maps each
        bound role to the instance filling it; ``declared`` names the instances
        the bundle provisioned (the rest it reused). Replaces any earlier
        record of the same bundle in the environment, whatever its version."""
        bv = self.get_bundle_version(bundle_name, version)
        bindings = dict(bindings or {})
        declared = list(declared or [])
        roles = bv.members or {}

        unknown = sorted(set(bindings) - set(roles))
        if unknown:
            raise ServiceException(
                f"Bundle, {bundle_name} {bv.version}, has no role, {', '.join(unknown)}."
            )
        instances = self._environment_instances(environment)
        missing = sorted({i for i in bindings.values() if i not in instances})
        if missing:
            raise ServiceException(
                f"Environment, {environment.type}, has no instance, {', '.join(missing)}."
            )
        unbound = sorted(set(declared) - set(bindings.values()))
        if unbound:
            raise ServiceException(
                f"Bundle, {bundle_name}, declares {', '.join(unbound)} but binds no role to it."
            )

        for rel, attached_bv, name in self._attached(environment):
            if name == bundle_name:
                self._remove_attachment(rel, attached_bv, instances)

        rel = EnvironmentHasBundleVersion(
            ref_from=environment.identifier,
            ref_to=bv.identifier,
            bindings=bindings,
            declared=declared,
            profiles=list(profiles or []),
            release_version=release_version,
            source_ref=source_ref,
            digest=digest,
        )
        self.client.upsert(rel)
        for role, instance_name in sorted(bindings.items()):
            self.client.upsert(
                RepoInstanceMemberOfBundleVersion(
                    ref_from=instances[instance_name].identifier,
                    ref_to=bv.identifier,
                    role=role,
                    provisioned="true" if instance_name in declared else "false",
                )
            )
        return attachment_to_dict(bundle_name, bv, rel)

    def detach_bundle_from_environment(
        self, environment: Environment, bundle_name: str
    ) -> Dict:
        """Remove a bundle's record from an environment, returning it -- its
        ``declared`` list is what the caller may now undeclare. Deploys and
        destroys nothing."""
        for rel, bv, name in self._attached(environment):
            if name == bundle_name:
                row = attachment_to_dict(bundle_name, bv, rel)
                self._remove_attachment(
                    rel, bv, self._environment_instances(environment)
                )
                return row
        raise ServiceException(
            f"Bundle, {bundle_name}, is not attached to environment, {environment.type}."
        )

    def list_environment_bundles(self, environment: Environment) -> List[Dict]:
        rows = [
            attachment_to_dict(name, bv, rel)
            for rel, bv, name in self._attached(environment)
        ]
        return sorted(rows, key=lambda r: r["bundle_name"])

    def bundles_of_instance(
        self, environment: Environment, instance_name: str
    ) -> List[Dict]:
        """The bundles an instance belongs to, and the role it fills in each."""
        instance = self._environment_instances(environment).get(instance_name)
        if instance is None:
            raise ServiceException(
                f"Environment, {environment.type}, has no instance, {instance_name}."
            )
        rels = self.client.get_from_repo_instance_member_of_bundle_version_hmd_lang_deployment(
            instance
        )
        self.rs.pull_relationship_nouns(rels)
        rows = []
        for rel in rels:
            bv = self.rs.ref_to(rel)
            rows.append(
                {
                    "bundle_name": self.bundle_name_of(bv),
                    "version": bv.version,
                    "role": rel.role,
                    "provisioned": rel.provisioned,
                }
            )
        return sorted(rows, key=lambda r: (r["bundle_name"], r["role"]))


def attachment_to_dict(
    bundle_name: str, bv: BundleVersion, rel: EnvironmentHasBundleVersion
) -> Dict:
    return {
        "bundle_name": bundle_name,
        "version": bv.version,
        "bindings": rel.bindings or {},
        "declared": rel.declared or [],
        "profiles": rel.profiles or [],
        "release_version": rel.release_version,
        "source_ref": rel.source_ref,
        "digest": rel.digest,
    }


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
        "local": bv.local,
    }
