"""Rendering an architecture model through a registered template (NERD036 SPEC006).

A template is a Mickey template package, and this renders it the way
``hmd_lib_mickey``'s render phase does, without the rest of Mickey (whose
dependency phases need git and a remote-dependency client in this image):

* every file is a Jinja template, named by its path within the package;
* files whose name or stem starts with ``_`` or ``.`` are partials, not outputs;
* a ``.j2`` or ``.jinja`` suffix is dropped from the output path, and the path
  itself is rendered as a template;
* the context is the model's top-level keys, plus ``vars`` (the request's
  parameters) and ``mickey`` (Mickey's folder defaults).

Each template repo checks that ``hmd mickey build`` produces the same outputs.
Templates get the model and their parameters and nothing else: no hooks, and
the environment is sandboxed, because a registered template runs inside this
service.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Dict, Optional

from hmd_base_service.exceptions import ServiceException
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from jinja2 import DictLoader, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .architecture import (
    build_architecture_model,
    get_architecture_view,
    resolve_rendering_template,
)

logger = logging.getLogger(__name__)

# hmd_lib_mickey.phases.render_templates.mickey_defaults
MICKEY_DEFAULTS = {
    "docs_folder": "docs",
    "src_folder": "src",
    "target_folder": "target",
    "metadata_folder": "meta-data",
}


def model_digest(model: Dict) -> str:
    """The SHA-256 of the model's canonical JSON: equal scopes, equal digests."""
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_partial(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        p.name.startswith(("_", "."))
        or p.stem.startswith(("_", "."))
        or ".DS_Store" in path
    )


def _check_path(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ServiceException(f"Template file {path} is outside the template.")
    return str(p)


def render_model(
    model: Dict, files: Dict[str, str], params: Optional[Dict]
) -> Dict[str, str]:
    """Render ``files`` (a Mickey template package, by path) over ``model``."""
    if not files:
        raise ServiceException("The rendering template registered no files.")
    files = {_check_path(path): content for path, content in files.items()}
    env = SandboxedEnvironment(loader=DictLoader(files))
    context = {**model, "vars": dict(params or {}), "mickey": MICKEY_DEFAULTS}

    outputs: Dict[str, str] = {}
    for path in sorted(files):
        if _is_partial(path):
            continue
        out_path = path
        for suffix in (".j2", ".jinja"):
            if out_path.endswith(suffix):
                out_path = out_path[: -len(suffix)]
                break
        try:
            out_path = _check_path(env.from_string(out_path).render(**context))
            outputs[out_path] = env.get_template(path).render(**context)
        except TemplateError as e:
            raise ServiceException(f"Rendering {path} failed: {e}") from e
    return outputs


def render_architecture(client: HmdLangDeploymentClient, payload: Dict) -> Dict:
    """Render a scope (or a supplied model) with the newest registered version of
    a template that satisfies the version spec."""
    template = resolve_rendering_template(
        client, payload.get("template_name"), payload.get("template_version_spec")
    )
    model = payload.get("model") or build_architecture_model(client, payload)
    outputs = render_model(model, template.files or {}, payload.get("params"))
    return {
        "template_name": template.template_name,
        "template_version": template.version,
        "model": model,
        "outputs": outputs,
    }


def render_architecture_view(client: HmdLangDeploymentClient, name: str) -> Dict:
    """Render a saved view as documentation fetches it (NERD036 SPEC008).

    ``etag`` changes exactly when the model or the template version does, so a
    documentation build can tell a fresh rendering from the one it cached.
    """
    view = get_architecture_view(client, name)
    payload = {
        "roots": view.roots,
        "depth": view.depth,
        "include": view.include,
        "exclude": view.exclude,
        "environment": view.environment,
        "template_name": view.template_name,
        "template_version_spec": view.template_version_spec,
        "params": view.params,
    }
    rendered = render_architecture(client, payload)
    digest = model_digest(rendered["model"])
    etag = hashlib.sha256(
        f"{digest}:{rendered['template_name']}:{rendered['template_version']}".encode()
    ).hexdigest()
    return {
        "view_name": view.view_name,
        "title": view.title,
        "template_name": rendered["template_name"],
        "template_version": rendered["template_version"],
        "model_digest": digest,
        "etag": etag,
        "rendered_at": datetime.now(timezone.utc).isoformat(),
        "model": rendered["model"],
        "outputs": rendered["outputs"],
    }
