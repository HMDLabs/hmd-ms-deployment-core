*** Settings ***
Documentation     Registry, catalogue and resolver tests for the deployment service core (hmd-ms-deployment-core)
...               Tests cover environment setup, RepoClass management, ChangeSet creation,
...               deployment execution, status tracking, and NERD0002 resource name formats.
Library           String
Library           Collections
Library           BuiltIn
Library           hmd_lib_robot_shared.basic_client_robot_lib.BasicClientLib    ${HMD_INSTANCE_NAME}    ${HMD_REPO_NAME}    ${HMD_DID}    ${HMD_ENVIRONMENT}    ${HMD_REGION}    ${HMD_CUSTOMER_CODE}    ${HMD_ACCOUNT}
Variables         vars_robot.py
Test Setup        Log Test Configuration
Suite Teardown    Cleanup Test Data

*** Variables ***
${TEST_ENVIRONMENT}      ${EMPTY}
${TEST_DEPLOYMENT_SET}   ${EMPTY}
${VPC_REPO_CLASS}        ${EMPTY}
${DATABASE_REPO_CLASS}   ${EMPTY}
${MONITORING_REPO_CLASS}   ${EMPTY}
${VPC_RID}               ${EMPTY}

*** Test Cases ***
# =============================================================================
# Suite 1: Environment and DeploymentSet Setup
# =============================================================================

Test 1.1 - Create Test Environment
    [Documentation]    Create a test environment with current AWS account and region
    [Tags]    setup    environment

    Log    Creating environment with account: ${test_environment_data}[account_number]
    Log    AWS Region: ${test_environment_data}[hmd_region]

    ${environment}=    Create and Upsert an Entity    hmd_lang_deployment.environment    ${test_environment_data}
    Set Suite Variable    ${TEST_ENVIRONMENT}    ${environment}

    # Verify environment can be retrieved
    ${environments}=    Search an Entity    entity_name=hmd_lang_deployment.environment    filter_=&{filter_environment}
    Length Should Be    ${environments}    1
    Should Be Equal    ${environments[0].type}    ${ENVIRONMENT_TYPE}
    Should Be Equal    ${environments[0].account_number}    ${test_environment_data}[account_number]

    Log    Environment created successfully: ${environment.identifier}

Test 1.2 - Create DeploymentSet for Testing
    [Documentation]    Create a DeploymentSet referencing the test environment
    [Tags]    setup    deploymentset

    ${deployment_set}=    Create and Upsert an Entity    hmd_lang_deployment.deployment_set    ${deployment_set_definition}
    Set Suite Variable    ${TEST_DEPLOYMENT_SET}    ${deployment_set}

    # Verify DeploymentSet can be retrieved
    ${deployment_sets}=    Search an Entity    entity_name=hmd_lang_deployment.deployment_set    filter_=&{filter_deployment_set}
    Length Should Be    ${deployment_sets}    1
    Should Be Equal    ${deployment_sets[0].name}    ${DEPLOYMENT_SET_NAME}

    # Verify definition includes test environment
    ${definition}=    Set Variable    ${deployment_sets[0].definition}
    Length Should Be    ${definition}    1
    Should Be Equal    ${definition[0]}[environment]    ${ENVIRONMENT_TYPE}

    Log    DeploymentSet created successfully: ${deployment_set.identifier}

# =============================================================================
# Suite 2: RepoClass and Version Management
# =============================================================================

Test 2.1 - Register VPC RepoClass Version 1.0.0
    [Documentation]    Register test-vpc RepoClass version 1.0.0 via add_repo_class_version
    [Tags]    repoclass    registration

    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_vpc_v1}    method=POST

    # Verify RepoClass was created
    ${repo_classes}=    Search an Entity    entity_name=hmd_lang_deployment.repo_class    filter_=&{filter_test_vpc}
    Length Should Be    ${repo_classes}    1
    Set Suite Variable    ${VPC_REPO_CLASS}    ${repo_classes[0]}
    Should Be Equal    ${repo_classes[0].repo_class_name}    test-vpc

    # Verify RepoClassVersion exists
    ${versions}=    Get From Relationships    ${repo_classes[0]}    hmd_lang_deployment.repo_class_has_repo_class_version
    ${v1}=    Version Entity    ${versions}    1.0.0
    Should Not Be Empty    ${v1.default_configuration}

    # Verify version notes
    ${notes}=    Get From Relationships    ${v1}    hmd_lang_deployment.repo_class_version_has_repo_class_version_notes
    Should Be True    len($notes) >= 1
    Should Be Equal    ${notes[0].version_notes}    Initial VPC for robot testing

    Log    VPC RepoClass registered successfully

