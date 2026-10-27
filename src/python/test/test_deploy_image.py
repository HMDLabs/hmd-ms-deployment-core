"""Per-item deploy image override (change set ``deploy_image``).

Reproducing a customer deployment means deploying an old repo class version
with the toolchain of its era, so one instance can name its own
hmd-img-projectbuilder image instead of the environment default.
"""

import os
from unittest.mock import patch

import pytest

from hmd_ms_deployment_core.deploy_image import (
    InvalidDeployImage,
    resolve_deploy_image,
)

CURRENT = "ghcr.io/hmdlabs/hmd-img-projectbuilder:0.5.402"
OLD = "ghcr.io/hmdlabs/hmd-img-projectbuilder:0.4.200"
MAPPED = "ghcr.io/hmdlabs/hmd-img-projectbuilder:0.5.300"


def _env(**overrides):
    values = {"HMD_APP_IMAGE": CURRENT, "HMD_APP_IMAGE_MAP": "{}"}
    values.update(overrides)
    return patch.dict(os.environ, values)


def test_no_override_keeps_the_environment_default():
    with _env():
        assert resolve_deploy_image("dev") == CURRENT
        assert resolve_deploy_image("dev", None) == CURRENT


def test_override_wins_over_the_default():
    with _env():
        assert resolve_deploy_image("dev", OLD) == OLD


def test_override_wins_over_the_environment_map():
    with _env(HMD_APP_IMAGE_MAP='{"dev": "%s"}' % MAPPED):
        assert resolve_deploy_image("dev", OLD) == OLD
        assert resolve_deploy_image("dev") == MAPPED


def test_empty_override_is_ignored():
    with _env():
        assert resolve_deploy_image("dev", "") == CURRENT


def test_override_from_another_repo_is_rejected():
    with _env(), pytest.raises(InvalidDeployImage):
        resolve_deploy_image("dev", "ghcr.io/hmdlabs/hmd-ms-deployment:0.4.200")


def test_override_from_another_registry_is_rejected():
    with _env(), pytest.raises(InvalidDeployImage):
        resolve_deploy_image("dev", "docker.io/someone/hmd-img-projectbuilder:0.4.200")


def test_override_without_a_tag_is_rejected():
    with _env(), pytest.raises(InvalidDeployImage):
        resolve_deploy_image("dev", "ghcr.io/hmdlabs/hmd-img-projectbuilder")


def test_override_may_use_the_ecr_mirror_of_the_configured_registry():
    mirror = "123456789012.dkr.ecr.us-east-1.amazonaws.com/ghcr/hmdlabs"
    configured = f"{mirror}/hmd-img-projectbuilder:0.5.402"
    with _env(HMD_APP_IMAGE=configured):
        assert (
            resolve_deploy_image("dev", f"{mirror}/hmd-img-projectbuilder:0.4.200")
            == f"{mirror}/hmd-img-projectbuilder:0.4.200"
        )
