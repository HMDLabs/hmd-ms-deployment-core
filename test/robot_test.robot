*** Settings ***
Library           String
Library           Collections
Library           BuiltIn
Library           hmd_lib_robot_shared.basic_client_robot_lib.BasicClientLib    ${HMD_INSTANCE_NAME}    ${HMD_REPO_NAME}    ${HMD_DID}    ${HMD_ENVIRONMENT}    ${HMD_REGION}    ${HMD_CUSTOMER_CODE}    ${HMD_ACCOUNT}
Variables         vars_robot.py
Suite Setup       clear service db    ${gozer_instance_name}    ${pre_test_db_clear}

*** Variables ***
&{environment_data}    type=dev    account_number=1234    hmd_region=reg1
&{repo_class1_data}    repo_class_name=a_repo_class
&{repo_version11_data}    version=0.1.1
&{repo_version12_data}    version=0.1.2
&{repo_class2_data}    repo_class_name=a_repo_class2
&{repo_version21_data}    version=0.1.1
&{repo_version22_data}    version=0.1.2
&{repo_instance_1_data}    name=ri1    auto_deploy=false
&{repo_inst_depl_11_data}    deployment_id=aaa    status=DEPLOY_NEXT
&{repo_inst_depl_12_data}    deployment_id=aaa    status=DEPLOYED
&{repo_instance_2_data}    name=ri1    auto_deploy=false
&{repo_inst_depl_21_data}    deployment_id=aaa    status=DEPLOY_NEXT
&{repo_inst_depl_22_data}    deployment_id=aaa    status=DEPLOYED
&{current_true}    current=true
&{current_false}    current=false
&{role_blah}      role=blah

*** Test Cases ***
Create Some Data
    ${environment}=    create and upsert an entity    hmd_lang_deployment.environment    ${environment_data}
    ${repo_class_1}=    create and upsert an entity    hmd_lang_deployment.repo_class    ${repo_class1_data}
    ${repo_version_11}=    create and upsert an entity    hmd_lang_deployment.repo_class_version    ${repo_version11_data}
    create and upsert a relationship    hmd_lang_deployment.repo_class_has_repo_class_version    ${repo_class_1}    ${repo_version_11}
    ${repo_version_12}=    create and upsert an entity    hmd_lang_deployment.repo_class_version    ${repo_version12_data}
    create and upsert a relationship    hmd_lang_deployment.repo_class_has_repo_class_version    ${repo_class_1}    ${repo_version_12}
    ${repo_inst_1}=    create and upsert an entity    hmd_lang_deployment.repo_instance    ${repo_instance_1_data}
    ${repo_inst_depl_11}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${repo_inst_depl_11_data}
    ${repo_inst_depl_12}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${repo_inst_depl_12_data}
    create and upsert a relationship    hmd_lang_deployment.environment_has_repo_instance    ${environment}    ${repo_inst_1}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_isa_repo_class    ${repo_inst_1}    ${repo_class_1}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${repo_inst_1}    ${repo_inst_depl_11}    ${current_false}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${repo_inst_1}    ${repo_inst_depl_12}    ${current_true}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_deployment_has_repo_class_version    ${repo_inst_depl_11}    ${repo_version_11}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_deployment_has_repo_class_version    ${repo_inst_depl_12}    ${repo_version_12}
    ${repo_class_2}=    create and upsert an entity    hmd_lang_deployment.repo_class    ${repo_class2_data}
    ${repo_version_21}=    create and upsert an entity    hmd_lang_deployment.repo_class_version    ${repo_version21_data}
    ${rc_rv_2}=    create and upsert a relationship    hmd_lang_deployment.repo_class_has_repo_class_version    ${repo_class_2}    ${repo_version_21}
    ${repo_version_22}=    create and upsert an entity    hmd_lang_deployment.repo_class_version    ${repo_version22_data}
    ${rc_rv_22}=    create and upsert a relationship    hmd_lang_deployment.repo_class_has_repo_class_version    ${repo_class_2}    ${repo_version_22}
    ${repo_inst_2}=    create and upsert an entity    hmd_lang_deployment.repo_instance    ${repo_instance_1_data}
    ${repo_inst_depl_21}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${repo_inst_depl_21_data}
    ${repo_inst_depl_22}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${repo_inst_depl_22_data}
    create and upsert a relationship    hmd_lang_deployment.environment_has_repo_instance    ${environment}    ${repo_inst_2}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_isa_repo_class    ${repo_inst_2}    ${repo_class_2}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${repo_inst_2}    ${repo_inst_depl_21}    ${current_false}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${repo_inst_2}    ${repo_inst_depl_22}    ${current_true}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_deployment_has_repo_class_version    ${repo_inst_depl_21}    ${repo_version_21}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_deployment_has_repo_class_version    ${repo_inst_depl_22}    ${repo_version_22}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_req_repo_instance    ${repo_inst_1}    ${repo_inst_2}    ${role_blah}

