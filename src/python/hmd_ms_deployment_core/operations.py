"""Core operations: registry, resource catalogue, resolver and instance records.

Registered by ``hmd-ms-base`` through ``SERVICE_CONFIG.operations_modules``. The
orchestrator image loads this module *and* ``hmd_ms_deployment.deployment_ops``;
the core image loads only this one. Every ``rest_path`` here is unchanged from
the single-package service (NERD0015).
"""

import logging
from typing import Dict, List

from hmd_base_service.exceptions import ServiceException, HmdEntityNotFoundException
from hmd_graphql_client.hmd_db_engine_client import DbEngineClient
from hmd_graphql_client.relationship_support import RelationshipSupport
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_lang_deployment.repo_class_version_notes import RepoClassVersionNotes
from hmd_lang_deployment.repo_class_version import RepoClassVersion
from hmd_lang_deployment.repo_instance import RepoInstance
from hmd_schema_loader import DefaultLoader
from . import DEPLOYED, DEPLOY_NEXT, RID_STATUS_OPTIONS
from ._env_cache_hook import get_env_cache
from .bom import (
    build_environment_bom,
    build_environment_bom_or_empty,
    find_class_instances,
)
from ._artifact_presence_hook import get_artifact_presence
from .bundle_information import BundleInformation, bundle_version_to_dict
from .changeset_validation import validate_changes
from .class_information import ClassInformation
from .discovery_index import get_or_build_index, search_index
from .environment_information import EnvironmentInformation
from .instance_config import InstanceConfigResolver, with_details
from .release_information import ReleaseInformation, release_version_to_dict
from .resource_information import ResourceInformation, seed_base_catalog_best_effort
from .version import VersionSpecifier, sort_versions

LOGGER = logging.getLogger(f"HMD.{__name__}")


def _get_deploy_client(evt, ctx):
    # Lazy: hmd-ms-base is only present in the service image, not the unit-test venv.
    from hmd_ms_base.crud_operations import get_first_db_engine

    db = get_first_db_engine(evt, ctx)
    loader = ctx["loader"]  # type: DefaultLoader
    base_client = DbEngineClient(db, loader)
    deploy_client = HmdLangDeploymentClient(base_client)
    return deploy_client


def _resolve_rcv_targets(deploy_client, rcv_rels) -> List[RepoClassVersion]:
    """Resolve repo_class_has_repo_class_version targets, dropping stale refs."""
    rs = RelationshipSupport()
    rs.register_client(deploy_client._base_client)
    resolved: List[RepoClassVersion] = []
    for rel in rcv_rels:
        rcv = rs.ref_to(rel)
        if rcv is None:
            LOGGER.warning(
                "RepoClassVersion referenced by rel %s could not be resolved; skipping",
                getattr(rel, "identifier", "?"),
            )
            continue
        resolved.append(rcv)
    return resolved


def _query_params(evt) -> Dict[str, str]:
    """The request's query-string parameters.

    ``evt.get("query")`` only resolves on hmd-base-service builds that special-case
    the key; on older builds it falls through to the raw Lambda event, which carries
    query params under ``queryStringParameters`` and never ``query`` -- so every
    ``?filter=`` on every operation silently read as empty. Prefer the request
    itself, which is present on all REST paths (the GraphQL handler builds events
    with ``request=None``).
    """
    request = getattr(evt, "request", None)
    if request is not None and hasattr(request, "query_params"):
        return dict(request.query_params)
    return evt.get("query", {}) or {}


def get_valid_environment(
    deploy_client: HmdLangDeploymentClient, environment_type: str
):
    environment = deploy_client.search_environment_hmd_lang_deployment(
        {"attribute": "type", "operator": "=", "value": environment_type}
    )
    if not environment:
        raise ServiceException(f"Environment, {environment_type}, not found.")
    assert (
        len(environment) == 1
    ), f"Expected 1 environment with type, {environment_type}. Found {len(environment)}."

    return environment[0]