Test 2.2 - Register Database RepoClass with VPC Dependency
    [Documentation]    Register test-database RepoClass with dependency on test-vpc
    [Tags]    repoclass    registration    dependencies

    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_database_v1}    method=POST

    # Verify RepoClass was created
    ${repo_classes}=    Search an Entity    entity_name=hmd_lang_deployment.repo_class    filter_=&{filter_test_database}
    Length Should Be    ${repo_classes}    1
    Set Suite Variable    ${DATABASE_REPO_CLASS}    ${repo_classes[0]}

    # Verify dependency on VPC
    ${versions}=    Get From Relationships    ${repo_classes[0]}    hmd_lang_deployment.repo_class_has_repo_class_version
    ${v1}=    Version Entity    ${versions}    1.0.0

    # Get From Relationships resolves to the target nouns, so the edge's own
    # attributes (role, required, version_spec) are read through the detail op.
    ${dependencies}=    Get From Relationships    ${v1}    hmd_lang_deployment.repo_class_version_req_repo_class
    Length Should Be    ${dependencies}    1
    Should Be Equal    ${dependencies[0].repo_class_name}    test-vpc
    ${detail}=    Invoke Custom Operation    path=get_repo_class_version_detail/test-database/1.0.0    data=&{EMPTY}    method=GET
    Should Be Equal    ${detail}[dependencies][vpc][repo_class_name]    test-vpc
    Should Be Equal    ${detail}[dependencies][vpc][required]    true
    Should Be Equal    ${detail}[dependencies][vpc][version_spec]    ~= 1.0

    Log    Database RepoClass with VPC dependency registered successfully

Test 2.3 - Register Monitoring RepoClass
    [Documentation]    Register test-monitoring RepoClass (no dependencies)
    [Tags]    repoclass    registration    nerd0013

    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_monitoring_v1}    method=POST

    ${repo_classes}=    Search an Entity    entity_name=hmd_lang_deployment.repo_class    filter_=&{filter_test_monitoring}
    Length Should Be    ${repo_classes}    1
    Set Suite Variable    ${MONITORING_REPO_CLASS}    ${repo_classes[0]}

    Log    Monitoring RepoClass registered successfully

Test 2.4 - Register VPC Version 1.1.0
    [Documentation]    Register second version of test-vpc RepoClass
    [Tags]    repoclass    versioning    nerd0013

    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_vpc_v1_1}    method=POST

    # Verify same RepoClass now has 2 versions
    ${repo_classes}=    Search an Entity    entity_name=hmd_lang_deployment.repo_class    filter_=&{filter_test_vpc}
    Length Should Be    ${repo_classes}    1

    ${versions}=    Get From Relationships    ${repo_classes[0]}    hmd_lang_deployment.repo_class_has_repo_class_version
    Should Be True    len($versions) >= 2

    # Verify both versions exist
    ${version_numbers}=    Create List
    FOR    ${version}    IN    @{versions}
        Append To List    ${version_numbers}    ${version.version}
    END
    List Should Contain Value    ${version_numbers}    1.0.0
    List Should Contain Value    ${version_numbers}    1.1.0

    Log    VPC version 1.1.0 registered successfully

Test 2.5 - Find RepoClass Versions
    [Documentation]    Test find_repo_class_versions operation
    [Tags]    repoclass    query

    ${result}=    Invoke Custom Operation    path=find_repo_class_versions/test-vpc    data=&{EMPTY}    method=POST
    Should Not Be Empty    ${result}
    Should Be True    len($result) >= 2

    # Verify versions are returned (should be sorted)
    ${version_list}=    Create List
    FOR    ${item}    IN    @{result}
        Append To List    ${version_list}    ${item}[version]
    END
    List Should Contain Value    ${version_list}    1.0.0
    List Should Contain Value    ${version_list}    1.1.0

    Log    Found ${{len($result)}} versions for test-vpc

Test 2.6 - Find RepoClass Versions Tolerates Malformed Version
    [Documentation]    find_repo_class_versions must return 200 even when a
    ...                registered version is not strict MAJOR.MINOR.BUILD.
    ...                Regression: previously raised 500 on sort.
    [Tags]    repoclass    query    regression

    # Register a two-component version that bypasses validate_version_number
    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_vpc_malformed}    method=POST

    ${result}=    Invoke Custom Operation    path=find_repo_class_versions/test-vpc    data=&{EMPTY}    method=POST
    Should Not Be Empty    ${result}
    Length Should Be    ${result}    3

    ${version_list}=    Create List
    FOR    ${item}    IN    @{result}
        Append To List    ${version_list}    ${item}[version]
    END
    List Should Contain Value    ${version_list}    1.0.0
    List Should Contain Value    ${version_list}    1.1.0
    List Should Contain Value    ${version_list}    0.1

    # Numeric versions sort descending first; malformed entries appended after
    Should Be Equal    ${version_list}[0]    1.1.0
    Should Be Equal    ${version_list}[1]    1.0.0
    Should Be Equal    ${version_list}[2]    0.1

    Log    Malformed version 0.1 surfaced after sorted numeric versions

