# Changelog

## 2026-10-19

- fix: make the datadog-lambda dependency optional; services route telemetry through the OTel collector instead of Datadog

## 2026-10-18

- feat: base ResourceDefinition `mcp.neuronsphere.io/mcp-server` (parent `application`): an MCP server's `server_name`, `url`, and how the GUI's MCP gateway authenticates to and authorizes it (`auth_mode`, `credential_ref`, `required_role`)

## 2026-10-17

- feat: environment-scoped architecture models (NERD036 SPEC006): `get_architecture_model` with `environment` draws the environment's instances (`inst:<env>/<name>`, with class, current deployment version and status), their declared resources overlaid by the Resource records their deployments produced (`resource_name`; outputs are never copied), `depends_on` edges from role bindings and `unbound-role` / `not-deployed` findings; roots may be `instance:<name>` or a class standing for its instances; every model's scope names its `root_nodes`
- feat: `render_architecture` (a scope or a model, rendered with the newest registered template version satisfying a spec) and `render_architecture_view/<name>` (a saved view as documentation fetches it, with `model_digest`, `etag` and `rendered_at`); templates are Mickey packages registered with their `files` and rendered by a sandboxed Jinja environment following Mickey's rules, without hooks (NERD036)
- feat: architecture views (NERD036): `add_repo_class_version` and `resync_repo_class_version_dependencies` keep the manifest's `deploy.resources` on the class version (shown by `get_repo_class_version_detail`); `register_rendering_template`, `list_rendering_templates` and `get_rendering_template/<name>/<version>` (versions side by side, re-registering replaces); `upsert_architecture_view` and `list_architecture_views`; `get_architecture_model`, the `architecture.neuronsphere.io/v1` model of a scope's classes, roles, declared resources and their `connects_to` flows
- test: `test/robot_architecture.robot`, safe against a persistent control plane (no clear, run-unique names); the base-catalog checks for the AWS types moved there from pytest
- feat: base ResourceDefinitions for what a microservice creates for itself (NERD036): neutral roots `network.neuronsphere.io/api-gateway`, `compute.neuronsphere.io/function`, `messaging.neuronsphere.io/queue` and `database.neuronsphere.io/key-value-table` (parent `database`), and the AWS types the shared CDKTF factory produces, `aws.neuronsphere.io/api-gateway-rest-api`, `lambda-function`, `dynamodb-table`, `sqs-queue` and `s3-bucket` (parent `storage.neuronsphere.io/object-store`)

## 2026-10-14

- feat: base ResourceDefinition `database.neuronsphere.io/database-account` (a service's own database and login on a shared instance; parent `database`, not `postgres`), requiring `database_name` and `secret_name`. Consumers that asked for `postgres` bound to the environment's shared instance and got its admin credential

## 2026-10-09

- feat: catalogue routes for the GUI (NERD009/NERD011): `list_bundles`, `list_bundle_versions/<name>`, `list_releases` (kind, newest version per status, newest installed) and `list_release_versions/<name>`; `get_release_version` and `release_version_to_dict` now also return `notes`, `install_report` and (on get) the releases a platform release `includes`
- feat: base catalog ships `connection.neuronsphere.io/connection` (isa `credential`, adds optional `conn_id` and `secret_keys`) so engine-specific connection subtypes can parent it
- feat: serialized Resources (`hmd_resources`, `get_deployment_resources`) carry `resource_definition.ancestry`, the root-first `isa` chain, so consumers depending on a supertype can match a subtype Resource

## 2026-10-08

- fix: `resync_repo_class_version_dependencies` updates an existing dependency edge's `required` and `version_spec` (and a resource edge's `tag_selector`) from the manifest instead of skipping an edge that is already there, so an edited manifest reaches an already-registered version without a version bump. The stored relationship is changed and re-upserted, since a new one would get its own identifier and duplicate the edge

## 2026-10-06

- feat: installed releases keep their install report (`install_report`) and the publisher's evidence and notes; `release_install_status` returns it, recomputed without writing on `?refresh=true`; retries skip settled artifacts and stop at a digest mismatch
- fix: a release pin is a (repo class, version) pair: install, coverage and pins_of accept a class pinned at several versions (a generic bucket class runs at several versions in one bundle); coverage keys its per-pin report `class@version`

## 2026-10-05

- fix: raise hmd_base_service.exceptions.ServiceException instead of hmd_cli_tools'; hmd-base-service 0.2.222's REST handler maps only its own class, so every 4xx came back as a bare 500
- fix: build on hmd-ms-base 0.2.282, the oldest base a service behind an nsctl environment route can run on (NERD024)
- feat: Bundle registry (NERD0010): `upsert_bundle_version`, `upsert_bundle_versions` (every bundle a repo class such as `hmd-bundle-core` declares, versioned by that repo's version) and `get_bundle_version`
- feat: Release registry (NERD0016): ReleaseVersion lifecycle (candidate, verifying, verified, failed, released, superseded), `install_release` reporting per-artifact arrival, `get_release_version` and `check_release_coverage`
- refactor: `validate_changeset` body extracted into `changeset_validation.validate_changes` so generated ChangeSets get the same checks; list-valued dependency roles are now resolved instead of raising
- refactor: dependency-edge wiring parameterized by edge type (`DependencyEdges`) so bundle roles reuse it
- fix: ordered version specs compare lexicographically; `>=1.2.0` accepted no `2.x` and `>=1.0.0,<2.0.0` rejected `1.5.0`
- fix: `sort_versions` ordered by patch before minor and major, so `0.1.4` sorted ahead of `0.2.0`

## 2026-10-02

- feat: evaluate BACON toolset deploy requirements (`DeployRequirementEvaluator`) in `validate_changeset`, against the tool set image each environment's deploys run on; optional `environments` and `acknowledge_requirements` body keys
- feat: store a manifest's `toolset` block and `deploy.commands` on the RepoClassVersion
- fix: record a deployment's `deployment_image` from `HMD_APP_IMAGE_MAP` for its environment, not always `HMD_APP_IMAGE`

## 2026-09-20

- feat: registry-and-resolver core split out of hmd-ms-deployment (NERD0015) — RepoClass/RCV registration, ResourceDefinition catalogue, Resources, discovery search, RepoInstance records
- feat: `register_deployed_instance` accepts `status` (DEPLOY_NEXT) and `dependencies` so a client can record a deployment plan without a ChangeSet
- feat: `get_repo_class_instances` (and alias `find_repo_class_instances`) lists pending and current deployments with `deployment_identifier`
- feat: DAG-free environment BOM (`bom.py`) behind `get_deployment_bom` / `compare_environments`
- feat: private null-cache hook (`_env_cache_hook`) so the core runs without Redis
