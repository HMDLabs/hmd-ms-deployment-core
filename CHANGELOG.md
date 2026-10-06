# Changelog

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