def setup(service):
    try:
        seed_base_catalog_best_effort(_get_deploy_client(evt={}, ctx=service.context))
    except Exception:
        LOGGER.exception(
            "Failed to build a deploy client for base ResourceDefinition catalog "
            "seeding at startup; continuing without it."
        )

    @service.operation(
        rest_path="/apiop/set_deployment_status/<args_id>/<args_status>",
        rest_methods=["POST"],
        args={"id": "string", "status": "string"},
    )
    def set_deployment_status(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        id_ = evt["args"]["id"]
        status = evt["args"]["status"]

        status_options = RID_STATUS_OPTIONS
        if status not in status_options:
            raise ServiceException(
                f"Status must be one of {', '.join(status_options)}, was {status}"
            )

        rid = deploy_client.get_repo_instance_deployment_hmd_lang_deployment(id_)
        if not rid:
            raise HmdEntityNotFoundException("RepoInstanceDeployment", id_)

        # Get the environment for the updated RID and alter the current deployed instance if necessary
        rel = deploy_client.get_to_repo_instance_has_repo_instance_deployment_hmd_lang_deployment(
            rid
        )[
            0
        ]
        ri = deploy_client.get_repo_instance_hmd_lang_deployment(rel.ref_from)
        rel = deploy_client.get_to_environment_has_repo_instance_hmd_lang_deployment(
            ri
        )[0]
        env = deploy_client.get_environment_hmd_lang_deployment(rel.ref_from)

        env_info = EnvironmentInformation(env, deploy_client)
        env_info.update_instance_deployment_status(rid, status)
        return {"message": f"Status updated to {status}"}

    @service.operation(
        rest_path="/apiop/add_repo_class_version",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def add_repo_class_version(evt, ctx):
        """Add a new RepoClassVersion to a RepoClass.

        The payload is of the form::

          {
            "repo_class_name": "name",
            "version": "1.2.3",
            "dependencies": {
              "dep1": {
                "repo_class_name": "a_name",
                "required": "true" | "false",
                "version_spec": "~= 0.1"
              },
              "dep2": {
                # NERD0004 SPEC0008: a role may declare a resource-type
                # dependency. When a "resource" is present it is authoritative
                # and "repo_class_name" (if given) degrades to a suggestion.
                "repo_class_name": "hmd-eks-cluster",   # optional suggestion
                "required": "true" | "false",
                "resource": {
                  "resource_namespace": "kubernetes",
                  "resource_definition_name": "kubernetes-cluster",
                  "version": "0.1.0",
                  "version_spec": "~= 0.1",             # optional
                  "tag_selector": "tier=prod,region=us-west-2"  # optional
                }
              }
            },
            "default_configuration": {
               ...
            },
            "version_notes": {
                "version_notes": "some notes",
                "requirment_identifiers": ["1","2"]
            },
            "discovery": {
                "summary": "...",
                "entry_points": [{"path": "...", "description": "..."}],
                "capabilities": [{"name": "...", "kind": "endpoint", "description": "..."}],
                "related_docs": [{"title": "...", "path": "..."}]
            }
          }

        :param evt: The event initiating the request.
        :param ctx: The request context.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]

        # use the VersionSpecifier to validate the version number is okay...
        VersionSpecifier.validate_version_number(payload["version"])

        class_information = ClassInformation(deploy_client)

        # Make sure the dependency data is good...
        dependencies = payload.get("dependencies", {})
        default_configuration = payload.get("default_configuration", {})
        discovery = payload.get("discovery", {})

        # find the specified repo-class
        repo_class_name = payload["repo_class_name"]

        version_notes = (
            None
            if "version_notes" not in payload
            else RepoClassVersionNotes(**payload["version_notes"])
        )

        class_information.add_repo_version_by_name(
            repo_class_name=repo_class_name,
            version=payload["version"],
            dependencies=dependencies,
            default_configuration=default_configuration,
            version_notes=version_notes,
            discovery=discovery,
        )

        # NERD0005: a new class version is a structural (topology) change, so the
        # cached topology of every environment may now be stale. Status is
        # unaffected. Targeted-invalidate rather than rebuild.
        get_env_cache().invalidate_all_topology()
        # NERD0013: the discovery catalog indexes each class's latest version.
        get_env_cache().invalidate_discovery_index()

    @service.operation(
        rest_path="/apiop/resync_repo_class_version_dependencies",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def resync_repo_class_version_dependencies(evt, ctx):
        """Repair an EXISTING RepoClassVersion's dependency edges from its
        current manifest ``deploy.dependencies`` block, without registering a
        new version.

        Registration (``add_repo_class_version``) can leave a version
        permanently short a role's edges if that role's wiring failed the
        first time (e.g. a transient error), since re-registering the same
        version number is rejected outright. This op re-runs the same
        role-wiring logic against the already-existing version to backfill
        any missing edges; it's idempotent, so calling it repeatedly -- or
        against a version that's already fully wired -- is safe.

        Payload is of the same form as ``add_repo_class_version``, minus
        ``default_configuration``/``version_notes``::

          {
            "repo_class_name": "name",
            "version": "1.2.3",
            "dependencies": { ... }
          }

        :param evt: The event initiating the request.
        :param ctx: The request context.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]

        class_information = ClassInformation(deploy_client)

        class_information.resync_repo_class_version_dependencies(
            repo_class_name=payload["repo_class_name"],
            version=payload["version"],
            dependencies=payload.get("dependencies", {}),
        )

        # NERD0005: this can add dependency edges to an existing version, so
        # the cached topology of every environment may now be stale.
        get_env_cache().invalidate_all_topology()
        get_env_cache().invalidate_discovery_index()

    @service.operation(
        rest_path="/apiop/upsert_resource_definition",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def upsert_resource_definition(evt, ctx):
        """Register (idempotently) a ResourceDefinition (NERD0004).

        The payload is of the form::

          {
            "resource_namespace": "aws",
            "resource_definition_name": "eks-cluster",
            "version": "0.1.0",
            "description": "An EKS cluster",
            "resource_metadata": { ... },
            "output_schema": { <JSON Schema> },
            "parent": {
                "resource_namespace": "kubernetes",
                "resource_definition_name": "kubernetes-cluster",
                "version": "0.1.0"
            },
            "repo_class_version_id": "<identifier>",
            "produced_by_repo_class_version_id": "<identifier>",
            "role": "primary"
          }

        The identity tuple is (resource_namespace, resource_definition_name,
        version); re-submitting the same tuple updates the existing definition.

        :param evt: The event initiating the request.
        :param ctx: The request context.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]

        VersionSpecifier.validate_version_number(payload["version"])

        resource_information = ResourceInformation(deploy_client)
        resource_definition = resource_information.upsert_resource_definition(
            resource_namespace=payload["resource_namespace"],
            resource_definition_name=payload["resource_definition_name"],
            version=payload["version"],
            description=payload.get("description"),
            resource_metadata=payload.get("resource_metadata"),
            output_schema=payload.get("output_schema"),
            parent_ref=payload.get("parent"),
            has_rcv_id=payload.get("repo_class_version_id"),
            produced_by_rcv_id=payload.get("produced_by_repo_class_version_id"),
            role=payload.get("role"),
        )
        return resource_definition.serialize()

    @service.operation(
        rest_path="/apiop/seed_base_resource_definitions",
        rest_methods=["POST"],
        args={},
    )
    def seed_base_resource_definitions(evt, ctx):
        """Seed the standard base ResourceDefinition catalog (NERD0004).

        Idempotently upserts the abstract, vendor-neutral supertypes bundled with
        the service (namespaced under ``*.neuronsphere.io``) so that per-repo
        concrete definitions can ``parent`` them. Takes no payload and is safe to
        call on every startup (e.g. from ``hmd ns up``).

        Returns the list of seeded ``{resource_namespace, resource_definition_name,
        version}`` identities.

        :param evt: The event initiating the request.
        :param ctx: The request context.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        return ResourceInformation(deploy_client).seed_base_resource_definitions()

    @service.operation(
        rest_path="/apiop/get_resource_definition/<args_id>",
        rest_methods=["GET"],
        args={"id": "string"},
    )
    def get_resource_definition(evt, ctx):
        """Fetch a single ResourceDefinition by its ``identifier``."""
        deploy_client = _get_deploy_client(evt, ctx)
        id_ = evt["args"]["id"]
        resource_definition = ResourceInformation(
            deploy_client
        ).get_resource_definition(id_)
        if not resource_definition:
            raise HmdEntityNotFoundException("ResourceDefinition", id_)
        return resource_definition.serialize()

    @service.operation(
        rest_path="/apiop/list_resource_definitions",
        rest_methods=["GET"],
        args={},
    )
    def list_resource_definitions(evt, ctx):
        """List ResourceDefinitions, optionally filtered by ``resource_namespace``.

        Results are sorted by (resource_namespace, resource_definition_name,
        version).
        """
        deploy_client = _get_deploy_client(evt, ctx)
        query_params = _query_params(evt)
        resource_namespace = query_params.get("resource_namespace")
        resource_definitions = ResourceInformation(
            deploy_client
        ).list_resource_definitions(resource_namespace=resource_namespace)
        return [rd.serialize() for rd in resource_definitions]

    @service.operation(
        rest_path="/apiop/declare_produces_resource_definition",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def declare_produces_resource_definition(evt, ctx):
        """Declare that a RepoClassVersion produces a ResourceDefinition.

        The payload is of the form::

          {
            "repo_class_version_id": "<identifier>",
            "resource_definition": {
                "resource_namespace": "aws",
                "resource_definition_name": "eks-cluster",
                "version": "0.1.0"
            },
            "role": "primary"
          }
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]

        rcv = deploy_client.get_repo_class_version_hmd_lang_deployment(
            payload["repo_class_version_id"]
        )
        if not rcv:
            raise HmdEntityNotFoundException(
                "RepoClassVersion", payload["repo_class_version_id"]
            )

        resource_information = ResourceInformation(deploy_client)
        def_ref = payload["resource_definition"]
        resource_definition = resource_information.find_resource_definition(
            def_ref["resource_namespace"],
            def_ref["resource_definition_name"],
            def_ref["version"],
        )
        if not resource_definition:
            raise ServiceException(f"ResourceDefinition {def_ref} not found.")

        resource_information.declare_produces(
            rcv, resource_definition, role=payload.get("role")
        )
        return {"message": "produces relationship recorded"}

    @service.operation(
        rest_path="/apiop/submit_resources",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def submit_resources(evt, ctx):
        """Record the concrete resources produced by a RepoInstanceDeployment.

        Intended for a deploy CLI to call at the end of a deployment. The payload
        is of the form::

          {
            "repo_instance_deployment_id": "<identifier>",
            "resources": [
              {
                "resource_name": "prod-eks",
                "resource_definition": {
                    "resource_namespace": "aws",
                    "resource_definition_name": "eks-cluster",
                    "version": "0.1.0"
                },
                "output": { "cluster_name": "prod-eks", "endpoint": "..." },
                "tags": [ {"key": "env", "value": "prod"} ]
              }
            ]
          }

        The RepoInstanceDeployment is resolved directly by ``identifier``.
        Submission is idempotent per (deployment, resource_name).
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]

        id_ = payload["repo_instance_deployment_id"]
        rid = deploy_client.get_repo_instance_deployment_hmd_lang_deployment(id_)
        if not rid:
            raise HmdEntityNotFoundException("RepoInstanceDeployment", id_)

        resources = ResourceInformation(deploy_client).submit_resources(
            rid, payload["resources"]
        )
        return [resource.serialize() for resource in resources]

    @service.operation(
        rest_path="/apiop/get_deployment_resources/<args_id>",
        rest_methods=["GET"],
        args={"id": "string"},
    )
    def get_deployment_resources(evt, ctx):
        """List the resources recorded for a RepoInstanceDeployment ``identifier``.

        Each entry carries ``resource_name``, ``resource_definition``, ``output`` and
        ``tags`` (the ``submit_resources`` shape) so a deploy CLI can match a produced
        resource by its Resource Definition and read its ``output`` (NERD0006).
        """
        deploy_client = _get_deploy_client(evt, ctx)
        id_ = evt["args"]["id"]
        rid = deploy_client.get_repo_instance_deployment_hmd_lang_deployment(id_)
        if not rid:
            raise HmdEntityNotFoundException("RepoInstanceDeployment", id_)
        return ResourceInformation(deploy_client).serialize_resources_for_deployment(
            rid
        )

    @service.operation(
        rest_path="/apiop/find_resources_by_tag/<args_key>/<args_value>",
        rest_methods=["GET"],
        args={"key": "string", "value": "string"},
    )
    def find_resources_by_tag(evt, ctx):
        """Find resources carrying a searchable ``key``=``value`` tag.

        Pass ``?environment=<type>`` to scope results to resources deployed in that
        environment (the whole graph otherwise).
        """
        deploy_client = _get_deploy_client(evt, ctx)
        query_params = _query_params(evt)
        environment = (query_params.get("environment", "") or "").strip() or None
        resources = ResourceInformation(deploy_client).find_resources_by_tag(
            evt["args"]["key"], evt["args"]["value"], environment_type=environment
        )
        return [resource.serialize() for resource in resources]

    @service.operation(
        rest_path="/apiop/get_effective_output_schema/<args_id>",
        rest_methods=["GET"],
        args={"id": "string"},
    )
    def get_effective_output_schema(evt, ctx):
        """Return the effective (isa-merged) output schema of a ResourceDefinition."""
        deploy_client = _get_deploy_client(evt, ctx)
        id_ = evt["args"]["id"]
        resource_information = ResourceInformation(deploy_client)
        rd = resource_information.get_resource_definition(id_)
        if not rd:
            raise HmdEntityNotFoundException("ResourceDefinition", id_)
        return resource_information.get_effective_output_schema(rd)

    @service.operation(
        rest_path="/apiop/get_resource_definition_ancestry/<args_id>",
        rest_methods=["GET"],
        args={"id": "string"},
    )
    def get_resource_definition_ancestry(evt, ctx):
        """Return the ``isa`` ancestry of a ResourceDefinition, root first."""
        deploy_client = _get_deploy_client(evt, ctx)
        id_ = evt["args"]["id"]
        resource_information = ResourceInformation(deploy_client)
        rd = resource_information.get_resource_definition(id_)
        if not rd:
            raise HmdEntityNotFoundException("ResourceDefinition", id_)
        return [
            ancestor.serialize() for ancestor in resource_information.get_ancestry(rd)
        ]

    @service.operation(
        rest_path="/apiop/get_producers/<args_id>",
        rest_methods=["GET"],
        args={"id": "string"},
    )
    def get_producers(evt, ctx):
        """Return the RepoClassVersions that produce a ResourceDefinition.

        Pass ``?include_subtypes=true`` to also include producers of any subtype
        (a definition that ``isa`` this one).
        """
        deploy_client = _get_deploy_client(evt, ctx)
        id_ = evt["args"]["id"]
        query_params = _query_params(evt)
        include_subtypes = (
            str(query_params.get("include_subtypes", "false")).lower() == "true"
        )
        resource_information = ResourceInformation(deploy_client)
        rd = resource_information.get_resource_definition(id_)
        if not rd:
            raise HmdEntityNotFoundException("ResourceDefinition", id_)
        producers = resource_information.get_producers(
            rd, include_subtypes=include_subtypes
        )
        return [rcv.serialize() for rcv in producers]

    @service.operation(
        rest_path="/apiop/find_resources_by_selector",
        rest_methods=["GET"],
        args={},
    )
    def find_resources_by_selector(evt, ctx):
        """Find resources whose tags satisfy every ``key=value`` pair in the selector.

        The selector is supplied as a comma-separated ``tags`` query parameter, e.g.
        ``?tags=tier=prod,region=us-west-2`` (all pairs must match). Pass
        ``?environment=<type>`` to additionally scope results to that environment.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        query_params = _query_params(evt)
        raw = query_params.get("tags", "") or ""
        environment = (query_params.get("environment", "") or "").strip() or None
        selector = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            selector[key.strip()] = value.strip()
        resources = ResourceInformation(deploy_client).find_resources_by_selector(
            selector, environment_type=environment
        )
        return [resource.serialize() for resource in resources]

    @service.operation(
        rest_path="/apiop/list_resources",
        rest_methods=["GET"],
        args={},
    )
    def list_resources(evt, ctx):
        """List deployed resources, paginated.

        ``?limit=&offset=`` (defaults 50/0, limit clamped to 500). Pass
        ``?environment=<type>`` to scope to resources deployed in that environment
        (the whole graph otherwise). Returns an
        ``{"items", "total", "limit", "offset"}`` envelope, matching
        ``list_change_set_deployments``. Tags are optional metadata, so this is the
        unfiltered companion to the tag-scoped ``find_resources_by_*`` endpoints.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        query_params = _query_params(evt)
        limit = min(int(query_params.get("limit", 50) or 50), 500)
        offset = max(int(query_params.get("offset", 0) or 0), 0)
        environment = (query_params.get("environment", "") or "").strip() or None
        page, total = ResourceInformation(deploy_client).list_resources(
            limit, offset, environment_type=environment
        )
        return {
            "items": [resource.serialize() for resource in page],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @service.operation(
        rest_path="/apiop/suggest_resource_dependencies/<args_type>",
        rest_methods=["GET"],
        args={"type": "string"},
    )
    def suggest_resource_dependencies(evt, ctx):
        """Suggest RepoInstances that could satisfy a resource-type dependency
        in an environment (NERD0004 SPEC0008).

        The operator still *supplies* the satisfying instance when applying a
        changeset; this endpoint auto-resolves the candidates so an operator or
        agent can discover what to supply. Resolution is inheritance-aware (a
        producer of a subtype satisfies), honors ``version_spec``, and filters by
        an optional tag selector.

        Two query forms are supported against the environment ``<type>``:

        - ``?repo_class_version_id=<id>`` — enumerate the consumer's resource
          requirements and return candidates per required ``role`` (also
          surfacing the retained ``repo_class_name`` as a suggestion).
        - ``?resource_namespace=..&resource_definition_name=..&version=..`` with
          optional ``version_spec`` and ``tags`` (``key=value,key=value``) —
          return candidates for a single ad-hoc requirement.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        environment_type = evt["args"]["type"]
        query_params = _query_params(evt)

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        env_info.load_environment()
        resource_information = ResourceInformation(deploy_client)

        def _candidates(def_ref, version_spec, tag_selector):
            return [
                {"name": ri.name, "identifier": ri.identifier}
                for ri in resource_information.find_satisfying_instances(
                    env_info, def_ref, version_spec, tag_selector
                )
            ]

        rcv_id = query_params.get("repo_class_version_id")
        if rcv_id:
            rcv = deploy_client.get_repo_class_version_hmd_lang_deployment(rcv_id)
            if not rcv:
                raise HmdEntityNotFoundException("RepoClassVersion", rcv_id)

            # Retained class-name dependencies are suggestions per role.
            suggested_by_role = {}
            for (
                creq
            ) in deploy_client.get_from_repo_class_version_req_repo_class_hmd_lang_deployment(
                rcv
            ):
                suggested_by_role[creq.role] = env_info.rel_support.ref_to(
                    creq
                ).repo_class_name

            result = {}
            for (
                req
            ) in deploy_client.get_from_repo_class_version_req_resource_definition_hmd_lang_deployment(
                rcv
            ):
                rd = env_info.rel_support.ref_to(req)
                def_ref = {
                    "resource_namespace": rd.resource_namespace,
                    "resource_definition_name": rd.resource_definition_name,
                    "version": rd.version,
                }
                result[req.role] = {
                    "resource_definition": def_ref,
                    "version_spec": req.version_spec,
                    "tag_selector": req.tag_selector,
                    "required": req.required,
                    "suggested_repo_class_name": suggested_by_role.get(req.role),
                    "candidates": _candidates(
                        def_ref, req.version_spec, req.tag_selector
                    ),
                }
            return result

        required_keys = (
            "resource_namespace",
            "resource_definition_name",
            "version",
        )
        if not all(query_params.get(k) for k in required_keys):
            raise ServiceException(
                "suggest_resource_dependencies requires either "
                "'repo_class_version_id' or all of "
                "'resource_namespace', 'resource_definition_name', 'version'."
            )
        def_ref = {k: query_params[k] for k in required_keys}
        return {
            "candidates": _candidates(
                def_ref,
                query_params.get("version_spec"),
                query_params.get("tags"),
            )
        }

    @service.operation(
        rest_path="/apiop/get_deployment_info/<args_type>",
        rest_methods=["GET"],
        args={"type": "string"},
    )
    def get_deployment_info(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        environment_type = evt["args"]["type"]

        environment = deploy_client.search_environment_hmd_lang_deployment(
            {"attribute": "type", "operator": "=", "value": environment_type}
        )
        if len(environment) == 0:
            raise ServiceException(
                f"No Environment with type, {environment_type}, not found."
            )

        assert (
            len(environment) == 1
        ), f"Expected 1 environment with type, {environment_type}. Found {len(environment)}."

        environment = environment[0]
        env_info = EnvironmentInformation(environment, deploy_client)
        diagram = env_info.generate_consolidated_diagram()

        return {
            "diagram": diagram,
        }

    @service.operation(
        rest_path="/apiop/get_deployment_bom/<args_type>",
        rest_methods=["GET"],
        args={"type": "string"},
    )
    def get_deployment_bom(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        environment_type = evt["args"]["type"]

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        return build_environment_bom(env_info)

    @service.operation(
        rest_path="/apiop/get_deployment_history/<args_type>/<args_name>",
        rest_methods=["GET"],
        args={"type": "string", "name": "string"},
    )
    def get_deployment_history(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        environment_type = evt["args"]["type"]
        ri_name = evt["args"]["name"]

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        return env_info.get_repo_instance_history(ri_name)

    @service.operation(
        rest_path="/apiop/get_deployment_config/<args_type>/<args_name>",
        rest_methods=["GET"],
        args={"type": "string", "name": "string"},
    )
    def get_deployment_config(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        environment_type = evt["args"]["type"]
        ri_name = evt["args"]["name"]

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        env_info.load_environment()
        ri = env_info.get_repo_instance(ri_name)
        LOGGER.info(ri.serialize(encode_blobs=False))
        resolver = InstanceConfigResolver(deploy_client, env_info.rel_support)
        config = resolver.get_instance_config(ri, False)
        return with_details(resolver, ri, config, env_info.get_repo_instance)

    @service.operation(
        rest_path="/apiop/register_deployed_instance",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def register_deployed_instance(evt, ctx):
        """Register an already-deployed RepoInstance into an environment with the
        full edge set.

        Reuses the exact path ``apply_changeset`` uses
        (``EnvironmentInformation.add_repo_instance`` with ``status=DEPLOYED``), so a
        seeded service/resource becomes a first-class, resolvable environment member
        (``EnvironmentHasRepoInstance`` + ``RepoInstanceIsaRepoClass`` +
        ``RepoInstanceDeployment`` + ``RepoInstanceDeploymentHasRepoClassVersion``)
        rather than a floating record. The RepoClassVersion must already exist
        (register it first via ``add_repo_class_version``).

        Payload: ``{environment?, repo_class_name, version, instance_name,
        deployment_id?, instance_configuration?, status?, dependencies?, hmd_region?}``.

        ``status`` is ``DEPLOYED`` (default -- ``hmd deploy --local`` recording what
        it just did) or ``DEPLOY_NEXT`` (a client recording a plan it is about to
        run: the RID's ``current`` edge stays ``false`` until
        ``set_deployment_status`` flips it, exactly as a ChangeSet entry behaves).
        ``dependencies`` maps a role to an instance name or list of names already in
        this environment; they are validated against the RepoClassVersion's declared
        roles the same way a ChangeSet entry is, so register in dependency order.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        environment_type = payload.get("environment", "local")
        repo_class_name = payload["repo_class_name"]
        version = payload["version"]
        instance_name = payload["instance_name"]
        deployment_id = payload.get("deployment_id", "local")
        instance_configuration = payload.get("instance_configuration", {})
        status = payload.get("status", DEPLOYED)
        if status not in (DEPLOYED, DEPLOY_NEXT):
            raise ServiceException(
                f"Invalid status {status!r}; expected {DEPLOYED} or {DEPLOY_NEXT}."
            )
        hmd_region = payload.get("hmd_region")

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        env_info.load_environment()

        rcv = ClassInformation(deploy_client).get_repo_class_version(
            repo_class_name, version
        )
        dependencies = None
        if payload.get("dependencies"):
            dependencies = env_info.instance_names_to_refs(payload["dependencies"])

        ri, rid = env_info.add_repo_instance(
            instance_name,
            deployment_id,
            instance_configuration,
            rcv,
            hmd_region=hmd_region,
            dependencies=dependencies,
            status=status,
        )
        get_env_cache().invalidate_environment(environment_type)
        return {
            "repo_instance_id": ri.identifier,
            "repo_instance_deployment_id": rid.identifier,
        }

    @service.operation(
        rest_path="/apiop/generate_dev_deployment_config",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def generate_dev_deployment_config(evt, ctx):
        """Resolve a dev ``--config-file`` from a repo's manifest ``deploy`` block.

        Binds each dependency role to the local NeuronSphere's default Resources (and
        a registered DB / env-linked seeded services) WITHOUT persisting the consumer
        as a RepoInstance or touching the ChangeSet graph. Unresolved roles (no local
        producer) are emitted as editable scaffolds. The returned dict is the exact
        ``{...default_configuration, dependencies, details}`` shape
        ``hmd deploy --local --config-file`` consumes.

        Payload: ``{environment?, instance_name?, default_configuration,
        dependencies, bindings?}`` -- ``default_configuration`` and ``dependencies``
        copied verbatim from the repo manifest's ``deploy`` block.
        """
        from .dev_config_resolver import (
            generate_dev_deployment_config as _generate_dev_config,
        )

        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        environment_type = payload.get("environment", "local")

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        env_info.load_environment()

        return _generate_dev_config(
            deploy_client,
            env_info,
            instance_name=payload.get(
                "instance_name", payload.get("repo_class_name", "dev")
            ),
            default_configuration=payload.get("default_configuration", {}),
            dependencies=payload.get("dependencies", {}),
            bindings=payload.get("bindings"),
        )

    @service.operation(
        rest_path="/apiop/upsert_bundle_version",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def upsert_bundle_version(evt, ctx):
        """Register one Bundle declaration (NERD0010 SPEC0005).

        Body: the ``meta-data/bundles/<name>.json`` shape -- ``bundle_name``,
        ``version``, ``roles``, optional ``config_schema``,
        ``default_configuration`` and ``discovery``. Idempotent for an
        unchanged re-POST; an error for changed content under an existing
        version.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        bv, status = BundleInformation(deploy_client).add_bundle_version(payload)
        return {**bundle_version_to_dict(payload["bundle_name"], bv), "status": status}

    @service.operation(
        rest_path="/apiop/upsert_bundle_versions",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def upsert_bundle_versions(evt, ctx):
        """Register every Bundle a repo class declares (``hmd-bundle-core``).

        Body: ``{"source_repo_class_name", "source_version", "bundles": [...]}``.
        A bundle without its own ``version`` takes ``source_version``. Each
        bundle succeeds or fails on its own; returns ``{"results": [...]}``.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        results = BundleInformation(deploy_client).add_bundle_versions(
            payload.get("source_repo_class_name"),
            payload.get("source_version"),
            payload.get("bundles") or [],
        )
        return {"results": results}

    @service.operation(
        rest_path="/apiop/get_bundle_version/<args_name>",
        rest_methods=["GET"],
        args={"name": "string"},
    )
    def get_bundle_version(evt, ctx):
        """A Bundle version: ``?version=`` exact, ``?version_spec=`` newest
        satisfying, otherwise the newest."""
        deploy_client = _get_deploy_client(evt, ctx)
        name = evt["args"]["name"]
        query = _query_params(evt)
        bv = BundleInformation(deploy_client).get_bundle_version(
            name, query.get("version"), query.get("version_spec")
        )
        return bundle_version_to_dict(name, bv)

    @service.operation(
        rest_path="/apiop/install_release",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def install_release(evt, ctx):
        """Record a delivered Release and report its arrival (NERD0016 SPEC0005).

        Body: ``{"lock": <neuronsphere.lock as JSON>, "release": <release.json>}``.
        Moves no bytes and deploys nothing. Returns per-entry status
        (``present`` / ``awaiting_replication`` / ``digest_mismatch`` /
        ``awaiting_registration``) and ``installed`` once every entry is present.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        return ReleaseInformation(
            deploy_client, get_artifact_presence()
        ).install_release(payload.get("lock") or {}, payload.get("release") or {})

    @service.operation(
        rest_path="/apiop/release_install_status/<args_name>",
        rest_methods=["GET"],
        args={"name": "string"},
    )
    def release_install_status(evt, ctx):
        """An installed release's arrival report (NERD0016 SPEC0030): per pinned
        artifact ``present`` / ``awaiting_replication`` / ``digest_mismatch`` /
        ``awaiting_registration``, and whether it is installed. ``?version=``
        selects the version; the stored report is returned unless
        ``?refresh=true``, which recomputes it -- never writing either way."""
        deploy_client = _get_deploy_client(evt, ctx)
        query = _query_params(evt)
        if not query.get("version"):
            raise ServiceException("Give ?version=.", 400)
        return ReleaseInformation(
            deploy_client, get_artifact_presence()
        ).install_status(
            evt["args"]["name"],
            query["version"],
            refresh=str(query.get("refresh", "")).lower() == "true",
        )

    @service.operation(
        rest_path="/apiop/get_release_version/<args_name>",
        rest_methods=["GET"],
        args={"name": "string"},
    )
    def get_release_version(evt, ctx):
        """A Release version: ``?version=`` exact, else the newest, optionally
        restricted by ``?status=a,b``."""
        deploy_client = _get_deploy_client(evt, ctx)
        name = evt["args"]["name"]
        query = _query_params(evt)
        statuses = query.get("status")
        rv = ReleaseInformation(deploy_client).get_release_version(
            name,
            query.get("version"),
            statuses.split(",") if statuses else None,
        )
        return release_version_to_dict(name, rv)

    @service.operation(
        rest_path="/apiop/check_release_coverage",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def check_release_coverage(evt, ctx):
        """Is this exact combination one that was tested? (NERD0016 SPEC0022)

        Body: ``{"pins": {class: version}}`` or ``{"definition": [<ChangeSet
        definition items>]}``. Reads catalogue and Release data only.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        submitted = payload.get("pins") or payload.get("definition")
        if not submitted:
            raise ServiceException("Provide 'pins' or 'definition'.", 400)
        return ReleaseInformation(deploy_client).check_release_coverage(submitted)

    @service.operation(
        rest_path="/apiop/validate_changeset",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def validate_changeset(evt, ctx):
        """Dry-run validation of a ChangeSet definition.

        Body: ``{"changes": [<change item>, ...]}`` where each change item
        matches the shape stored in ``ChangeSet.definition`` (at minimum
        ``repo_instance_name``, ``repo_class_name``, ``repo_class_version``,
        and optionally ``dependencies`` as ``{role: instance_name}``).

        Returns ``{"valid": bool, "errors": [...], "warnings": [...]}`` where
        each error/warning is ``{"type", "instance", "message"}``. The endpoint
        verifies that each referenced RepoClass + version exists, that every
        required role on the class version is supplied, that every named
        dependency resolves to another change in the set, and that the implied
        instance dependency graph has no cycles. It performs no DB writes.

        It also evaluates the deploy requirements of the tool set that will run
        the deploys (BACON ``toolset.deploy_requirements``). Optional body keys:
        ``environments``, the environment types the change set will be applied
        to, so each is checked against the image mapped to it (otherwise the
        default image, with instances found by name); and
        ``acknowledge_requirements``, requirement names the operator overrides,
        which are then reported as warnings.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]
        return validate_changes(
            deploy_client,
            payload.get("changes", []),
            environment_types=payload.get("environments") or [],
            acknowledged=payload.get("acknowledge_requirements") or (),
        )

    @service.operation(
        rest_path="/apiop/compare_environments",
        rest_methods=["POST"],
        args={"payload": "json"},
    )
    def compare_environments(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        payload = evt["args"]["payload"]

        from_env = payload["from_env"]
        to_env = payload["to_env"]

        from_environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=from_env
        )
        to_environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=to_env
        )

        from_info = EnvironmentInformation(from_environment, deploy_client)
        to_info = EnvironmentInformation(to_environment, deploy_client)

        from_bom = build_environment_bom(from_info)
        to_bom = build_environment_bom_or_empty(to_info)

        differences = []
        to_dict = {change["repo_instance_name"]: change for change in to_bom}

        from_names = [ri["repo_instance_name"] for ri in from_bom]
        new_in_from = [rin for rin in from_names if not rin in to_dict]
        removed_from_to = [rin for rin in to_dict if not rin in from_names]

        for name in from_names:
            frm = [frm for frm in from_bom if frm["repo_instance_name"] == name][0]
            if name in new_in_from:
                differences.append(frm)
            else:
                to = [to for to in to_bom if to["repo_instance_name"] == name][0]
                if frm != to:
                    differences.append(frm)

        return {
            "deploy_change_set": differences,
            "removed_instances": removed_from_to,
            "existing_change_set": [
                to_dict.get(change["repo_instance_name"])
                for change in differences
                if change["repo_instance_name"] in to_dict
            ],
        }

    @service.operation(
        rest_path="/apiop/find_repo_class_versions/<args_repo_class_name>",
        rest_methods=["POST"],
        args={"repo_class_name": "string"},
    )
    def find_repo_class_versions(evt, ctx):
        """List a repo class's versions, newest-first.

        Legacy default (no query params) returns a bare list of version dicts,
        each with a resolved ``dependencies`` map. Opt-in pagination is
        triggered by any of ``?limit=&offset=&q=&include_deps=``:

        - ``limit`` (default 50, clamped to 500) / ``offset`` (default 0) page
          the sorted list; the response becomes an
          ``{"items", "total", "limit", "offset"}`` envelope like
          ``list_resources``.
        - ``q`` filters versions by case-insensitive substring before paging.
        - ``include_deps=false`` skips the per-version dependency resolution
          entirely (the version picker only needs the version string).

        The expensive dependency resolution runs *after* the slice, so a paged
        call only resolves deps for the page (or not at all), never the whole
        list.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        repo_class_name = evt["args"]["repo_class_name"]
        query = _query_params(evt)

        paginated = any(k in query for k in ("limit", "offset", "q", "include_deps"))
        limit = min(int(query.get("limit", 50) or 50), 500)
        offset = max(int(query.get("offset", 0) or 0), 0)
        q = (query.get("q", "") or "").strip().lower()
        include_deps = str(query.get("include_deps", "true")).lower() != "false"

        repo_classes = deploy_client.search_repo_class_hmd_lang_deployment(
            {"attribute": "repo_class_name", "operator": "=", "value": repo_class_name}
        )

        versions: List[RepoClassVersion] = []

        for repo_class in repo_classes:
            rcv_rels = deploy_client.get_from_repo_class_has_repo_class_version_hmd_lang_deployment(
                repo_class
            )
            versions.extend(_resolve_rcv_targets(deploy_client, rcv_rels))

        versions = sort_versions(versions, lambda rcv: rcv.version)

        if q:
            versions = [rcv for rcv in versions if q in str(rcv.version or "").lower()]

        total = len(versions)
        page = versions[offset : offset + limit] if paginated else versions

        rs = RelationshipSupport()
        rs.register_client(deploy_client._base_client)
        result = []
        for rcv in page:
            rcv_dict = rcv.serialize(encode_blobs=False)
            deps = {}
            if include_deps:
                for (
                    rel
                ) in deploy_client.get_from_repo_class_version_req_repo_class_hmd_lang_deployment(
                    rcv
                ):
                    target_rc = rs.ref_to(rel)
                    if target_rc is None:
                        continue
                    deps[rel.role] = {
                        "repo_class_name": target_rc.repo_class_name,
                        "required": str(rel.required).lower(),
                        "version_spec": rel.version_spec,
                    }
            rcv_dict["dependencies"] = deps
            result.append(rcv_dict)

        if not paginated:
            return result

        return {
            "items": result,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @service.operation(
        rest_path="/apiop/get_repo_class_version_detail/<args_repo_class_name>/<args_version>",
        rest_methods=["GET"],
        args={"repo_class_name": "string", "version": "string"},
    )
    def get_repo_class_version_detail(evt, ctx):
        """Fetch a single RepoClassVersion's full detail, including its
        BACON ``discovery`` metadata, for the ``hmd-app-neuronsphere``
        RepoClassVersion detail view.

        :param evt: The event initiating the request.
        :param ctx: The request context.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        repo_class_name = evt["args"]["repo_class_name"]
        version = evt["args"]["version"]

        class_information = ClassInformation(deploy_client)
        rcv = class_information.get_repo_class_version(repo_class_name, version)

        rs = RelationshipSupport()
        rs.register_client(deploy_client._base_client)
        rcv_dict = rcv.serialize(encode_blobs=False)
        deps = {}
        for (
            rel
        ) in deploy_client.get_from_repo_class_version_req_repo_class_hmd_lang_deployment(
            rcv
        ):
            target_rc = rs.ref_to(rel)
            if target_rc is None:
                continue
            deps[rel.role] = {
                "repo_class_name": target_rc.repo_class_name,
                "required": str(rel.required).lower(),
                "version_spec": rel.version_spec,
            }
        rcv_dict["dependencies"] = deps
        return rcv_dict

    def _repo_class_instances(evt, ctx):
        deploy_client = _get_deploy_client(evt, ctx)
        repo_class_name = evt["args"]["repo_class_name"]
        environment_type = evt["args"]["type"]

        environment = get_valid_environment(
            deploy_client=deploy_client, environment_type=environment_type
        )
        env_info = EnvironmentInformation(environment, deploy_client)
        return find_class_instances(env_info, repo_class_name)

    @service.operation(
        rest_path="/apiop/get_repo_class_instances/<args_repo_class_name>/<args_type>",
        rest_methods=["GET"],
        args={"repo_class_name": "string", "type": "string"},
    )
    def find_repo_class_instances(evt, ctx):
        """Instances of a RepoClass in an environment, pending (DEPLOY_NEXT) or current."""
        return _repo_class_instances(evt, ctx)

    @service.operation(
        rest_path="/apiop/find_repo_class_instances/<args_repo_class_name>/<args_type>",
        rest_methods=["GET"],
        args={"repo_class_name": "string", "type": "string"},
    )
    def find_repo_class_instances_by_path(evt, ctx):
        """Alias of ``get_repo_class_instances`` under the name the Robot suites use."""
        return _repo_class_instances(evt, ctx)

    @service.operation(
        rest_path="/apiop/list_repo_classes",
        rest_methods=["GET"],
        args={},
    )
    def list_repo_classes(evt, ctx):
        """List every registered RepoClass with its known versions.

        Returns a list of ``{"repo_class_name": str, "identifier": str,
        "versions": [<sorted version strings>], "latest_version": str|None,
        "summary": str, "capability_count": int}`` entries. Versions are sorted
        with the same comparator used by ``find_repo_class_versions`` so the
        GUI can render selectors consistently.

        ``summary`` and ``capability_count`` (NERD0013 SPEC0001) come from the
        latest version's BACON ``discovery`` block, via the same cached catalog
        index ``search_discovery`` reads -- so the listing is one bulk load,
        not one relationship walk per class.
        """
        deploy_client = _get_deploy_client(evt, ctx)

        return [
            {
                "repo_class_name": entry.repo_class_name,
                "identifier": entry.identifier,
                "versions": list(entry.versions),
                "latest_version": entry.version,
                "summary": entry.summary,
                "capability_count": len(entry.capabilities),
            }
            for entry in get_or_build_index(deploy_client)
        ]

    @service.operation(
        rest_path="/apiop/search_discovery",
        rest_methods=["GET"],
        args={},
    )
    def search_discovery(evt, ctx):
        """Search every RepoClass's latest-version BACON ``discovery`` metadata
        (NERD0013 SPEC0002) -- "which repo class can do X?".

        Query parameters:

        - ``q``: free text; every whitespace-separated token must appear
          (case-insensitive substring) in the summary, a capability
          name/description, or an entry point. Class names are not searched.
        - ``kind``: exact BACON capability kind (``endpoint``, ``cli_command``,
          ``function``, ``class``, ``operation``); only capabilities of that
          kind are considered and returned. Unknown kinds are a 400.
        - ``repo_class_name``: case-insensitive prefix filter.
        - ``limit`` (default 50, clamped to 500) / ``offset`` (default 0).

        Returns ``{"items", "total", "limit", "offset", "q", "kind",
        "repo_class_name"}`` where each item carries ``repo_class_name``,
        ``version``, ``summary``, ``score``, ``matched_fields``, the matching
        ``capabilities`` and ``entry_points`` (all of them when ``q`` is empty),
        ``related_docs`` and ``capability_count``. Items are ordered by score
        descending, then name. The index is served from Redis when the NERD0005
        cache is enabled and rebuilt from the graph otherwise.
        """
        deploy_client = _get_deploy_client(evt, ctx)
        query = _query_params(evt)

        q = (query.get("q", "") or "").strip()
        kind = (query.get("kind", "") or "").strip() or None
        repo_class_name = (query.get("repo_class_name", "") or "").strip() or None
        limit = min(int(query.get("limit", 50) or 50), 500)
        offset = max(int(query.get("offset", 0) or 0), 0)

        matches = search_index(
            get_or_build_index(deploy_client),
            q=q,
            kind=kind,
            repo_class_name=repo_class_name,
        )

        return {
            "items": matches[offset : offset + limit],
            "total": len(matches),
            "limit": limit,
            "offset": offset,
            "q": q,
            "kind": kind,
            "repo_class_name": repo_class_name,
        }
