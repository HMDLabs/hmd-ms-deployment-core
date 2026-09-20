import os
import time
import boto3
from hmd_cli_tools.hmd_cli_tools import get_cloud_region

# Get current AWS account and region dynamically
def get_aws_account_number():
    """Get current AWS account number via STS."""
    try:
        sts_client = boto3.client('sts')
        return sts_client.get_caller_identity()['Account']
    except Exception as e:
        print(f"Warning: Could not get AWS account number: {e}")
        return "123456789012"  # Fallback for local testing

def get_aws_region():
    """Get AWS region from HMD_REGION environment variable."""
    try:
        hmd_region = os.environ.get('HMD_REGION', 'reg1')
        return get_cloud_region(hmd_region)
    except Exception as e:
        print(f"Warning: Could not get AWS region: {e}")
        return "us-west-2"  # Fallback

# Environment configuration
AWS_ACCOUNT_NUMBER = get_aws_account_number()
AWS_REGION = get_aws_region()
DEPLOYMENT_ID = "aaa"
# Unique per run: the control plane's database persists across runs, and the
# service asserts exactly one Environment per type. Repo classes (test-vpc, ...)
# are shared and registered tolerantly; instances live in this run's environment.
_RUN_ID = os.environ.get("ROBOT_RUN_ID") or time.strftime("%m%d%H%M%S")
ENVIRONMENT_TYPE = f"robot-test-{_RUN_ID}"
DEPLOYMENT_SET_NAME = f"{ENVIRONMENT_TYPE}-deploymentset"

gozer_instance_name = "ms-gozer"

pre_test_db_clear = {
    "rds_services": ["dbaccount-deployment"]
}

add_rc_1 = {
  "repo_class_name": "add_rc_1",
  "version": "1.2.3",
  "dependencies": {},
  "default_configuration": {
    "field": "value"
  },
  "version_notes": {
    "version_notes": "A version note.",
    "requirement_identifiers": ["1","2"]
  }
}

add_rc_1_filter = {
  "attribute": "repo_class_name",
  "operator": "=",
  "value": "add_rc_1"
}

bom_results = [
  {
    "repo_instance_name": "ri1",
    "repo_class_name": "a_repo_class2",
    "repo_class_version": "0.1.2",
    "deployment_id": "aaa",
    "auto_deploy": "false",
    "instance_configuration": {},
    "status": "DEPLOYED",
    "dependencies": {}
  },
  {
    "repo_instance_name": "ri1",
    "repo_class_name": "a_repo_class",
    "repo_class_version": "0.1.2",
    "deployment_id": "aaa",
    "auto_deploy": "false",
    "instance_configuration": {},
    "status": "DEPLOYED",
    "dependencies": {
      "blah": "ri1"
    }
  }
]

# Test data for comprehensive deployment workflow tests
test_vpc_v1 = {
    "repo_class_name": "test-vpc",
    "version": "1.0.0",
    "dependencies": {},
    "default_configuration": {
        "cidr": "10.200.0.0/16",
        "azs": ["us-west-2a", "us-west-2b"],
        "public_subnets": ["10.200.100.0/24", "10.200.101.0/24"],
        "private_subnets": ["10.200.0.0/20", "10.200.16.0/20"]
    },
    "version_notes": {
        "version_notes": "Initial VPC for robot testing",
        "requirement_identifiers": ["TEST-001"]
    }
}

test_vpc_v1_1 = {
    "repo_class_name": "test-vpc",
    "version": "1.1.0",
    "dependencies": {},
    "default_configuration": {
        "cidr": "10.200.0.0/16",
        "azs": ["us-west-2a", "us-west-2b", "us-west-2c"],
        "public_subnets": ["10.200.100.0/24", "10.200.101.0/24", "10.200.102.0/24"],
        "private_subnets": ["10.200.0.0/20", "10.200.16.0/20", "10.200.32.0/20"]
    },
    "version_notes": {
        "version_notes": "Updated VPC with 3 AZs",
        "requirement_identifiers": ["TEST-002"]
    }
}

