"""Variables for robot_architecture.robot (NERD036, hmd-cli-neuronsphere).

Every name carries the run id, so the suite can run against a control plane
whose database persists across runs, and it asserts only on what it creates.
"""

import os
import time

_RUN_ID = os.environ.get("ROBOT_RUN_ID") or time.strftime("%m%d%H%M%S")

PRODUCER_CLASS = f"arch-s3bucket-{_RUN_ID}"
CONSUMER_CLASS = f"arch-librarian-{_RUN_ID}"
TEMPLATE_NAME = f"arch-template-{_RUN_ID}"
VIEW_NAME = f"arch-view-{_RUN_ID}"


def _aws(name):
    return {
        "resource_namespace": "aws.neuronsphere.io",
        "resource_definition_name": name,
        "version": "0.1.0",
    }


# The producer: an S3 bucket, as hmd-inf-s3bucket declares it.
producer_resources = {"bucket": {**_aws("s3-bucket"), "produces": True}}

add_producer = {
    "repo_class_name": PRODUCER_CLASS,
    "version": "0.1.0",
    "dependencies": {},
    "default_configuration": {},
    "resources": producer_resources,
}

# The consumer: a librarian. Its own REST API, function and table, and the
# bucket it reads through its lib-repo role.
consumer_resources = {
    "api": {
        **_aws("api-gateway-rest-api"),
        "produces": True,
        "metadata": {"connects_to": ["function"]},
    },
    "function": {
        **_aws("lambda-function"),
        "produces": True,
        "metadata": {"connects_to": ["entity-table", "dep:lib-repo"]},
    },
    "entity-table": {**_aws("dynamodb-table"), "produces": True},
}

add_consumer = {
    "repo_class_name": CONSUMER_CLASS,
    "version": "0.1.0",
    "dependencies": {
        "lib-repo": {"repo_class_name": PRODUCER_CLASS, "required": "true"},
    },
    "default_configuration": {},
    "resources": consumer_resources,
}

# Resync with a changed declaration: the table moves to a retry queue.
resync_consumer = {
    "repo_class_name": CONSUMER_CLASS,
    "version": "0.1.0",
    "dependencies": add_consumer["dependencies"],
    "resources": {
        **consumer_resources,
        "retry-queue": {**_aws("sqs-queue"), "produces": True},
    },
}

template_descriptor = {
    "name": TEMPLATE_NAME,
    "requires_schema": "architecture.neuronsphere.io/v1",
    "outputs": [
        {"path": "architecture.puml", "media_type": "text/x-plantuml", "primary": True}
    ],
    "parameters": {
        "type": "object",
        "properties": {"audience": {"type": "string", "default": "customer"}},
    },
    "scope_defaults": {"depth": 1},
}


def _template(version):
    return {
        "template_name": TEMPLATE_NAME,
        "version": version,
        "title": "Robot template",
        "descriptor": template_descriptor,
        "artifact_location": f"{TEMPLATE_NAME}@{version}:template",
    }


register_template_010 = _template("0.1.0")
register_template_020 = _template("0.2.0")

view = {
    "view_name": VIEW_NAME,
    "title": "Robot view",
    "roots": [CONSUMER_CLASS],
    "depth": 1,
    "template_name": TEMPLATE_NAME,
    "template_version_spec": "~=0.1",
    "params": {"audience": "customer"},
}

view_updated = {**view, "template_version_spec": "~=0.2"}

model_request = {"roots": [CONSUMER_CLASS], "depth": 1}

CONSUMER_NODE = f"rc:{CONSUMER_CLASS}"
PRODUCER_NODE = f"rc:{PRODUCER_CLASS}"
API_NODE = f"res:{CONSUMER_CLASS}/api"
FUNCTION_NODE = f"res:{CONSUMER_CLASS}/function"
TABLE_NODE = f"res:{CONSUMER_CLASS}/entity-table"
BUCKET_NODE = f"res:{PRODUCER_CLASS}/bucket"

# A template that renders: one PlantUML file over the model's nodes and flows.
_PUML = """@startuml
title {{ vars.title | default('Architecture') }}
{% for n in nodes %}rectangle "{{ n.label }}" as {{ n.id | replace(':', '_') | replace('/', '_') | replace('-', '_') }}
{% endfor %}{% for e in edges if e.kind in ('flows_to', 'depends_on') %}{{ e['from'] | replace(':', '_') | replace('/', '_') | replace('-', '_') }} --> {{ e.to | replace(':', '_') | replace('/', '_') | replace('-', '_') }}
{% endfor %}' audience: {{ vars.audience }}
@enduml
"""

register_template_030 = {
    **_template("0.3.0"),
    "files": {"architecture.puml.j2": _PUML},
}

render_request = {
    "roots": [CONSUMER_CLASS],
    "depth": 1,
    "template_name": TEMPLATE_NAME,
    "template_version_spec": "~=0.3",
    "params": {"audience": "customer", "title": "Robot render"},
}

RENDER_VIEW_NAME = f"arch-render-view-{_RUN_ID}"

render_view = {
    "view_name": RENDER_VIEW_NAME,
    "roots": [CONSUMER_CLASS],
    "depth": 1,
    "template_name": TEMPLATE_NAME,
    "template_version_spec": "~=0.3",
    "params": {"audience": "customer"},
}
