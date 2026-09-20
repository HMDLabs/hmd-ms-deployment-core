"""Private seam for the environment-graph cache.

The core never talks to Redis. Modules that *would* benefit from a cache
(``environment_information.load_environment``, ``discovery_index``) call
:func:`get_env_cache` and get whatever has been registered here -- by default
:class:`NullEnvCache`, whose every read is a miss and every write a no-op. The
premium package registers its Redis-backed ``EnvCache`` (NERD0005) at setup.

This module is an internal implementation detail shared with the premium
package, not a plugin API: keep it underscore-prefixed and undocumented.
"""

from typing import List, Optional, Tuple

from hmd_lang_deployment.repo_instance_deployment import RepoInstanceDeployment
from hmd_lang_deployment.repo_instance_deployment_has_repo_class_version import (
    RepoInstanceDeploymentHasRepoClassVersion,
)
from hmd_lang_deployment.repo_instance_has_repo_instance_deployment import (
    RepoInstanceHasRepoInstanceDeployment,
)

# Namespace names of the entities that make up the volatile "status" layer. A
# noun/relationship is topology iff it is NOT one of these.
VOLATILE_NOUN_TYPES = (RepoInstanceDeployment,)
VOLATILE_REL_TYPES = (
    RepoInstanceHasRepoInstanceDeployment,
    RepoInstanceDeploymentHasRepoClassVersion,
)


def is_volatile(entity) -> bool:
    """True if ``entity`` belongs to the volatile status layer, else topology."""
    return isinstance(entity, VOLATILE_NOUN_TYPES + VOLATILE_REL_TYPES)


class NullEnvCache:
    """A cache that is never enabled: reads miss, writes and invalidations no-op."""

    @property
    def is_enabled(self) -> bool:
        return False

    def load_layers(
        self, environment_type: str, loader
    ) -> Tuple[Optional[List], Optional[List]]:
        return None, None

    def store_layers(self, environment_type: str, topology: List, status: List) -> None:
        pass

    def splice_status(
        self, environment_type: str, repo_instance_id: str, volatile: List
    ) -> None:
        pass

    def load_discovery_index(self) -> Optional[str]:
        return None

    def store_discovery_index(self, blob: str) -> None:
        pass

    def invalidate_discovery_index(self) -> None:
        pass

    def invalidate_topology(self, environment_type: str) -> None:
        pass

    def invalidate_status(self, environment_type: str) -> None:
        pass

    def invalidate_environment(self, environment_type: str) -> None:
        pass

    def invalidate_all_topology(self) -> None:
        pass

    def invalidate_all(self) -> None:
        pass


_env_cache = None


def register_env_cache(cache) -> None:
    """Install a cache implementation (the premium ``EnvCache``); ``None`` resets to the null cache."""
    global _env_cache
    _env_cache = cache


def get_env_cache():
    """The registered cache, or a process-wide :class:`NullEnvCache`."""
    global _env_cache
    if _env_cache is None:
        _env_cache = NullEnvCache()
    return _env_cache
