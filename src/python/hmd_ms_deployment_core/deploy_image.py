"""Which tool set image runs a deployment.

Kept free of entity imports so the deployment recorder, the workflow creator
and the deploy-requirement evaluator can all share it without import cycles.
"""

import json
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(f"HMD.{__name__}")


TOOLSET_REPO = "hmd-img-projectbuilder"


class InvalidDeployImage(ValueError):
    """A per-item ``deploy_image`` that is not an allowed tool set image."""


def _registry_prefix(image: str) -> str:
    return image.split("@", 1)[0].rsplit("/", 1)[0]


def _validate_override(override: str, default: Optional[str]) -> str:
    """An override runs with deploy credentials, so it must be the tool set
    image from the registry the environment already deploys from."""
    parsed = parse_image_reference(override)
    if parsed is None or parsed[0] != TOOLSET_REPO:
        raise InvalidDeployImage(
            f"deploy_image {override!r} must be a tagged {TOOLSET_REPO} image."
        )
    if default and _registry_prefix(override) != _registry_prefix(default):
        raise InvalidDeployImage(
            f"deploy_image {override!r} must come from {_registry_prefix(default)}."
        )
    return override


def resolve_deploy_image(
    environment_type: Optional[str] = None, override: Optional[str] = None
) -> Optional[str]:
    """The tool set image that runs deployments into ``environment_type``.

    A per-item ``override`` (the change set's ``deploy_image``) wins, then
    ``HMD_APP_IMAGE_MAP`` (a JSON object keyed by environment type), then
    ``HMD_APP_IMAGE``. Validation, workflow creation and the recorded
    ``deployment_image`` all call this, so the tool set whose requirements are
    checked is the one that runs the deploy.
    """
    image = os.environ.get("HMD_APP_IMAGE")
    try:
        image_map = json.loads(os.environ.get("HMD_APP_IMAGE_MAP") or "{}")
    except json.JSONDecodeError:
        logger.warning("HMD_APP_IMAGE_MAP is not valid JSON; ignoring it.")
        image_map = {}
    if isinstance(image_map, dict) and environment_type:
        image = image_map.get(environment_type, image)
    if override:
        return _validate_override(override, os.environ.get("HMD_APP_IMAGE"))
    return image


def parse_image_reference(image: Optional[str]) -> Optional[Tuple[str, str]]:
    """``(repo_class_name, version)`` for an image reference, or None.

    The repository's last path segment is the repo class and the tag is the
    version, whatever registry or mirror prefix the reference carries (ghcr,
    an ECR pull-through cache, a local registry with a port).
    """
    if not image:
        return None
    reference = image.split("@", 1)[0]
    last_segment = reference.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return None
    name, tag = last_segment.rsplit(":", 1)
    if not name or not tag:
        return None
    return name, tag
