.. Resource Definitions and Resources

Resource Definitions and Resources
====================================

Overview
--------------------

The Deployment Service models *what a RepoClass produces* as first-class, queryable
data. When an instance is deployed it emits concrete **Resources** (an EKS cluster, a
Lambda function, a microservice API endpoint, an Airflow application, etc.), each typed
by a namespaced, versioned **Resource Definition** that describes the schema of its
outputs.

This feature (NERD0004) adds three new nouns to the ``hmd_lang_deployment`` vocabulary --
``resource_definition``, ``resource``, and ``resource_tag`` -- along with the
``/apiop/*`` service operations to manage them and an artifact-ingestion path that reads
Resource Definition YAML shipped inside a ``RepoClass`` artifact.

.. note::

    See :doc:`proposals/NERD0004_ResourceDefinitions` for the full design rationale and
    the staged (future) work.

Concepts
--------------------

ResourceDefinition
++++++++++++++++++++

A ``ResourceDefinition`` is a CRD-like document (modeled after a Kubernetes Custom
Resource Definition) that describes the *schema of the outputs* a ``RepoClass`` produces
when it is deployed. Its business identity is the tuple
(``resource_namespace``, ``resource_definition_name``, ``version``); multiple definitions
with the same name may coexist under different namespaces or versions.

Key attributes:

- ``resource_namespace``, ``resource_definition_name``, ``version`` -- the namespaced,
  versioned business identity.
- ``output_schema`` -- a JSON Schema (draft-07) describing the structure of the outputs a
  producing ``RepoClass`` emits.
- ``description`` -- human- and agent-readable summary of the resource type.
- ``resource_metadata`` -- a mapping of agent-facing annotations that let LLM agents
  reason about what the resource is and how it relates to other resources.

A ``ResourceDefinition`` supports **single inheritance** via an ``isa`` relationship: it
may inherit from at most one other ``ResourceDefinition``. For example an
``aws/eks-cluster`` definition ``isa`` ``kubernetes/kubernetes-cluster`` definition,
establishing that every EKS cluster *is a* Kubernetes cluster. The effective output
schema of a child is its parent's ``output_schema`` merged with its own, and the ``isa``
graph must be acyclic.

A ``ResourceDefinition`` is a globally referenceable entity: once stored it is **not**
confined to the ``RepoClass`` that shipped it. Any ``RepoClass`` may declare that it
produces that resource type without carrying the definition YAML itself.

Resource
++++++++++++++++++++

A ``Resource`` is a concrete, deployment-time output. Each ``Resource`` has a
``resource_name`` (business identity) and an ``output`` mapping holding the actual output
values (an ARN, an endpoint URL, a cluster name, and so on).

A ``Resource`` is typed by a ``ResourceDefinition`` (via an ``isa`` relationship) and is
linked to the ``RepoInstanceDeployment`` that produced it. Its ``output`` must conform to
the effective ``output_schema`` of its ``ResourceDefinition`` (including inherited
schema). Together, the Resources recorded for an environment form the queryable inventory
of what actually exists there.

ResourceTag
++++++++++++++++++++

A ``ResourceTag`` is a searchable ``key``/``value`` tag attached to a ``Resource``
(Kubernetes-label style). Tags are modeled as their own noun -- **not** as a mapping
attribute on ``Resource`` -- because ``mapping`` and ``collection`` attributes are stored
base64-encoded and cannot be searched or filtered, whereas tag-selector queries must
filter on string attributes.

Tags enable equality-based **tag-selector** queries (a set of required ``key=value``
pairs, all of which must match), for example "a Kubernetes cluster tagged
``tier=prod``".

Entity Model
--------------------

The resource nouns and the relationships between them (and to ``RepoClassVersion`` and
``RepoInstanceDeployment``):

.. uml:: ./puml/resources.puml

Service Operations
--------------------

All operations are exposed under the ``/apiop/`` prefix, mirroring the rest of the
Deployment Service API.

Upsert Resource Definition
++++++++++++++++++++++++++++

``POST /apiop/upsert_resource_definition``

Creates or updates a ``ResourceDefinition``. Idempotent on the
(``resource_namespace``, ``resource_definition_name``, ``version``) identity. Returns the
serialized ``ResourceDefinition``.

Request body::

    {
        "resource_namespace": "aws",
        "resource_definition_name": "eks-cluster",
        "version": "0.1.0",
        "description": "An Amazon EKS Kubernetes cluster.",
        "resource_metadata": {"provider": "aws"},
        "output_schema": {
            "type": "object",
            "properties": {
                "cluster_name": {"type": "string"},
                "cluster_endpoint": {"type": "string"}
            },
            "required": ["cluster_name"]
        },
        "parent": {
            "resource_namespace": "kubernetes",
            "resource_definition_name": "kubernetes-cluster",
            "version": "0.1.0"
        },
        "repo_class_version_id": "<nid>",
        "produced_by_repo_class_version_id": "<nid>",
        "role": "cluster"
    }

Fields:

- ``description``, ``resource_metadata``, ``output_schema``, ``parent``,
  ``repo_class_version_id``, ``produced_by_repo_class_version_id``, and ``role`` are all
  optional.
- ``parent`` references another ``ResourceDefinition`` by its
  (namespace, name, version) identity to establish the ``isa`` inheritance edge.
- ``repo_class_version_id`` records provenance (which ``RepoClassVersion`` shipped the
  definition); ``produced_by_repo_class_version_id`` additionally records a *produces*
  edge.

Seed Base Resource Definitions
++++++++++++++++++++++++++++++++

``POST /apiop/seed_base_resource_definitions``

Idempotently upserts the standard **base ResourceDefinition catalog** bundled with the
service (see `Base ResourceDefinitions`_ below). Takes no payload and is safe to call on
every startup. Returns the list of seeded
(``resource_namespace``, ``resource_definition_name``, ``version``) identities.

The service now also calls this seeding logic automatically, in-process, every time it
starts (Lambda cold start or FastAPI app init) -- see ``deployment_ops.setup()`` --
so cloud dev/test/prod environments get the base catalog without any external trigger.
The local platform (``hmd ns up``) still calls this endpoint explicitly before seeding
its BOM; that call is now belt-and-suspenders rather than required.

Get Resource Definition
++++++++++++++++++++++++++++

``GET /apiop/get_resource_definition/<id>``

Fetches a single ``ResourceDefinition`` by its entity identifier. Returns the serialized
``ResourceDefinition``.

List Resource Definitions
++++++++++++++++++++++++++++

``GET /apiop/list_resource_definitions``

Returns all ``ResourceDefinition`` records sorted by
(``resource_namespace``, ``resource_definition_name``, ``version``).

Query parameters:

- ``resource_namespace`` -- optional filter; returns only definitions in the given
  namespace.

Declare Produces Resource Definition
+++++++++++++++++++++++++++++++++++++

``POST /apiop/declare_produces_resource_definition``

Records that a ``RepoClassVersion`` produces a resource type. The referenced
``ResourceDefinition`` need only already exist -- the declaring version does not have to
ship the YAML.

Request body::

    {
        "repo_class_version_id": "<nid>",
        "resource_definition": {
            "resource_namespace": "aws",
            "resource_definition_name": "eks-cluster",
            "version": "0.1.0"
        },
        "role": "cluster"
    }

``role`` is optional and distinguishes multiple produced resource types on the same
version.

Submit Resources
++++++++++++++++++++++++++++

``POST /apiop/submit_resources``

The deploy-CLI-facing operation used at deployment time to record the concrete resources
an instance produced. Idempotent per (deployment, ``resource_name``). Each ``output`` is
validated against its ``ResourceDefinition``'s **effective** ``output_schema`` -- its own
schema merged with its ``isa`` ancestors' (JSON Schema draft-07). Returns the serialized
``Resource`` records.

Request body::

    {
        "repo_instance_deployment_id": "<RepoInstanceDeployment nid>",
        "resources": [
            {
                "resource_name": "core-eks",
                "resource_definition": {
                    "resource_namespace": "aws",
                    "resource_definition_name": "eks-cluster",
                    "version": "0.1.0"
                },
                "output": {
                    "cluster_name": "core-eks",
                    "cluster_endpoint": "https://ABC123.eks.us-west-2.amazonaws.com"
                },
                "tags": [
                    {"key": "tier", "value": "prod"},
                    {"key": "region", "value": "us-west-2"}
                ]
            }
        ]
    }

.. warning::

    ``repo_instance_deployment_id`` is the ``RepoInstanceDeployment`` entity
    **identifier** (nid), not the business ``deployment_id`` (e.g. ``"aaa"``).

Get Deployment Resources
++++++++++++++++++++++++++++

``GET /apiop/get_deployment_resources/<id>``

Lists the ``Resource`` records recorded for a ``RepoInstanceDeployment`` identifier.

Find Resources by Tag
++++++++++++++++++++++++++++

``GET /apiop/find_resources_by_tag/<key>/<value>``

Finds all ``Resource`` records carrying a ``ResourceTag`` with the given ``key`` and
``value``. This backs the tag-selector query convention described above.

Find Resources by Selector
++++++++++++++++++++++++++++

``GET /apiop/find_resources_by_selector?tags=<k1>=<v1>,<k2>=<v2>``

Finds ``Resource`` records whose tags satisfy **every** ``key=value`` pair in the
selector (Kubernetes label-selector AND semantics). The selector is supplied as a
comma-separated ``tags`` query parameter, for example
``?tags=tier=prod,region=us-west-2``. An empty selector matches nothing.

Get Effective Output Schema
++++++++++++++++++++++++++++

``GET /apiop/get_effective_output_schema/<id>``

Returns the **effective** output schema of a ``ResourceDefinition``: its own
``output_schema`` deep-merged with those of its ``isa`` ancestors (root first, child
overriding). ``properties`` are merged key-wise and ``required`` lists are unioned.
This is the schema a submitted ``Resource``'s ``output`` is validated against.

Get Resource Definition Ancestry
+++++++++++++++++++++++++++++++++

``GET /apiop/get_resource_definition_ancestry/<id>``

Returns the ``isa`` ancestry of a ``ResourceDefinition`` as a list ordered **root
first**, ending with the definition itself. For an ``aws/eks-cluster`` that ``isa``
``kubernetes/kubernetes-cluster`` this returns ``[kubernetes-cluster, eks-cluster]``.

Get Producers
++++++++++++++++++++++++++++

``GET /apiop/get_producers/<id>``

Returns the ``RepoClassVersion`` records that declare (via
``repo_class_version_produces_resource_definition``) that they produce the given
``ResourceDefinition``.

Query parameters:

- ``include_subtypes`` -- when ``true``, also include producers of any **subtype** (a
  definition that ``isa`` this one), mirroring the substitutability that future
  resource-based dependency resolution relies on. A producer of ``aws/eks-cluster`` is
  therefore returned when querying producers of ``kubernetes/kubernetes-cluster`` with
  ``?include_subtypes=true``.

Submitting Resources from ``hmd deploy``
-----------------------------------------

Resource submission is wired into the standard ``hmd deploy`` call so a deploying
RepoClass records what it produced without any bespoke tooling.

When the Deployment Service builds a deployment workflow, each instance's generated
deploy script exports the RepoInstanceDeployment identifier as the
``HMD_REPO_INSTANCE_DEPLOYMENT_ID`` environment variable (the same identifier used for the
deployment's status callbacks) before invoking ``hmd deploy``. ``hmd deploy`` reads it as
the default for its ``--repo-instance-deployment-id`` argument. After the deploy commands
complete, ``hmd deploy`` reads a produced-resources output file and, if present, POSTs it
to ``/apiop/submit_resources`` related to that RepoInstanceDeployment.

.. note::

   The identifier is passed as an **environment variable**, not a CLI flag, to preserve
   backward compatibility. An older ``HMD_APP_IMAGE`` whose ``hmd-cli-deploy`` predates
   resource submission simply ignores the unrecognized environment variable (and performs
   no submission), whereas an unknown ``--repo-instance-deployment-id`` *argument* would
   cause the deploy step to fail. Resource submission therefore activates only once the
   deployment image ships a ``hmd-cli-deploy`` that understands it.

Produced outputs must reflect **what was actually deployed** -- Helm values change the
rendered chart, and Terraform outputs (ARNs, IDs) are only known after apply -- so they
are **rendered from the live deployment** rather than hand-authored. Each deploy tool
writes **one JSON file per Resource** into the well-known directory
``meta-data/resources_output/`` (each file a single resource dict matching the
``submit_resources`` ``resources[]`` shape); ``hmd deploy`` aggregates every file there
and submits them. A legacy single ``meta-data/resources_output.json`` **list** is still
honored for backward compatibility.

Each per-Resource file looks like:

.. code-block:: json

    {
      "resource_name": "core-eks",
      "resource_definition": {
        "resource_namespace": "aws.neuronsphere.io",
        "resource_definition_name": "eks-cluster",
        "version": "0.1.0"
      },
      "output": {
        "cluster_name": "core-eks",
        "cluster_arn": "arn:aws:eks:us-west-2:...:cluster/core-eks"
      },
      "tags": [
        {"key": "tier", "value": "prod"},
        {"key": "region", "value": "us-west-2"}
      ]
    }

Rendering the outputs (helm / cdktf)
+++++++++++++++++++++++++++++++++++++

The two common deploy tools render these files automatically -- a repo only ships the
declaration, never a static output file:

- **Helm** (``hmd-cli-helm``): the chart ships
  ``src/helm/templates/resource-outputs.yaml``, a benign **ConfigMap** whose ``data``
  maps each produced ``resource_name`` to a JSON string of its resource dict, built from
  ``.Values``. After a successful deploy, ``hmd-cli-helm`` renders just that template with
  ``helm template --show-only templates/resource-outputs.yaml`` (using the same merged
  values) and writes each ``data`` entry to ``meta-data/resources_output/``. Because it is
  an ordinary, data-only ConfigMap that no controller acts on, it is also harmless if an
  older ``hmd-cli-helm`` (unaware of the export step) simply applies it with the rest of
  the chart -- it can never break the deploy. No guard value is required.

- **cdktf** (``hmd-cli-cdktf`` + ``hmd-lib-cdktf``): a stack calls
  ``self.add_resource_output(resource_name, resource_definition, output, tags)`` (on
  ``HmdCdkTfStack``), which emits a ``TerraformOutput`` named ``hmd_resource__<name>``
  whose value is the JSON-encoded resource dict (output values may be Terraform tokens
  such as an IAM role ARN, resolved at apply). After ``terraform apply``, ``hmd-cli-cdktf``
  reads ``terraform output -json``, selects the ``hmd_resource__*`` outputs, and writes
  one file per Resource into ``meta-data/resources_output/``.

The submission step no-ops when ``--repo-instance-deployment-id`` is absent (for example
a local ``hmd deploy -l`` run) or when nothing was produced.

Artifact Ingestion
--------------------

Resource Definitions can be shipped inside a ``RepoClass`` artifact and ingested
automatically. When a new ``RepoClassVersion`` is registered via the artifact-driven path
(when the artifact is pulled from the Artifact Librarian), the Deployment Service reads
Resource Definition YAML files from the standard folder location
``meta-data/resources/*.yaml`` inside the unpacked artifact. This mirrors the existing
``meta-data/`` ingestion mechanism used for other entities.

For each YAML document the service:

#. Upserts the ``ResourceDefinition`` (idempotent on the (namespace, name, version)
   identity).
#. Records a provenance edge linking the definition to the ``RepoClassVersion`` that
   shipped it.
#. If the document sets ``produces: true``, also records a *produces* edge from the
   ``RepoClassVersion`` to the definition.

A Resource Definition YAML document has the following keys:

- ``resource_namespace``, ``resource_definition_name``, ``version`` -- required identity.
- ``description`` -- optional.
- ``resource_metadata`` -- optional mapping of agent-facing annotations.
- ``output_schema`` -- optional JSON Schema for the outputs.
- ``parent`` -- optional (namespace, name, version) of the definition to inherit from.
- ``produces`` -- optional boolean; when ``true`` also declares a *produces* edge from
  the shipping ``RepoClassVersion``.
- ``role`` -- optional role for the produces edge.

Example ``meta-data/resources/eks-cluster.yaml``:

.. code-block:: yaml

    resource_namespace: aws
    resource_definition_name: eks-cluster
    version: 0.1.0
    description: An Amazon EKS Kubernetes cluster.
    resource_metadata:
      provider: aws
    parent:
      resource_namespace: kubernetes
      resource_definition_name: kubernetes-cluster
      version: 0.1.0
    produces: true
    role: cluster
    output_schema:
      type: object
      properties:
        cluster_name:
          type: string
        cluster_endpoint:
          type: string
      required:
        - cluster_name

Declaring production via ``deploy.resources``
++++++++++++++++++++++++++++++++++++++++++++++

``manifest.json``'s ``deploy.resources`` (BACON's canonical declaration location) is a second,
complementary way to declare that a ``RepoClassVersion`` **produces** a resource type -- it is
**not** an alternative place to define a type's schema. A ResourceDefinition's ``output_schema``,
``parent``/isa, ``description``, and ``resource_metadata`` are defined **only** via
``meta-data/resources/*.yaml`` above; that ingestion always runs, unconditionally. Each
``deploy.resources.<name>`` entry instead references an existing (or not-yet-registered) type
**by identity only**:

.. code-block:: json

    {
      "deploy": {
        "resources": {
          "cluster": {
            "resource_namespace": "aws",
            "resource_definition_name": "eks-cluster",
            "version": "0.1.0",
            "role": "cluster",
            "produces": true
          }
        }
      }
    }

Fields:

- ``resource_namespace``, ``resource_definition_name``, ``version`` -- required identity of the
  ResourceDefinition being produced.
- ``role`` -- optional role for the produces edge.
- ``produces`` -- optional boolean, **defaults to true** when omitted (per the BACON spec); an
  explicit ``false`` skips the entry.

If the referenced ResourceDefinition isn't registered yet (for example it's shipped by a
different repo, or a later artifact), a minimal stub is upserted so the identity exists and the
authoritative definition later reconciles into the same row -- the same tolerant
find-or-stub-upsert pattern used for consumer-side resource dependencies. Declaring the same
production through both a YAML file's ``produces: true`` and a ``deploy.resources`` entry is
harmless, since the produces edge is deduped per ``(RepoClassVersion, ResourceDefinition)``.

Base ResourceDefinitions
--------------------------

The service ships a small, standard catalog of **abstract, vendor-neutral base
ResourceDefinitions** -- the shared starting point for per-repo definitions. These are the
supertypes a repo's concrete definition ``parent`` s (with ``produces: true``), so that
capabilities line up across the platform: one repo's ``aws.neuronsphere.io/eks-cluster``
can satisfy another repo's requirement for ``kubernetes.neuronsphere.io/kubernetes-cluster``.

The base definitions are abstract -- they carry an ``output_schema`` and (where relevant)
a ``parent``, but **no** producer. They are namespaced as **fully-qualified DNS
subdomains under** ``neuronsphere.io`` (the Kubernetes CRD API-group convention), e.g.
``network.neuronsphere.io``, ``database.neuronsphere.io``. This keeps the catalog
unambiguously NeuronSphere-owned and lets customers extend the graph with their **own**
supertypes under their own domain (for example ``network.acme.com/vpc``) without
colliding.

The catalog (all at ``version: 0.1.0``):

.. list-table::
   :header-rows: 1
   :widths: 32 24 30

   * - namespace / name
     - parent
     - typical producers
   * - ``network.neuronsphere.io/network``
     -
     - *(abstract root)*
   * - ``network.neuronsphere.io/vpc``
     - ``network.neuronsphere.io/network``
     - ``hmd-vpc``
   * - ``network.neuronsphere.io/docker-network``
     - ``network.neuronsphere.io/network``
     - local Docker network (``hmd ns up``)
   * - ``network.neuronsphere.io/load-balancer``
     -
     - ``hmd-inf-eks-alb``, ``hmd-inf-api-gateway``
   * - ``network.neuronsphere.io/firewall``
     -
     - ``hmd-inf-wafv2``
   * - ``kubernetes.neuronsphere.io/kubernetes-cluster``
     -
     - ``hmd-inf-eks-cluster``
   * - ``kubernetes.neuronsphere.io/deployment``
     -
     - *(abstract root: any in-cluster workload)*
   * - ``kubernetes.neuronsphere.io/service``
     -
     - *(abstract root: in-cluster URL / internal DNS)*
   * - ``kubernetes.neuronsphere.io/operator``
     - ``kubernetes.neuronsphere.io/deployment``
     - ``hmd-inf-ext-secrets``, ``hmd-inf-clickhouse-operator``, ``hmd-inf-keda``
   * - ``kubernetes.neuronsphere.io/ingress-controller``
     - ``kubernetes.neuronsphere.io/operator``
     - ``hmd-inf-eks-alb``
   * - ``kubernetes.neuronsphere.io/custom-resource-definition``
     -
     - ``hmd-inf-ext-secrets-crds``
   * - ``compute.neuronsphere.io/compute-node``
     -
     - ``hmd-inf-eks-node-group``, ``hmd-inf-karpenter``
   * - ``database.neuronsphere.io/database``
     -
     - *(abstract root)*
   * - ``database.neuronsphere.io/postgres``
     - ``database.neuronsphere.io/database``
     - ``hmd-postgres-rds``, ``hmd-database-account``
   * - ``database.neuronsphere.io/graph``
     - ``database.neuronsphere.io/database``
     - ``hmd-inf-neptune``
   * - ``cache.neuronsphere.io/cache``
     -
     - ``hmd-inf-redis``
   * - ``storage.neuronsphere.io/object-store``
     -
     - ``hmd-inf-s3bucket``
   * - ``storage.neuronsphere.io/filesystem``
     -
     - ``hmd-inf-efs``
   * - ``credentials.neuronsphere.io/credential``
     -
     - ``hmd-inf-credentials``
   * - ``application.neuronsphere.io/application``
     -
     - ``hmd-app-*``, helm ``hmd-inf-*`` apps
   * - ``application.neuronsphere.io/microservice``
     - ``application.neuronsphere.io/application``
     - ``hmd-ms-*``
   * - ``data.neuronsphere.io/dataset``
     -
     - *(abstract root: data a product produces)*
   * - ``data.neuronsphere.io/data-model``
     -
     - *(abstract root: a set of model definitions, e.g. a dbt project)*
   * - ``data.neuronsphere.io/sql-table``
     - ``data.neuronsphere.io/dataset``
     - a customer's data-product classes
   * - ``data.neuronsphere.io/sql-view``
     - ``data.neuronsphere.io/sql-table``
     - a customer's data-product classes

Some categories provide a **generic supertype** so that a consumer can depend on a
capability rather than a specific implementation. ``network.neuronsphere.io/network`` is the
canonical example: both a cloud ``vpc`` and the local ``docker-network`` inherit from it, so
a RepoClass that requires ``network.neuronsphere.io/network`` is satisfied by a real VPC in
the cloud and by the Docker network when running locally under ``hmd ns up`` — the same
RepoClass can be exercised in both environments without changing its dependency.

The ``kubernetes.neuronsphere.io`` supertypes describe Kubernetes-native resources and form
a small inheritance chain: ``ingress-controller`` *isa* ``operator`` *isa* ``deployment``,
so an ``hmd-inf-eks-alb`` producing ``aws.neuronsphere.io/aws-load-balancer-controller``
(a subtype of ``ingress-controller``) satisfies a requirement for any of those supertypes.
``service`` and ``custom-resource-definition`` are standalone roots; ``service`` carries
an ``internal_dns`` output (``name.namespace.svc.cluster.local``) so consumers can
reference a cluster-internal URL of a produced workload (for example an operator's
admission webhook).

The ``data.neuronsphere.io`` supertypes (NERD0014) describe what the infrastructure
exists to hold. ``sql-view`` *isa* ``sql-table`` *isa* ``dataset``: every consumer that can
read a table can read a view, so a requirement on ``dataset`` is satisfied by a producer of
either. ``dataset``'s root field is ``location`` rather than ``schema`` because a dataset is
not always a SQL relation -- a Parquet prefix, a topic and a warehouse schema are all "where
consumers find it" -- and ``sql-table`` narrows it to the three-part name. ``data-model`` is
a separate root, deliberately not a ``dataset``: a dbt project is a set of definitions that,
when run, produce datasets, and a consumer that depends on ``data-model`` wants the
definitions to reference as sources, not the rows. A customer's ``data.acme.com/dataset``
becomes a subtype by adding ``parent``; nothing it already emits is renamed.

The catalog is bundled as YAML under ``hmd_ms_deployment_core/base_resources/`` and loaded by
the `Seed Base Resource Definitions`_ operation. To adopt resource-based dependencies, a
repo ships a concrete subtype in ``meta-data/resources/*.yaml`` that ``parent`` s the
relevant base type and declares ``produces: true`` -- for example:

.. code-block:: yaml

    resource_namespace: aws.neuronsphere.io
    resource_definition_name: eks-cluster
    version: 0.1.0
    description: An Amazon EKS Kubernetes cluster.
    parent:
      resource_namespace: kubernetes.neuronsphere.io
      resource_definition_name: kubernetes-cluster
      version: 0.1.0
    produces: true
    role: cluster
    output_schema:
      type: object
      properties:
        cluster_name:
          type: string

Resource-Based Dependencies
---------------------------

A ``RepoClassVersion`` can declare a dependency on a resource *type* (rather than a named
``RepoClass``), with resolution respecting inheritance -- a producer of a subtype (e.g.
``aws/eks-cluster``) satisfies a requirement for a supertype (e.g.
``kubernetes/kubernetes-cluster``) -- and honoring tag selectors (NERD0004 SPEC0008).

Declaring a resource dependency (``manifest.json``)
+++++++++++++++++++++++++++++++++++++++++++++++++++++

The change is **backward compatible**. A dependency role in ``deploy.dependencies`` keeps its
existing ``repo_class_name`` / ``required`` / ``version_spec`` properties and may add an optional
``resource`` object. When a role declares a ``resource``, the resource requirement is
**authoritative** and the ``repo_class_name`` (if present) becomes only a *suggestion*. Roles with
no ``resource`` behave exactly as before.

.. code-block:: json

   {
     "deploy": {
       "dependencies": {
         "cluster": {
           "repo_class_name": "hmd-eks-cluster",
           "required": "true",
           "resource": {
             "resource_namespace": "kubernetes",
             "resource_definition_name": "kubernetes-cluster",
             "version": "0.1.0",
             "version_spec": "~= 0.1",
             "tag_selector": "tier=prod,region=us-west-2"
           }
         }
       }
     }
   }

Ingesting this class version records two edges: a ``repo_class_version_req_repo_class`` edge (the
retained suggestion) and an authoritative ``repo_class_version_req_resource_definition`` edge
carrying ``required``, ``version_spec``, ``role``, and ``tag_selector``.

Resolution and validation
++++++++++++++++++++++++++

When a changeset is applied, the operator supplies the satisfying instance for a resource-based
role just as for a class-name role. The service **validates** that the supplied instance produces a
compatible resource type -- the required definition or a subtype, honoring ``version_spec`` and the
``tag_selector`` -- and otherwise rejects the changeset. The resolved dependency feeds the existing
``repo_instance_req_repo_instance`` edges, so the deployment DAG builder is unchanged.

Discovering candidates
++++++++++++++++++++++++

``GET /apiop/suggest_resource_dependencies/<env-type>`` auto-resolves the instances in an
environment that could satisfy a requirement, so an operator or agent can discover what to supply.
Query by consumer -- ``?repo_class_version_id=<id>`` returns candidates per required role (and the
retained ``repo_class_name`` suggestion) -- or ad-hoc with
``?resource_namespace=..&resource_definition_name=..&version=..`` plus optional ``version_spec`` and
``tags`` (``key=value,key=value``).