Test 2.7 - Find RepoClass Versions Paginates and Filters
    [Documentation]    Opt-in ?limit/&offset/&q/&include_deps switches
    ...                find_repo_class_versions to the {items,total,limit,offset}
    ...                envelope, slices newest-first before resolving deps, and
    ...                skips dep resolution when include_deps=false.
    [Tags]    repoclass    query    pagination    gui

    # test-vpc now has 3 versions: 1.1.0, 1.0.0 (sorted), then malformed 0.1.
    # Page 1 of size 2, deps skipped.
    ${page1}=    Invoke Custom Operation    path=find_repo_class_versions/test-vpc?limit=2&offset=0&include_deps=false    data=&{EMPTY}    method=POST
    Length Should Be    ${page1}[items]    2
    Should Be Equal    ${page1}[items][0][version]    1.1.0
    Should Be Equal    ${page1}[items][1][version]    1.0.0
    Should Be Equal As Integers    ${page1}[total]    3
    # include_deps=false leaves dependencies empty (the picker never needs them).
    Should Be Empty    ${page1}[items][0][dependencies]

    # Page 2 picks up the remaining (malformed) version with no overlap.
    ${page2}=    Invoke Custom Operation    path=find_repo_class_versions/test-vpc?limit=2&offset=2    data=&{EMPTY}    method=POST
    Length Should Be    ${page2}[items]    1
    Should Be Equal    ${page2}[items][0][version]    0.1
    Should Be Equal As Integers    ${page2}[total]    3

    # ?q filters by version substring before paging.
    ${filtered}=    Invoke Custom Operation    path=find_repo_class_versions/test-vpc?q=1.1    data=&{EMPTY}    method=POST
    Length Should Be    ${filtered}[items]    1
    Should Be Equal    ${filtered}[items][0][version]    1.1.0
    Should Be Equal As Integers    ${filtered}[total]    1

    Log    Paginated versions envelope filters and slices as expected

Test 2.8 - Register Versions With Discovery Metadata
    [Documentation]    NERD0013 SPEC0002/SPEC0003: add_repo_class_version persists the
    ...                BACON discovery block and get_repo_class_version_detail returns it.
    ...                Two versions of test-discovery are registered so later search
    ...                tests can prove only the latest version is indexed.
    [Tags]    repoclass    discovery    nerd0013

    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_discovery_v1_0}    method=POST
    Register Repo Class Version Tolerating Existing    path=add_repo_class_version    data=&{test_discovery_v1_1}    method=POST

    ${detail}=    Invoke Custom Operation    path=get_repo_class_version_detail/test-discovery/1.1.0    data=&{EMPTY}    method=GET
    Should Be Equal    ${detail}[discovery][summary]    ${test_discovery_v1_1}[discovery][summary]
    Length Should Be    ${detail}[discovery][capabilities]    2
    Should Be Equal    ${detail}[discovery][capabilities][0][name]    create_vpc

    Log    Discovery metadata persisted on test-discovery@1.1.0

# =============================================================================
# Suite 3: Instance records without a ChangeSet (NERD0015 SPEC0003)
# =============================================================================

Test 3.1 - Register VPC Instance As A Plan (DEPLOY_NEXT)
    [Documentation]    register_deployed_instance with status DEPLOY_NEXT records the instance the way apply_changeset would, without a ChangeSet
    [Tags]    register    deployment    core

    &{instance_configuration}=    Create Dictionary    cidr=10.200.0.0/16
    &{payload}=    Create Dictionary
    ...    environment=${ENVIRONMENT_TYPE}
    ...    repo_class_name=test-vpc
    ...    version=1.0.0
    ...    instance_name=robot-vpc
    ...    deployment_id=${DEPLOYMENT_ID}
    ...    instance_configuration=${instance_configuration}
    ...    status=DEPLOY_NEXT

    ${result}=    Invoke Custom Operation    path=register_deployed_instance    data=&{payload}    method=POST
    Should Not Be Empty    ${result}[repo_instance_id]
    Should Not Be Empty    ${result}[repo_instance_deployment_id]
    Set Suite Variable    ${VPC_RID}    ${result}[repo_instance_deployment_id]

    ${result}=    Invoke Custom Operation    path=find_repo_class_instances/test-vpc/${ENVIRONMENT_TYPE}    data=&{EMPTY}    method=GET
    Length Should Be    ${result}    1
    Should Be Equal    ${result[0]}[instance_name]    robot-vpc
    Should Be Equal    ${result[0]}[status]    DEPLOY_NEXT
    Should Be Equal    ${result[0]}[deployment_id]    ${DEPLOYMENT_ID}
    Should Be Equal    ${result[0]}[deployment_identifier]    ${VPC_RID}

    Log    RepoInstance 'robot-vpc' registered with status DEPLOY_NEXT