Pull BOM
    ${BOM}=    invoke custom operation    path=get_deployment_bom/dev    data=&{EMPTY}    method=GET
    should be equal    ${BOM}    ${bom_results}

Add RepoClass
    invoke custom operation    path=add_repo_class_version    data=&{add_rc_1}    method=POST
    ${results}=    search an entity    entity_name=hmd_lang_deployment.repo_class    filter_=&{add_rc_1_filter}
    length should be    ${results}    1
    ${versions}=    get from relationships    ${results[0]}    hmd_lang_deployment.repo_class_has_repo_class_version
    length should be    ${versions}    1
    should be equal    ${versions[0].version}    1.2.3
    ${notes}=    get from relationships    ${versions[0]}    hmd_lang_deployment.repo_class_version_has_repo_class_version_notes
    length should be    ${notes}    1
    should be equal    ${notes[0].version_notes}    A version note.

Manage Resource Definitions
    # Upsert a parent and a child (inheriting) resource definition.
    ${k8s}=    invoke custom operation    path=upsert_resource_definition    data=&{resource_def_k8s}    method=POST
    ${eks}=    invoke custom operation    path=upsert_resource_definition    data=&{resource_def_eks}    method=POST
    ${eks_id}=    get from dictionary    ${eks}    identifier
    # Fetch the child back by identifier.
    ${fetched}=    invoke custom operation    path=get_resource_definition/${eks_id}    data=&{EMPTY}    method=GET
    should be equal    ${fetched['resource_definition_name']}    eks-cluster
    # List includes both definitions.
    ${all}=    invoke custom operation    path=list_resource_definitions    data=&{EMPTY}    method=GET
    length should be    ${all}    2
    # The child's isa ancestry is returned root-first (kubernetes-cluster, eks-cluster).
    ${ancestry}=    invoke custom operation    path=get_resource_definition_ancestry/${eks_id}    data=&{EMPTY}    method=GET
    length should be    ${ancestry}    2
    should be equal    ${ancestry[0]['resource_definition_name']}    kubernetes-cluster
    should be equal    ${ancestry[1]['resource_definition_name']}    eks-cluster
    # The effective output schema merges the parent's and child's schemas.
    ${eff}=    invoke custom operation    path=get_effective_output_schema/${eks_id}    data=&{EMPTY}    method=GET
    dictionary should contain key    ${eff['properties']}    cluster_name
    dictionary should contain key    ${eff['properties']}    cluster_arn

Submit And Query Resources
    # A deployment to attach resources to.
    ${rid}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${resource_inst_depl_data}
    &{submission}=    create dictionary    repo_instance_deployment_id=${rid.identifier}    resources=${resource_submission_list}
    ${submitted}=    invoke custom operation    path=submit_resources    data=&{submission}    method=POST
    length should be    ${submitted}    1
    # The deployment now reports its resources.
    ${dep_resources}=    invoke custom operation    path=get_deployment_resources/${rid.identifier}    data=&{EMPTY}    method=GET
    length should be    ${dep_resources}    1
    should be equal    ${dep_resources[0]['resource_name']}    prod-eks
    # The resource is discoverable by its searchable tag.
    ${by_tag}=    invoke custom operation    path=find_resources_by_tag/env/prod    data=&{EMPTY}    method=GET
    length should be    ${by_tag}    1
    should be equal    ${by_tag[0]['resource_name']}    prod-eks
    # The resource is also listed unfiltered via the paginated list_resources envelope.
    ${listed}=    invoke custom operation    path=list_resources    data=&{EMPTY}    method=GET
    dictionary should contain key    ${listed}    items
    dictionary should contain key    ${listed}    total
    should be equal as integers    ${listed['total']}    1
    should be equal as integers    ${listed['limit']}    50
    should be equal as integers    ${listed['offset']}    0
    length should be    ${listed['items']}    1
    should be equal    ${listed['items'][0]['resource_name']}    prod-eks
    # Pagination: limit=0 window yields no items but still reports the full total.
    ${empty_page}=    invoke custom operation    path=list_resources?limit=0    data=&{EMPTY}    method=GET
    length should be    ${empty_page['items']}    0
    should be equal as integers    ${empty_page['total']}    1
    # Environment scoping: the resource hangs off no environment here, so an
    # environment-scoped list excludes it (walks environment->instance->deployment).
    ${scoped}=    invoke custom operation    path=list_resources?environment=no-such-env    data=&{EMPTY}    method=GET
    should be equal as integers    ${scoped['total']}    0
    length should be    ${scoped['items']}    0

