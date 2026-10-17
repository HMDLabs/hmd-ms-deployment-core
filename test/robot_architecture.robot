*** Settings ***
Documentation     NERD036 (hmd-cli-neuronsphere): architecture views. The AWS resource
...               types a service creates, the resources a class version declares, rendering
...               templates, saved views and the architecture model.
...               Safe against a persistent control plane: nothing is cleared, every name
...               carries the run id, and only what this suite creates is asserted on.
Library           String
Library           Collections
Library           BuiltIn
Library           hmd_lib_robot_shared.basic_client_robot_lib.BasicClientLib    ${HMD_INSTANCE_NAME}    ${HMD_REPO_NAME}    ${HMD_DID}    ${HMD_ENVIRONMENT}    ${HMD_REGION}    ${HMD_CUSTOMER_CODE}    ${HMD_ACCOUNT}
Variables         vars_architecture.py

*** Test Cases ***
Base Catalog Carries The AWS Service Types
    [Documentation]    A service's REST API, function, table, queue and bucket have definitions,
    ...    each a subtype of a neutral type.
    invoke custom operation    path=seed_base_resource_definitions    data=&{EMPTY}    method=POST
    Ancestry Should Be    s3-bucket    object-store    s3-bucket
    Ancestry Should Be    api-gateway-rest-api    api-gateway    api-gateway-rest-api
    Ancestry Should Be    lambda-function    function    lambda-function
    Ancestry Should Be    dynamodb-table    database    key-value-table    dynamodb-table
    Ancestry Should Be    sqs-queue    queue    sqs-queue

A Class Version Keeps Its Declared Resources
    [Documentation]    deploy.resources is kept as declared: names and metadata.connects_to,
    ...    which the produces edges cannot hold.
    invoke custom operation    path=add_repo_class_version    data=&{add_producer}    method=POST
    invoke custom operation    path=add_repo_class_version    data=&{add_consumer}    method=POST
    ${detail}=    invoke custom operation    path=get_repo_class_version_detail/${CONSUMER_CLASS}/0.1.0    data=&{EMPTY}    method=GET
    dictionary should contain key    ${detail}    resources
    should be equal    ${detail['resources']}    ${consumer_resources}
    ${flows}=    get from dictionary    ${detail['resources']['function']['metadata']}    connects_to
    should be equal    ${flows}    ${{["entity-table", "dep:lib-repo"]}}

Resync Refreshes Declared Resources
    invoke custom operation    path=resync_repo_class_version_dependencies    data=&{resync_consumer}    method=POST
    ${detail}=    invoke custom operation    path=get_repo_class_version_detail/${CONSUMER_CLASS}/0.1.0    data=&{EMPTY}    method=GET
    dictionary should contain key    ${detail['resources']}    retry-queue

Rendering Templates Register Side By Side
    [Documentation]    Two versions of one template coexist; each is fetched by name and version.
    invoke custom operation    path=register_rendering_template    data=&{register_template_010}    method=POST
    invoke custom operation    path=register_rendering_template    data=&{register_template_020}    method=POST
    # Registering the same version again replaces it rather than adding a third.
    invoke custom operation    path=register_rendering_template    data=&{register_template_020}    method=POST
    ${all}=    invoke custom operation    path=list_rendering_templates    data=&{EMPTY}    method=GET
    ${ours}=    Templates Named    ${all}    ${TEMPLATE_NAME}
    length should be    ${ours}    2
    ${one}=    invoke custom operation    path=get_rendering_template/${TEMPLATE_NAME}/0.2.0    data=&{EMPTY}    method=GET
    should be equal    ${one['version']}    0.2.0
    should be equal    ${one['descriptor']['requires_schema']}    architecture.neuronsphere.io/v1

Architecture Views Are Saved By Name
    invoke custom operation    path=upsert_architecture_view    data=&{view}    method=POST
    invoke custom operation    path=upsert_architecture_view    data=&{view_updated}    method=POST
    ${all}=    invoke custom operation    path=list_architecture_views    data=&{EMPTY}    method=GET
    ${ours}=    evaluate    (lambda views, name: [v for v in views if v['view_name'] == name])($all, $VIEW_NAME)
    length should be    ${ours}    1
    should be equal    ${ours[0]['template_version_spec']}    ~=0.2
    should be equal    ${ours[0]['roots']}    ${{[$CONSUMER_CLASS]}}

Architecture Model Of A Class
    [Documentation]    The model of the consumer: both classes, the consumer's role edge to the
    ...    producer, every declared resource, and flows_to edges from connects_to, with
    ...    dep:lib-repo resolved to the bucket the producer declares.
    ${model}=    invoke custom operation    path=get_architecture_model    data=&{model_request}    method=POST
    should be equal    ${model['schema']}    architecture.neuronsphere.io/v1
    ${ids}=    evaluate    [n['id'] for n in $model['nodes']]
    FOR    ${expected}    IN    ${CONSUMER_NODE}    ${PRODUCER_NODE}    ${API_NODE}    ${FUNCTION_NODE}    ${TABLE_NODE}    ${BUCKET_NODE}
        list should contain value    ${ids}    ${expected}
    END
    ${api}=    evaluate    (lambda nodes, id_: next(n for n in nodes if n['id'] == id_))($model['nodes'], $API_NODE)
    should be equal    ${api['resource_definition']}    aws.neuronsphere.io/api-gateway-rest-api@0.1.0
    should be equal    ${api['group']}    ${CONSUMER_NODE}
    Model Should Have Edge    ${model}    ${CONSUMER_NODE}    ${PRODUCER_NODE}    depends_on
    Model Should Have Edge    ${model}    ${API_NODE}    ${FUNCTION_NODE}    flows_to
    Model Should Have Edge    ${model}    ${FUNCTION_NODE}    ${TABLE_NODE}    flows_to
    Model Should Have Edge    ${model}    ${FUNCTION_NODE}    ${BUCKET_NODE}    flows_to
    ${role}=    evaluate    (lambda edges, src: next(e['role'] for e in edges if e['kind'] == 'depends_on' and e['from'] == src))($model['edges'], $CONSUMER_NODE)
    should be equal    ${role}    lib-repo
    # Nodes and edges are sorted by id, so equal scopes give byte-identical models.
    ${sorted}=    evaluate    $ids == sorted($ids)
    should be true    ${sorted}

*** Keywords ***
Ancestry Should Be
    [Arguments]    ${name}    @{expected}
    ${all}=    invoke custom operation    path=list_resource_definitions    data=&{EMPTY}    method=GET
    ${rd}=    evaluate    (lambda defs, name: next(d for d in defs if d['resource_namespace'] == 'aws.neuronsphere.io' and d['resource_definition_name'] == name))($all, $name)
    ${ancestry}=    invoke custom operation    path=get_resource_definition_ancestry/${rd['identifier']}    data=&{EMPTY}    method=GET
    ${names}=    evaluate    [a['resource_definition_name'] for a in $ancestry]
    should be equal    ${names}    ${expected}

Templates Named
    [Arguments]    ${templates}    ${name}
    ${ours}=    evaluate    (lambda templates, name: [t for t in templates if t['template_name'] == name])($templates, $name)
    RETURN    ${ours}

Model Should Have Edge
    [Arguments]    ${model}    ${from}    ${to}    ${kind}
    ${found}=    evaluate    (lambda edges, src, dst, kind: any(e['from'] == src and e['to'] == dst and e['kind'] == kind for e in edges))($model['edges'], $from, $to, $kind)
    should be true    ${found}    no ${kind} edge ${from} -> ${to}