# Malformed two-component version — accepted by add_repo_class_version
# (validate_version_number only checks numericness) but historically broke
# find_repo_class_versions because sort_versions assumed MAJOR.MINOR.BUILD.
test_vpc_malformed = {
    "repo_class_name": "test-vpc",
    "version": "0.1",
    "dependencies": {},
    "default_configuration": {
        "cidr": "10.199.0.0/16"
    },
    "version_notes": {
        "version_notes": "Two-component malformed version for sort robustness test",
        "requirement_identifiers": ["TEST-005"]
    }
}

test_database_v1 = {
    "repo_class_name": "test-database",
    "version": "1.0.0",
    "dependencies": {
        "vpc": {
            "repo_class_name": "test-vpc",
            "version_spec": "~= 1.0",
            "required": "true"
        }
    },
    "default_configuration": {
        "engine": "postgres",
        "engine_version": "14.7",
        "instance_class": "db.t3.micro",
        "allocated_storage": 20
    },
    "version_notes": {
        "version_notes": "Test database with VPC dependency",
        "requirement_identifiers": ["TEST-003"]
    }
}

test_monitoring_v1 = {
    "repo_class_name": "test-monitoring",
    "version": "1.0.0",
    "dependencies": {},
    "default_configuration": {
        "log_retention_days": 7,
        "metrics_enabled": True
    },
    "version_notes": {
        "version_notes": "Basic monitoring setup",
        "requirement_identifiers": ["TEST-004"]
    },
    # NERD0013: BACON discovery metadata, searchable via search_discovery
    "discovery": {
        "summary": "Ships log retention and metrics collection for a deployment.",
        "entry_points": [
            {"path": "src/python/monitoring/cli.py", "description": "Monitoring CLI entry point."}
        ],
        "capabilities": [
            {
                "name": "hmd monitoring rotate-logs",
                "kind": "cli_command",
                "description": "Rotates and prunes log files past the retention window.",
                "location": "src/python/monitoring/cli.py:40"
            }
        ],
        "related_docs": [{"title": "Monitoring guide", "path": "docs/monitoring.rst"}]
    }
}

# NERD0013: a dedicated RepoClass with two versions whose discovery differs, so
# search_discovery is proven to index only the latest version per class.
test_discovery_v1_0 = {
    "repo_class_name": "test-discovery",
    "version": "1.0.0",
    "dependencies": {},
    "default_configuration": {},
    "discovery": {
        "summary": "Legacy summary that must not be indexed once 1.1.0 exists.",
        "capabilities": [
            {"name": "legacy_only_capability", "kind": "function", "description": "Old."}
        ]
    }
}

test_discovery_v1_1 = {
    "repo_class_name": "test-discovery",
    "version": "1.1.0",
    "dependencies": {},
    "default_configuration": {},
    "discovery": {
        "summary": "Provisions the base VPC and its subnets for an account.",
        "entry_points": [
            {"path": "src/python/discovery/ops.py", "description": "Registered service operations."}
        ],
        "capabilities": [
            {
                "name": "create_vpc",
                "kind": "operation",
                "description": "Create the VPC and subnets.",
                "location": "src/python/discovery/ops.py:12"
            },
            {
                "name": "GET /apiop/vpc_status",
                "kind": "endpoint",
                "description": "Report the VPC provisioning status."
            }
        ],
        "related_docs": [{"title": "User guide", "path": "docs/user_guide.rst"}]
    }
}

# ChangeSet definitions
changeset_single_instance = {
    "name": "robot-changeset-vpc",
    "definition": [
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-vpc",
            "repo_class_name": "test-vpc",
            "repo_class_version": "1.0.0",
            "instance_configuration": {
                "cidr": "10.200.0.0/16"
            },
            "dependencies": {}
        }
    ]
}

changeset_with_extra_status_field = {
    "name": "robot-changeset-extra-status",
    "definition": [
        {
            "status": "DEPLOYED",
            "auto_deploy": "false",
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-vpc",
            "repo_class_name": "test-vpc",
            "repo_class_version": "1.0.0",
            "instance_configuration": {},
            "dependencies": {}
        }
    ]
}