Test 3.2 - Register Dependent Instance With Dependencies
    [Documentation]    dependencies are resolved by instance name and recorded as RepoInstanceReqRepoInstance edges
    [Tags]    register    dependencies    core

    &{deps}=    Create Dictionary    vpc=robot-vpc
    &{payload}=    Create Dictionary
    ...    environment=${ENVIRONMENT_TYPE}
    ...    repo_class_name=test-database
    ...    version=1.0.0
    ...    instance_name=robot-database
    ...    deployment_id=${DEPLOYMENT_ID}
    ...    dependencies=${deps}
    ...    status=DEPLOY_NEXT

    ${result}=    Invoke Custom Operation    path=register_deployed_instance    data=&{payload}    method=POST
    Should Not Be Empty    ${result}[repo_instance_deployment_id]
    Set Suite Variable    ${DB_RID}    ${result}[repo_instance_deployment_id]

    ${result}=    Invoke Custom Operation    path=find_repo_class_instances/test-database/${ENVIRONMENT_TYPE}    data=&{EMPTY}    method=GET
    Length Should Be    ${result}    1
    Should Be Equal    ${result[0]}[dependencies][vpc]    robot-vpc

    # The pending dependency shows up in the resolved configuration.
    ${config}=    Invoke Custom Operation    path=get_deployment_config/${ENVIRONMENT_TYPE}/robot-database    data=&{EMPTY}    method=GET
    Should Be Equal    ${config}[dependencies][vpc][instance_name]    robot-vpc

    Log    RepoInstance 'robot-database' registered depending on robot-vpc

Test 3.3 - Update Deployment Status to DEPLOYED
    [Documentation]    set_deployment_status flips the pending deployment to current, as a runner does after the deploy
    [Tags]    status    deployment    core

    ${status_result}=    Invoke Custom Operation    path=set_deployment_status/${VPC_RID}/DEPLOYED    data=&{EMPTY}    method=POST
    Should Contain    ${status_result}[message]    DEPLOYED
    ${status_result}=    Invoke Custom Operation    path=set_deployment_status/${DB_RID}/DEPLOYED    data=&{EMPTY}    method=POST
    Should Contain    ${status_result}[message]    DEPLOYED

    ${result}=    Invoke Custom Operation    path=find_repo_class_instances/test-vpc/${ENVIRONMENT_TYPE}    data=&{EMPTY}    method=GET
    Should Be Equal    ${result[0]}[status]    DEPLOYED

    Log    Deployment statuses updated to DEPLOYED

Test 3.4 - Register An Already-Deployed Instance (default status)
    [Documentation]    The default status is DEPLOYED and current -- what hmd deploy --local records after the fact
    [Tags]    register    deployment    core

    &{payload}=    Create Dictionary
    ...    environment=${ENVIRONMENT_TYPE}
    ...    repo_class_name=test-monitoring
    ...    version=1.0.0
    ...    instance_name=robot-monitoring
    ...    deployment_id=${DEPLOYMENT_ID}

    ${result}=    Invoke Custom Operation    path=register_deployed_instance    data=&{payload}    method=POST
    Should Not Be Empty    ${result}[repo_instance_deployment_id]

    ${history}=    Invoke Custom Operation    path=get_deployment_history/${ENVIRONMENT_TYPE}/robot-monitoring    data=&{EMPTY}    method=GET
    Should Be Equal    ${history}[history][0][status]    DEPLOYED

Test 3.5 - Unknown Dependency Is Refused
    [Documentation]    A dependency that is not in the environment is an error, not a silent omission
    [Tags]    register    negative    core

    &{deps}=    Create Dictionary    vpc=no-such-vpc
    &{payload}=    Create Dictionary
    ...    environment=${ENVIRONMENT_TYPE}
    ...    repo_class_name=test-database
    ...    version=1.0.0
    ...    instance_name=robot-orphan
    ...    deployment_id=${DEPLOYMENT_ID}
    ...    dependencies=${deps}
    ...    status=DEPLOY_NEXT

    Run Keyword And Expect Error    *    Invoke Custom Operation    path=register_deployed_instance    data=&{payload}    method=POST

# =============================================================================
# Suite 4: Deployment Status and History
# =============================================================================

Test 4.1 - Retrieve Deployment BOM
    [Documentation]    Get Bill of Materials for the test environment
    [Tags]    bom    query

    ${bom}=    Invoke Custom Operation    path=get_deployment_bom/${ENVIRONMENT_TYPE}    data=&{EMPTY}    method=GET
    Should Not Be Empty    ${bom}

    # Verify BOM contains the deployed instances, dependencies first
    @{names}=    Create List
    FOR    ${item}    IN    @{bom}
        Append To List    ${names}    ${item}[repo_instance_name]
    END
    List Should Contain Value    ${names}    robot-vpc
    List Should Contain Value    ${names}    robot-database
    ${vpc_at}=    Get Index From List    ${names}    robot-vpc
    ${db_at}=    Get Index From List    ${names}    robot-database
    Should Be True    ${vpc_at} < ${db_at}    the BOM lists robot-vpc before the robot-database that depends on it

    Log    Retrieved BOM with ${{len($bom)}} instances