Add RepoClass With Resource Dependency
    # NERD0004 SPEC0008: a consumer class version declaring a resource-type
    # dependency (the kubernetes-cluster definition was created above).
    invoke custom operation    path=add_repo_class_version    data=&{add_rc_resource_dep}    method=POST
    ${results}=    search an entity    entity_name=hmd_lang_deployment.repo_class    filter_=&{add_rc_resource_dep_filter}
    length should be    ${results}    1
    ${versions}=    get from relationships    ${results[0]}    hmd_lang_deployment.repo_class_has_repo_class_version
    length should be    ${versions}    1
    # The class-name dependency is retained as a suggestion.
    ${class_reqs}=    get from relationships    ${versions[0]}    hmd_lang_deployment.repo_class_version_req_repo_class
    length should be    ${class_reqs}    1
    should be equal    ${class_reqs[0].role}    cluster
    # The resource-type dependency is authoritative and carries the version_spec/selector.
    ${res_reqs}=    get from relationships    ${versions[0]}    hmd_lang_deployment.repo_class_version_req_resource_definition
    length should be    ${res_reqs}    1
    should be equal    ${res_reqs[0].role}    cluster
    should be equal    ${res_reqs[0].version_spec}    ~= 0.1
    should be equal    ${res_reqs[0].tag_selector}    tier=prod

Seed Base Catalog And Produce Kubernetes Resource
    # Seed the bundled base catalog (idempotent), including the K8s-native supertypes.
    invoke custom operation    path=seed_base_resource_definitions    data=&{EMPTY}    method=POST
    # A concrete controller parenting the seeded base ingress-controller resolves its
    # full inheritance chain: deployment -> operator -> ingress-controller -> itself.
    ${controller}=    invoke custom operation    path=upsert_resource_definition    data=&{resource_def_alb_controller}    method=POST
    ${ctrl_id}=    get from dictionary    ${controller}    identifier
    ${ancestry}=    invoke custom operation    path=get_resource_definition_ancestry/${ctrl_id}    data=&{EMPTY}    method=GET
    length should be    ${ancestry}    4
    should be equal    ${ancestry[0]['resource_definition_name']}    deployment
    should be equal    ${ancestry[1]['resource_definition_name']}    operator
    should be equal    ${ancestry[2]['resource_definition_name']}    ingress-controller
    should be equal    ${ancestry[3]['resource_definition_name']}    aws-load-balancer-controller
    # The effective schema merges the whole chain (deployment + operator + ingress-controller).
    ${eff}=    invoke custom operation    path=get_effective_output_schema/${ctrl_id}    data=&{EMPTY}    method=GET
    dictionary should contain key    ${eff['properties']}    name
    dictionary should contain key    ${eff['properties']}    ingress_class
    dictionary should contain key    ${eff['properties']}    irsa_role_arn
    # A deployment produces the controller Resource; the rendered output validates and round-trips.
    ${rid}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${alb_controller_inst_depl_data}
    &{submission}=    create dictionary    repo_instance_deployment_id=${rid.identifier}    resources=${alb_controller_submission_list}
    ${submitted}=    invoke custom operation    path=submit_resources    data=&{submission}    method=POST
    length should be    ${submitted}    1
    ${dep_resources}=    invoke custom operation    path=get_deployment_resources/${rid.identifier}    data=&{EMPTY}    method=GET
    length should be    ${dep_resources}    1
    should be equal    ${dep_resources[0]['resource_name']}    alb-controller
    should be equal    ${dep_resources[0]['output']['ingress_class']}    alb