changeset_with_dependencies = {
    "name": "robot-changeset-full",
    "definition": [
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-vpc",
            "repo_class_name": "test-vpc",
            "repo_class_version": "1.0.0",
            "instance_configuration": {},
            "dependencies": {}
        },
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-monitoring",
            "repo_class_name": "test-monitoring",
            "repo_class_version": "1.0.0",
            "instance_configuration": {},
            "dependencies": {}
        },
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-database",
            "repo_class_name": "test-database",
            "repo_class_version": "1.0.0",
            "instance_configuration": {
                "instance_class": "db.t3.small"
            },
            "dependencies": {
                "vpc": "robot-vpc"
            }
        }
    ]
}

# ChangeSet with mixed resource name formats (NERD0002)
changeset_mixed_formats = {
    "name": "robot-changeset-mixed",
    "definition": [
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-vpc-2",
            "repo_class_name": "test-vpc",
            "repo_class_version": "1.1.0",
            "instance_configuration": {},
            "dependencies": {}
        },
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-monitoring-2",
            "repo_class_name": "test-monitoring",
            "repo_class_version": "1.0.0",
            "instance_configuration": {},
            "dependencies": {}
        },
        {
            "deployment_id": DEPLOYMENT_ID,
            "repo_instance_name": "robot-database-2",
            "repo_class_name": "test-database",
            "repo_class_version": "1.0.0",
            "instance_configuration": {},
            "dependencies": {
                "vpc": f"ns:robot-vpc-2:{DEPLOYMENT_ID}"  # Shorthand format
            }
        }
    ]
}

# DeploymentSet definition
deployment_set_definition = {
    "name": DEPLOYMENT_SET_NAME,
    "definition": [
        {
            "environment": ENVIRONMENT_TYPE,
            "deployment_gate": {
                "transforms": [],
                "approval": False
            }
        }
    ]
}

# Environment data
# Renamed from `test_environment` to avoid a case-insensitive Robot Framework
# collision with the suite variable `${TEST_ENVIRONMENT}` (which holds the
# created environment entity). The section-table `${TEST_ENVIRONMENT}=${EMPTY}`
# otherwise shadows this dict, breaking `${test_environment}[account_number]`.
test_environment_data = {
    "type": ENVIRONMENT_TYPE,
    "account_number": AWS_ACCOUNT_NUMBER,
    "hmd_region": os.environ.get('HMD_REGION', 'reg1')
}

# Search filters
filter_test_vpc = {
    "attribute": "repo_class_name",
    "operator": "=",
    "value": "test-vpc"
}

filter_test_database = {
    "attribute": "repo_class_name",
    "operator": "=",
    "value": "test-database"
}

filter_test_monitoring = {
    "attribute": "repo_class_name",
    "operator": "=",
    "value": "test-monitoring"
}

filter_environment = {
    "attribute": "type",
    "operator": "=",
    "value": ENVIRONMENT_TYPE
}

filter_deployment_set = {
    "attribute": "name",
    "operator": "=",
    "value": DEPLOYMENT_SET_NAME
}


# ---------------------------------------------------------------------------
# NERD0004 Resource Definitions and Resources
# ---------------------------------------------------------------------------

resource_def_k8s = {
    "resource_namespace": "kubernetes",
    "resource_definition_name": "kubernetes-cluster",
    "version": "0.1.0",
    "description": "A generic Kubernetes cluster",
    "output_schema": {
        "type": "object",
        "required": ["cluster_name"],
        "properties": {"cluster_name": {"type": "string"}},
    },
}

resource_def_eks = {
    "resource_namespace": "aws",
    "resource_definition_name": "eks-cluster",
    "version": "0.1.0",
    "description": "An EKS cluster",
    "output_schema": {
        "type": "object",
        "properties": {"cluster_arn": {"type": "string"}},
    },
    "parent": {
        "resource_namespace": "kubernetes",
        "resource_definition_name": "kubernetes-cluster",
        "version": "0.1.0",
    },
}

resource_inst_depl_data = {
    "deployment_id": "resource-test-dep",
    "status": "DEPLOYED",
}

# NERD0004 SPEC0008: a consumer class version declaring a resource-type
# dependency. The class-name is retained as a suggestion; the resource
# requirement is authoritative.
add_rc_resource_dep = {
    "repo_class_name": "consumer_rc",
    "version": "1.0.0",
    "dependencies": {
        "cluster": {
            "required": "true",
            "repo_class_name": "a_repo_class",
            "resource": {
                "resource_namespace": "kubernetes",
                "resource_definition_name": "kubernetes-cluster",
                "version": "0.1.0",
                "version_spec": "~= 0.1",
                "tag_selector": "tier=prod",
            },
        }
    },
    "default_configuration": {},
}