Test 4.2 - Retrieve Instance Deployment History
    [Documentation]    Get deployment history for a specific instance
    [Tags]    history    query

    ${history}=    Invoke Custom Operation    path=get_deployment_history/${ENVIRONMENT_TYPE}/robot-vpc    data=&{EMPTY}    method=GET
    Should Not Be Empty    ${history}
    Should Not Be Empty    ${history}[repo_instance]
    Should Not Be Empty    ${history}[history]

    # Verify instance information
    Should Be Equal    ${history}[repo_instance][name]    robot-vpc

    # Verify history includes deployment record
    Should Be True    len($history["history"]) >= 1
    Should Be Equal    ${history}[history][0][status]    DEPLOYED
    Should Be Equal    ${history}[history][0][deployment_id]    ${DEPLOYMENT_ID}

    Log    Retrieved deployment history for robot-vpc

Test 4.3 - Retrieve Instance Configuration
    [Documentation]    Get merged configuration for deployed instance
    [Tags]    configuration    query

    ${config}=    Invoke Custom Operation    path=get_deployment_config/${ENVIRONMENT_TYPE}/robot-vpc    data=&{EMPTY}    method=GET
    Should Not Be Empty    ${config}

    # The merged configuration is the top level: class defaults deep-updated with
    # the instance configuration, plus identity and dependency details.
    Dictionary Should Contain Key    ${config}    cidr
    Should Be Equal    ${config}[cidr]    10.200.0.0/16
    Should Be Equal    ${config}[instance_name]    robot-vpc
    Dictionary Should Contain Key    ${config}    details

    Log    Retrieved configuration for robot-vpc

# =============================================================================
# Suite 8: Queries
# =============================================================================

Test 8.1 - Find RepoClass Instances by Type
    [Documentation]    Find all instances of a specific RepoClass in environment
    [Tags]    query    search

    ${result}=    Invoke Custom Operation    path=find_repo_class_instances/test-vpc/${ENVIRONMENT_TYPE}    data=&{EMPTY}    method=GET
    Should Not Be Empty    ${result}

    # Should have at least one VPC instance
    ${count}=    Get Length    ${result}
    Should Be True    ${count} >= 1

    # Verify all results are test-vpc instances
    FOR    ${item}    IN    @{result}
        Should Be Equal    ${item}[repo_class_name]    test-vpc
    END

    Log    Found ${count} test-vpc instances

# =============================================================================
# Suite 9: GUI-Facing Listing, Validation and Discovery
# =============================================================================

Test 9.1 - List All RepoClasses
    [Documentation]    list_repo_classes returns all registered RepoClasses with their versions
    [Tags]    repoclass    query    gui    nerd0013

    ${result}=    Invoke Custom Operation    path=list_repo_classes    data=&{EMPTY}    method=GET
    Should Not Be Empty    ${result}

    # Each entry should expose a repo_class_name, a versions list and (NERD0013
    # SPEC0001) the latest version's discovery summary + capability_count.
    ${names}=    Create List
    FOR    ${item}    IN    @{result}
        Dictionary Should Contain Key    ${item}    repo_class_name
        Dictionary Should Contain Key    ${item}    versions
        Dictionary Should Contain Key    ${item}    summary
        Dictionary Should Contain Key    ${item}    capability_count
        Append To List    ${names}    ${item}[repo_class_name]
    END

    List Should Contain Value    ${names}    test-vpc
    List Should Contain Value    ${names}    test-monitoring

    # test-monitoring carries a discovery block; test-vpc registered none.
    FOR    ${item}    IN    @{result}
        Run Keyword If    '${item}[repo_class_name]' == 'test-monitoring'
        ...    Should Be Equal    ${item}[summary]    ${test_monitoring_v1}[discovery][summary]
        Run Keyword If    '${item}[repo_class_name]' == 'test-monitoring'
        ...    Should Be Equal As Integers    ${item}[capability_count]    1
        Run Keyword If    '${item}[repo_class_name]' == 'test-vpc'
        ...    Should Be Equal    ${item}[summary]    ${EMPTY}
    END

    # test-vpc should report both 1.0.0 and 1.1.0
    FOR    ${item}    IN    @{result}
        Run Keyword If    '${item}[repo_class_name]' == 'test-vpc'
        ...    List Should Contain Value    ${item}[versions]    1.0.0
        Run Keyword If    '${item}[repo_class_name]' == 'test-vpc'
        ...    List Should Contain Value    ${item}[versions]    1.1.0
    END

    Log    list_repo_classes returned ${{len($result)}} classes