Environment Scoping Excludes Other Environments And Superseded Deployments
    # Two environments, each with its own instance and a current DEPLOYED deployment.
    ${dev_env}=    search an entity    entity_name=hmd_lang_deployment.environment    filter_=&{scoping_dev_environment_filter}
    length should be    ${dev_env}    1
    ${admin_env}=    create and upsert an entity    hmd_lang_deployment.environment    ${scoping_admin_environment_data}
    ${dev_ri}=    create and upsert an entity    hmd_lang_deployment.repo_instance    ${scoping_dev_instance_data}
    ${admin_ri}=    create and upsert an entity    hmd_lang_deployment.repo_instance    ${scoping_admin_instance_data}
    create and upsert a relationship    hmd_lang_deployment.environment_has_repo_instance    ${dev_env[0]}    ${dev_ri}
    create and upsert a relationship    hmd_lang_deployment.environment_has_repo_instance    ${admin_env}    ${admin_ri}
    # The dev instance has been deployed twice. The superseded deployment keeps
    # status DEPLOYED -- only `current` distinguishes it from the live one.
    ${dev_old}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${scoping_dev_old_depl_data}
    ${dev_new}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${scoping_dev_new_depl_data}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${dev_ri}    ${dev_old}    ${current_false}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${dev_ri}    ${dev_new}    ${current_true}
    ${admin_depl}=    create and upsert an entity    hmd_lang_deployment.repo_instance_deployment    ${scoping_admin_depl_data}
    create and upsert a relationship    hmd_lang_deployment.repo_instance_has_repo_instance_deployment    ${admin_ri}    ${admin_depl}    ${current_true}
    # Each deployment produces its resources; both dev deployments produce the same name.
    &{dev_old_submission}=    create dictionary    repo_instance_deployment_id=${dev_old.identifier}    resources=${scoping_dev_submission_list}
    invoke custom operation    path=submit_resources    data=&{dev_old_submission}    method=POST
    &{dev_new_submission}=    create dictionary    repo_instance_deployment_id=${dev_new.identifier}    resources=${scoping_dev_submission_list}
    invoke custom operation    path=submit_resources    data=&{dev_new_submission}    method=POST
    &{admin_submission}=    create dictionary    repo_instance_deployment_id=${admin_depl.identifier}    resources=${scoping_admin_submission_list}
    invoke custom operation    path=submit_resources    data=&{admin_submission}    method=POST
    # dev lists its resource exactly once (the superseded deployment's copy is not
    # current) and never the admin environment's.
    ${dev_listed}=    invoke custom operation    path=list_resources?environment=dev    data=&{EMPTY}    method=GET
    ${dev_names}=    evaluate    [r["resource_name"] for r in $dev_listed["items"]]
    ${dev_count}=    evaluate    $dev_names.count("scoping-dev-cluster")
    should be equal as integers    ${dev_count}    1
    list should not contain value    ${dev_names}    scoping-admin-cluster
    # ...and admin lists its own, never dev's.
    ${admin_listed}=    invoke custom operation    path=list_resources?environment=admin    data=&{EMPTY}    method=GET
    ${admin_names}=    evaluate    [r["resource_name"] for r in $admin_listed["items"]]
    list should contain value    ${admin_names}    scoping-admin-cluster
    list should not contain value    ${admin_names}    scoping-dev-cluster
    # The unscoped listing still sees every row, including the superseded copy.
    ${all_listed}=    invoke custom operation    path=list_resources    data=&{EMPTY}    method=GET
    ${all_names}=    evaluate    [r["resource_name"] for r in $all_listed["items"]]
    ${all_count}=    evaluate    $all_names.count("scoping-dev-cluster")
    should be equal as integers    ${all_count}    2

*** Keywords ***
Create and Upsert an Entity
    [Arguments]    ${entity_name}    ${entity_data}
    ${entity}=    Create an Entity    ${entity_name}    ${entity_data}
    ${entity}=    Upsert an Entity    ${entity}
    [Return]    ${entity}

Create and Upsert a Relationship
    [Arguments]    ${relationship_name}    ${from}    ${to}    ${rel_data}=&{EMPTY}
    &{entity_data}=    create dictionary    ref_from=${from.identifier}    ref_to=${to.identifier}    &{rel_data}
    ${entity}=    Create an Entity    ${relationship_name}    ${entity_data}
    ${entity}=    Upsert an Entity    ${entity}
    [Return]    ${entity}