add_rc_resource_dep_filter = {
    "attribute": "repo_class_name",
    "operator": "=",
    "value": "consumer_rc",
}

resource_submission_list = [
    {
        "resource_name": "prod-eks",
        "resource_definition": {
            "resource_namespace": "aws",
            "resource_definition_name": "eks-cluster",
            "version": "0.1.0",
        },
        "output": {"cluster_name": "prod-eks"},
        "tags": [
            {"key": "env", "value": "prod"},
            {"key": "team", "value": "platform"},
        ],
    }
]

# NERD0004 Kubernetes-native supertypes: a concrete ingress controller parenting the
# seeded base kubernetes.neuronsphere.io/ingress-controller (isa operator isa deployment).
resource_def_alb_controller = {
    "resource_namespace": "aws.neuronsphere.io",
    "resource_definition_name": "aws-load-balancer-controller",
    "version": "0.1.0",
    "description": "The AWS Load Balancer Controller",
    "parent": {
        "resource_namespace": "kubernetes.neuronsphere.io",
        "resource_definition_name": "ingress-controller",
        "version": "0.1.0",
    },
}

alb_controller_inst_depl_data = {
    "deployment_id": "alb-ctrl-dep",
    "status": "DEPLOYED",
}

# The produced output as hmd-cli-helm renders it from the chart; it must validate against
# the merged (ingress-controller -> operator -> deployment) effective schema.
alb_controller_submission_list = [
    {
        "resource_name": "alb-controller",
        "resource_definition": {
            "resource_namespace": "aws.neuronsphere.io",
            "resource_definition_name": "aws-load-balancer-controller",
            "version": "0.1.0",
        },
        "output": {
            "name": "aws-load-balancer-controller",
            "namespace": "alb-aaa",
            "service_account": "alb-aaa",
            "ingress_class": "alb",
            "irsa_role_arn": "arn:aws:iam::123456789012:role/alb-aaa-alb-ctrl-role",
        },
        "tags": [{"key": "tier", "value": "prod"}],
    }
]


# Environment scoping (list_resources?environment=): a second Environment whose
# resources must never leak into the dev listing.
scoping_admin_environment_data = {
    "type": "admin",
    "account_number": "5678",
    "hmd_region": "reg1",
}

scoping_dev_environment_filter = {
    "attribute": "type",
    "operator": "=",
    "value": "dev",
}

scoping_admin_instance_data = {"name": "scoping-ri-admin", "auto_deploy": "false"}
scoping_dev_instance_data = {"name": "scoping-ri-dev", "auto_deploy": "false"}

# Two DEPLOYED deployments for the same dev instance: the superseded one keeps
# status DEPLOYED and only has the relationship's `current` flag flipped off,
# which is exactly the shape that used to duplicate every redeployed resource.
scoping_dev_old_depl_data = {"deployment_id": "scoping-dev-1", "status": "DEPLOYED"}
scoping_dev_new_depl_data = {"deployment_id": "scoping-dev-2", "status": "DEPLOYED"}
scoping_admin_depl_data = {"deployment_id": "scoping-admin-1", "status": "DEPLOYED"}

# submit_resources is idempotent per *deployment*, so the same resource_name
# submitted against both dev deployments mints two distinct Resource rows.
scoping_dev_submission_list = [
    {
        "resource_name": "scoping-dev-cluster",
        "resource_definition": {
            "resource_namespace": "aws",
            "resource_definition_name": "eks-cluster",
            "version": "0.1.0",
        },
        "output": {"cluster_name": "scoping-dev-cluster"},
        "tags": [{"key": "scoping", "value": "dev"}],
    }
]

scoping_admin_submission_list = [
    {
        "resource_name": "scoping-admin-cluster",
        "resource_definition": {
            "resource_namespace": "aws",
            "resource_definition_name": "eks-cluster",
            "version": "0.1.0",
        },
        "output": {"cluster_name": "scoping-admin-cluster"},
        "tags": [{"key": "scoping", "value": "admin"}],
    }
]