Test 9.4 - Validate ChangeSet With Satisfiable Dependencies
    [Documentation]    validate_changeset reports valid=true for a self-consistent set of changes
    [Tags]    validation    gui    changeset

    ${vpc_change}=    Create Dictionary    repo_instance_name=validate-vpc    repo_class_name=test-vpc    repo_class_version=1.0.0    deployment_id=${DEPLOYMENT_ID}
    ${db_deps}=    Create Dictionary    vpc=validate-vpc
    ${db_change}=    Create Dictionary    repo_instance_name=validate-db    repo_class_name=test-database    repo_class_version=1.0.0    deployment_id=${DEPLOYMENT_ID}    dependencies=${db_deps}
    @{changes}=    Create List    ${vpc_change}    ${db_change}
    &{payload}=    Create Dictionary    changes=${changes}

    ${result}=    Invoke Custom Operation    path=validate_changeset    data=&{payload}    method=POST
    Should Not Be Empty    ${result}
    Dictionary Should Contain Key    ${result}    valid
    Dictionary Should Contain Key    ${result}    errors
    Dictionary Should Contain Key    ${result}    warnings
    Should Be True    ${result}[valid]
    Should Be Empty    ${result}[errors]

    Log    validate_changeset passed for satisfiable changes

Test 9.5 - Validate ChangeSet Detects Missing Dependency
    [Documentation]    validate_changeset reports an error when a required dependency is absent
    [Tags]    validation    gui    changeset    negative

    ${db_deps}=    Create Dictionary    vpc=missing-vpc
    ${db_change}=    Create Dictionary    repo_instance_name=orphan-db    repo_class_name=test-database    repo_class_version=1.0.0    deployment_id=${DEPLOYMENT_ID}    dependencies=${db_deps}
    @{changes}=    Create List    ${db_change}
    &{payload}=    Create Dictionary    changes=${changes}

    ${result}=    Invoke Custom Operation    path=validate_changeset    data=&{payload}    method=POST
    Should Be Equal    ${result}[valid]    ${FALSE}
    Should Not Be Empty    ${result}[errors]

    Log    validate_changeset flagged missing dependency: ${result}[errors]

Test 9.6 - Validate ChangeSet Detects Unknown RepoClass
    [Documentation]    validate_changeset reports an error when a referenced RepoClass does not exist
    [Tags]    validation    gui    changeset    negative

    ${change}=    Create Dictionary    repo_instance_name=ghost    repo_class_name=does-not-exist    repo_class_version=9.9.9    deployment_id=${DEPLOYMENT_ID}
    @{changes}=    Create List    ${change}
    &{payload}=    Create Dictionary    changes=${changes}

    ${result}=    Invoke Custom Operation    path=validate_changeset    data=&{payload}    method=POST
    Should Be Equal    ${result}[valid]    ${FALSE}
    Should Not Be Empty    ${result}[errors]

    Log    validate_changeset flagged unknown RepoClass: ${result}[errors]

Test 9.9 - Search Discovery By Capability Text
    [Documentation]    NERD0013 SPEC0002: search_discovery matches free text across the
    ...                latest version's summary, capability names/descriptions and entry
    ...                points, and returns only the matching capabilities per hit.
    [Tags]    repoclass    discovery    query    gui    nerd0013

    ${result}=    Invoke Custom Operation    path=search_discovery?q=subnets    data=&{EMPTY}    method=GET
    Dictionary Should Contain Key    ${result}    items
    Dictionary Should Contain Key    ${result}    total
    Should Be True    ${result}[total] >= 1
    ${hit}=    First Item For Repo Class    ${result}[items]    test-discovery
    Should Not Be Equal    ${hit}    ${NONE}    test-discovery should match on its capability text
    # Only the latest version is indexed, never 1.0.0's legacy discovery.
    Should Be Equal    ${hit}[version]    1.1.0
    Length Should Be    ${hit}[capabilities]    1
    Should Be Equal    ${hit}[capabilities][0][name]    create_vpc

    # A capability-name hit outranks a summary-only hit and the legacy text is gone.
    ${legacy}=    Invoke Custom Operation    path=search_discovery?q=legacy_only_capability    data=&{EMPTY}    method=GET
    Should Be Equal As Integers    ${legacy}[total]    0

    Log    search_discovery matched test-discovery@1.1.0 on capability text

