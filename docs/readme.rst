Overview
========

``hmd-ms-deployment-core`` is the registry-and-resolver core of the NeuronSphere
deployment service. It owns:

- **RepoClass / RepoClassVersion registration** — ``add_repo_class_version``,
  ``resync_repo_class_version_dependencies``, ``find_repo_class_versions``,
  ``get_repo_class_version_detail``, ``list_repo_classes``.
- **The ResourceDefinition catalogue** (NERD0004) — the bundled base types under
  ``hmd_ms_deployment_core/base_resources/``, seeded on every cold start, plus
  ``upsert_resource_definition``, ``declare_produces_resource_definition``,
  ``get_effective_output_schema``, ``get_resource_definition_ancestry``,
  ``get_producers`` and ``suggest_resource_dependencies``.
- **Resources** produced by deployments — ``submit_resources``,
  ``get_deployment_resources``, ``find_resources_by_tag``, ``find_resources_by_selector``,
  ``list_resources``.
- **Discovery search** (NERD0013) — ``search_discovery``.
- **RepoInstance records** — ``register_deployed_instance`` (with ``status`` and
  ``dependencies``), ``set_deployment_status``, ``get_deployment_history``,
  ``get_deployment_config``, ``get_deployment_bom``, ``get_deployment_info``,
  ``get_repo_class_instances``, ``generate_dev_deployment_config``,
  ``validate_changeset`` and ``compare_environments``.

It does **not** own ChangeSets, the deployment DAG, Argo execution, run
observability or the librarian monitors: those live in ``hmd-ms-deployment``, whose
image is built ``FROM`` this one and loads ``hmd_ms_deployment_core.operations``
alongside its own module. Every ``/apiop/*`` route is the same on both images.

This is the image ``nsctl env start`` runs as the local control plane, under the
service name ``hmd_ms_deployment``. See ``NERD0015`` in the ``hmd-ms-deployment``
repository for the split, its boundary and what it does and does not protect.

Licence
-------

BUSL 1.1 for the service code (``src/python``, ``src/docker``), Apache 2.0 for the
deploy descriptor (``meta-data/``, ``src/cdktf/``) -- see ``LICENSE.txt``.
