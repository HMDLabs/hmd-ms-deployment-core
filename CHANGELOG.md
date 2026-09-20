# Changelog

## 2026-09-20

- feat: registry-and-resolver core split out of hmd-ms-deployment (NERD0015) — RepoClass/RCV registration, ResourceDefinition catalogue, Resources, discovery search, RepoInstance records
- feat: `register_deployed_instance` accepts `status` (DEPLOY_NEXT) and `dependencies` so a client can record a deployment plan without a ChangeSet
- feat: `get_repo_class_instances` (and alias `find_repo_class_instances`) lists pending and current deployments with `deployment_identifier`
- feat: DAG-free environment BOM (`bom.py`) behind `get_deployment_bom` / `compare_environments`
- feat: private null-cache hook (`_env_cache_hook`) so the core runs without Redis