Test 9.10 - Search Discovery Filters By Kind
    [Documentation]    NERD0013 SPEC0002: ?kind= is an exact filter on capability kind and
    ...                an unknown kind is rejected.
    [Tags]    repoclass    discovery    query    gui    nerd0013    negative

    ${cli}=    Invoke Custom Operation    path=search_discovery?kind=cli_command    data=&{EMPTY}    method=GET
    Should Be True    ${cli}[total] >= 1
    ${names}=    Create List
    FOR    ${item}    IN    @{cli}[items]
        Append To List    ${names}    ${item}[repo_class_name]
        FOR    ${cap}    IN    @{item}[capabilities]
            Should Be Equal    ${cap}[kind]    cli_command
        END
    END
    List Should Contain Value    ${names}    test-monitoring
    List Should Not Contain Value    ${names}    test-discovery

    Run Keyword And Expect Error    *
    ...    Invoke Custom Operation    path=search_discovery?kind=bogus    data=&{EMPTY}    method=GET

    Log    kind filter restricted results to cli_command capabilities

Test 9.11 - Search Discovery Paginates And Prefix-Filters
    [Documentation]    NERD0013 SPEC0002: ?repo_class_name= is a case-insensitive prefix
    ...                filter and ?limit/&offset page the {items,total,limit,offset} envelope.
    ...                An empty query returns every class with its full capability list.
    [Tags]    repoclass    discovery    query    gui    pagination    nerd0013

    ${page1}=    Invoke Custom Operation    path=search_discovery?repo_class_name=TEST-&limit=1&offset=0    data=&{EMPTY}    method=GET
    Length Should Be    ${page1}[items]    1
    Should Be True    ${page1}[total] >= 2
    Should Be Equal As Integers    ${page1}[limit]    1
    Should Be Equal As Integers    ${page1}[offset]    0

    ${page2}=    Invoke Custom Operation    path=search_discovery?repo_class_name=test-&limit=1&offset=1    data=&{EMPTY}    method=GET
    Length Should Be    ${page2}[items]    1
    Should Not Be Equal    ${page1}[items][0][repo_class_name]    ${page2}[items][0][repo_class_name]

    # Unfiltered: test-discovery reports both of its 1.1.0 capabilities.
    ${all}=    Invoke Custom Operation    path=search_discovery?repo_class_name=test-discovery    data=&{EMPTY}    method=GET
    Should Be Equal As Integers    ${all}[total]    1
    Length Should Be    ${all}[items][0][capabilities]    2
    Should Be Equal As Integers    ${all}[items][0][capability_count]    2
    Length Should Be    ${all}[items][0][related_docs]    1

    Log    search_discovery envelope paged and prefix-filtered as expected

# =============================================================================
# Suite 10: Bundle and Release Registry (NERD0010, NERD0016)
# =============================================================================

Test 10.1 - Register Bundles From One Repo Version
    [Documentation]    upsert_bundle_versions registers every bundle a repo class declares,
    ...                versioned by that repo's version; each bundle succeeds or fails alone
    [Tags]    bundle    registry

    ${result}=    Invoke Custom Operation    path=upsert_bundle_versions    data=&{bundle_core_payload}    method=POST
    Should Be Equal    ${result}[results][0][status]    created
    Should Be Equal    ${result}[results][0][version]    0.1.0
    Should Be Equal    ${result}[results][1][status]    error

    ${again}=    Invoke Custom Operation    path=upsert_bundle_versions    data=&{bundle_core_payload}    method=POST
    Should Be Equal    ${again}[results][0][status]    unchanged

Test 10.2 - Get Bundle Version
    [Documentation]    get_bundle_version returns the declaration and its provenance
    [Tags]    bundle    registry

    ${bundle}=    Invoke Custom Operation    path=get_bundle_version/${BUNDLE_NAME}    data=&{EMPTY}    method=GET
    Should Be Equal    ${bundle}[version]    0.1.0
    Should Be Equal    ${bundle}[source_repo_class_name]    hmd-bundle-core
    Dictionary Should Contain Key    ${bundle}[roles]    database

    ${by_spec}=    Invoke Custom Operation    path=get_bundle_version/${BUNDLE_NAME}?version_spec=~=0.1.0    data=&{EMPTY}    method=GET
    Should Be Equal    ${by_spec}[version]    0.1.0

Test 10.3 - Install Release Records Pins
    [Documentation]    install_release records the release and reports each pinned artifact;
    ...                both pinned versions are registered, so the release is installed
    [Tags]    release    registry

    ${result}=    Invoke Custom Operation    path=install_release    data=&{release_install_payload}    method=POST
    Should Be Equal    ${result}[release_name]    ${RELEASE_NAME}
    Length Should Be    ${result}[entries]    2
    IF    '${result}[artifact_check]' == 'unavailable'
        Should Be True    ${result}[installed]
    END

    ${rv}=    Invoke Custom Operation    path=get_release_version/${RELEASE_NAME}?version=0.1.0    data=&{EMPTY}    method=GET
    Should Be Equal    ${rv}[status]    released
    Should Be Equal    ${rv}[pins][test-vpc]    1.0.0

    ${again}=    Invoke Custom Operation    path=install_release    data=&{release_install_payload}    method=POST
    Should Be Equal    ${again}[version]    0.1.0

