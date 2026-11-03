"""``load_environment`` through the env-graph cache (NERD0005).

The cache-hit branch rehydrates freshly deserialized copies of every entity. When the
client's instance cache already holds an equal entity (an earlier load on the same
client), ``BaseClient.cache_instance`` keeps the existing object, and relationship
links attach to that one. ``EnvironmentInformation`` must hold the same objects, or
its RepoInstances have no edges and the BOM cannot resolve their class
(``IndexError`` in ``serialize_repo_instance``), which broke every release export.
"""

from typing import List, Tuple

import pytest
from hmd_graphql_client.hmd_db_engine_client import DbEngineClient
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_meta_types import Entity

from hmd_ms_deployment_core import _env_cache_hook as hook
from hmd_ms_deployment_core.bom import build_environment_bom
from hmd_ms_deployment_core.environment_information import EnvironmentInformation


class _SerializingCache:
    """Hands back new deserialized copies on every hit, as the Redis cache does."""

    is_enabled = True

    def __init__(self, topology: List[Entity], status: List[Entity]):
        self._topology = [(type(e), e.serialize()) for e in topology]
        self._status = [(type(e), e.serialize()) for e in status]

    def load_layers(self, environment_type, loader) -> Tuple[List, List]:
        return (
            [Entity.deserialize(t, d) for t, d in self._topology],
            [Entity.deserialize(t, d) for t, d in self._status],
        )

    def store_layers(self, *args):
        pass


@pytest.fixture()
def cached_dev_env(base_environment):
    """A DbEngineClient-backed dev environment whose graph comes only from the cache."""
    mem, _ = base_environment
    env = mem.search_environment_hmd_lang_deployment(
        {"attribute": "type", "operator": "=", "value": "dev"}
    )[0]
    source = EnvironmentInformation(env, mem)
    source.load_environment()
    entities = [e for by_id in source.nouns.values() for e in by_id.values()] + [
        r for by_id in source.relationships.values() for r in by_id.values()
    ]
    hook.register_env_cache(
        _SerializingCache(
            [e for e in entities if not hook.is_volatile(e)],
            [e for e in entities if hook.is_volatile(e)],
        )
    )
    client = HmdLangDeploymentClient(DbEngineClient(None, mem._base_client.loader))
    yield client, env
    hook.register_env_cache(None)


def test_single_cached_load_builds_the_bom(cached_dev_env):
    client, env = cached_dev_env
    bom = build_environment_bom(EnvironmentInformation(env, client))
    assert {e["repo_instance_name"] for e in bom} == {"ri1", "ri2", "ri3"}


def test_second_cached_load_keeps_relationships(cached_dev_env):
    # release_resolution._env_info loads, then build_environment_bom loads again.
    client, env = cached_dev_env
    env_info = EnvironmentInformation(env, client)
    env_info.load_environment()
    bom = build_environment_bom(env_info)
    by_name = {e["repo_instance_name"]: e for e in bom}
    assert set(by_name) == {"ri1", "ri2", "ri3"}
    assert by_name["ri1"]["repo_class_name"] == "rc1"
    assert by_name["ri1"]["dependencies"] == {"rc1-rc2": "ri2", "rc1-rc3": "ri3"}


def test_cached_load_on_a_warm_client_keeps_relationships(cached_dev_env):
    # A second EnvironmentInformation on the same client (e.g. a later operation in
    # the same request) also rehydrates over an already-populated instance cache.
    client, env = cached_dev_env
    EnvironmentInformation(env, client).load_environment()
    bom = build_environment_bom(EnvironmentInformation(env, client))
    assert {e["repo_instance_name"] for e in bom} == {"ri1", "ri2", "ri3"}
