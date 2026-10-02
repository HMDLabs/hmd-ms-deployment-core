"""Which tool set image runs a deployment.

Kept free of entity imports so the deployment recorder, the workflow creator
and the deploy-requirement evaluator can all share it without import cycles.
"""

import json
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(f"HMD.{__name__}")


def resolve_deploy_image(environment_type: Optional[str] = None) -> Optional[str]:
    """The tool set image that runs deployments into ``environment_type``.

    ``HMD_APP_IMAGE_MAP`` (a JSON object keyed by environment type) wins over
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