Test 10.3b - Release Install Status
    [Documentation]    release_install_status returns the stored install report without writing;
    ...                refreshing recomputes it, and a missing version is refused
    [Tags]    release    registry

    ${status}=    Invoke Custom Operation    path=release_install_status/${RELEASE_NAME}?version=0.1.0    data=&{EMPTY}    method=GET
    Length Should Be    ${status}[entries]    2
    IF    '${status}[artifact_check]' == 'unavailable'
        Should Be True    ${status}[installed]
    END
    ${fresh}=    Invoke Custom Operation    path=release_install_status/${RELEASE_NAME}?version=0.1.0&refresh=true    data=&{EMPTY}    method=GET
    Should Be Equal    ${fresh}[installed]    ${status}[installed]
    Run Keyword And Expect Error    *version*
    ...    Invoke Custom Operation    path=release_install_status/${RELEASE_NAME}    data=&{EMPTY}    method=GET

Test 10.4 - Check Release Coverage
    [Documentation]    check_release_coverage reports an exactly tested combination and an untested one
    [Tags]    release    coverage

    ${tested}=    Invoke Custom Operation    path=check_release_coverage    data=&{release_coverage_payload}    method=POST
    Should Be True    '${tested}[match]' in ['equal', 'covered']

    ${untested}=    Invoke Custom Operation    path=check_release_coverage    data=&{release_coverage_untested_payload}    method=POST
    Should Be Equal    ${untested}[match]    neither

*** Keywords ***
Register Repo Class Version Tolerating Existing
    [Documentation]    add_repo_class_version, treating "already has version" as success: the
    ...                control plane's database persists across runs and this suite is rerun against it.
    [Arguments]    ${path}    ${data}    ${method}=POST
    ${status}    ${result}=    Run Keyword And Ignore Error    Invoke Custom Operation    path=${path}    data=${data}    method=${method}
    IF    '${status}' == 'FAIL'
        ${text}=    Convert To String    ${result}
        Should Contain    ${text}    already has version
    END
    [Return]    ${result}

Version Entity
    [Documentation]    The RepoClassVersion entity with the given version, from a relationship list.
    [Arguments]    ${versions}    ${version}
    FOR    ${v}    IN    @{versions}
        Return From Keyword If    '${v.version}' == '${version}'    ${v}
    END
    Fail    No version ${version} among ${versions}

First Item For Repo Class
    [Arguments]    ${items}    ${repo_class_name}
    FOR    ${item}    IN    @{items}
        Return From Keyword If    '${item}[repo_class_name]' == '${repo_class_name}'    ${item}
    END
    [Return]    ${NONE}

Log Test Configuration
    Log    ========================================
    Log    Test Configuration
    Log    ========================================
    Log    AWS Account: ${test_environment_data}[account_number]
    Log    AWS Region: ${test_environment_data}[hmd_region]
    Log    HMD Region: ${test_environment_data}[hmd_region]
    Log    Environment Type: ${ENVIRONMENT_TYPE}
    Log    Deployment Set: ${DEPLOYMENT_SET_NAME}
    Log    Deployment ID: ${DEPLOYMENT_ID}
    Log    ========================================

Create and Upsert an Entity
    [Arguments]    ${entity_name}    ${entity_data}
    ${entity}=    Create an Entity    ${entity_name}    ${entity_data}
    ${entity}=    Upsert an Entity    ${entity}
    [Return]    ${entity}


Create and Upsert a Relationship
    [Arguments]    ${relationship_name}    ${from}    ${to}    ${rel_data}=&{EMPTY}
    &{entity_data}=    Create Dictionary    ref_from=${from.identifier}    ref_to=${to.identifier}    &{rel_data}
    ${entity}=    Create an Entity    ${relationship_name}    ${entity_data}
    ${entity}=    Upsert an Entity    ${entity}
    [Return]    ${entity}

Find Entity By Identifier
    [Arguments]    ${entity_list}    ${identifier}
    FOR    ${entity}    IN    @{entity_list}
        Return From Keyword If    '${entity.identifier}' == '${identifier}'    ${entity}
    END
    [Return]    ${NONE}

Get Instance From Definition
    [Arguments]    ${definition}    ${instance_name}
    FOR    ${item}    IN    @{definition}
        Return From Keyword If    '${item}[repo_instance_name]' == '${instance_name}'    ${item}
    END
    Fail    Instance ${instance_name} not found in definition


Cleanup Test Data
    [Documentation]    Clean up test entities created during test run
    Log    Cleaning up test data...

    # Note: In a real cleanup, we would destroy all test instances
    # For now, we just log the cleanup intent
    Log    Test entities with prefix 'robot-' and 'test-' should be cleaned up
    Log    Environment '${ENVIRONMENT_TYPE}' can be removed
    Log    DeploymentSet '${DEPLOYMENT_SET_NAME}' can be removed
