# Changelog

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
