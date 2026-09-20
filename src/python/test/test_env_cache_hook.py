"""The core never imports redis: it talks to whatever cache is registered, or a null one."""

import sys

from hmd_lang_deployment.repo_class import RepoClass
from hmd_lang_deployment.repo_instance_deployment import RepoInstanceDeployment

from hmd_ms_deployment_core import _env_cache_hook as hook


def test_default_cache_is_null_and_disabled():
    hook.register_env_cache(None)
    cache = hook.get_env_cache()
    assert isinstance(cache, hook.NullEnvCache)
    assert cache.is_enabled is False
    assert cache.load_layers("dev", loader=None) == (None, None)
    assert cache.load_discovery_index() is None
    # Every write/invalidation is a no-op that must not raise.
    cache.store_layers("dev", [], [])
    cache.splice_status("dev", "ri", [])
    cache.store_discovery_index("[]")
    cache.invalidate_discovery_index()
    cache.invalidate_environment("dev")
    cache.invalidate_all_topology()
    cache.invalidate_all()


def test_registered_cache_is_returned_until_reset():
    sentinel = object()
    hook.register_env_cache(sentinel)
    try:
        assert hook.get_env_cache() is sentinel
    finally:
        hook.register_env_cache(None)
    assert isinstance(hook.get_env_cache(), hook.NullEnvCache)


def test_is_volatile_partitions_status_from_topology():
    assert hook.is_volatile(
        RepoInstanceDeployment(deployment_id="x", status="DEPLOYED")
    )
    assert not hook.is_volatile(RepoClass(repo_class_name="rc"))


def test_core_package_does_not_import_redis():
    for mod in list(sys.modules):
        if mod.startswith("hmd_ms_deployment_core"):
            del sys.modules[mod]
    sys.modules.pop("redis", None)
    import hmd_ms_deployment_core.operations  # noqa: F401  (pulls in every core module)

    assert "redis" not in sys.modules
