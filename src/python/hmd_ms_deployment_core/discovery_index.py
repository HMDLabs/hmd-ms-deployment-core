"""Discovery catalog index and search (NERD0013).

Every ``RepoClassVersion`` carries the BACON ``discovery`` block copied from its
manifest (``summary``, ``entry_points``, ``capabilities``, ``related_docs``).
``discovery`` is a ``mapping`` attribute, which hmd-entity-storage persists as a
base64 blob: the entity search API cannot filter on it, and it only supports
``=``/``!=``/``<``/``>`` anyway -- no substring match. So "which repo class can
do X?" is answered here, in Python, over an index built from the graph:

* one :class:`DiscoveryEntry` per ``RepoClass``, populated from its **latest**
  version (``sort_versions`` head, the same ordering ``list_repo_classes`` and
  ``find_repo_class_versions`` present);
* built with two bulk reads (all classes, all versions) plus one relationship
  read per class -- never a per-version entity fetch;
* cached as JSON in Redis beside the NERD0005 environment graph and invalidated
  whenever a version is registered or re-synced.

Everything below :func:`build_index` is pure and unit-tested without a client.
"""

from dataclasses import asdict, dataclass, field
from json import dumps, loads
from logging import getLogger
from typing import Dict, Iterable, List, Optional

from hmd_cli_tools import ServiceException

from .class_information import ClassInformation
from .version import sort_versions

logger = getLogger(f"HMD.{__name__}")

# The BACON ``discovery.capabilities[].kind`` enum (hmd-docs-bacon schema.rst).
VALID_KINDS = ("endpoint", "cli_command", "function", "class", "operation")

# Relative weight of a text hit per field -- a capability *named* for the thing
# you asked for beats a summary that merely mentions it.
_WEIGHT_CAPABILITY_NAME = 3
_WEIGHT_SUMMARY = 2
_WEIGHT_CAPABILITY_DESCRIPTION = 1
_WEIGHT_ENTRY_POINT = 1


@dataclass
class DiscoveryEntry:
    """One repo class's slice of the catalog: its latest version's discovery."""

    repo_class_name: str
    identifier: str
    version: Optional[str]
    versions: List[str] = field(default_factory=list)
    summary: str = ""
    entry_points: List[Dict] = field(default_factory=list)
    capabilities: List[Dict] = field(default_factory=list)
    related_docs: List[Dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _str(value) -> str:
    return "" if value is None else str(value)


def _items(raw, keys: Iterable[str]) -> List[Dict]:
    """Coerce a discovery list section into dicts carrying exactly ``keys``."""
    if not isinstance(raw, list):
        return []
    return [
        {k: _str(item.get(k)) for k in keys} for item in raw if isinstance(item, dict)
    ]


def normalize_discovery(raw: Optional[Dict]) -> Dict:
    """Return a discovery mapping with every section present and well-typed.

    Manifests are hand-edited, so this tolerates a missing block, a non-list
    section, or a junk item, dropping what it cannot use rather than failing
    the whole catalog on one bad repo.
    """
    raw = raw if isinstance(raw, dict) else {}
    return {
        "summary": _str(raw.get("summary")),
        "entry_points": _items(raw.get("entry_points"), ("path", "description")),
        "capabilities": _items(
            raw.get("capabilities"), ("name", "kind", "description", "location")
        ),
        "related_docs": _items(raw.get("related_docs"), ("title", "path")),
    }


# ---------------------------------------------------------------------------
# Construction (SPEC0003)
# ---------------------------------------------------------------------------
def build_index(deploy_client) -> List[DiscoveryEntry]:
    """Build the catalog from the graph with bulk reads only.

    Classes with no registered version are still emitted (empty discovery) so
    ``list_repo_classes`` can render the whole catalog from this one index.
    """
    versions_by_id = {
        rcv.identifier: rcv
        for rcv in deploy_client.search_repo_class_version_hmd_lang_deployment({})
    }

    entries: List[DiscoveryEntry] = []
    for repo_class in deploy_client.search_repo_class_hmd_lang_deployment({}):
        rels = deploy_client.get_from_repo_class_has_repo_class_version_hmd_lang_deployment(
            repo_class
        )
        versions = []
        for rel in rels:
            rcv = versions_by_id.get(ClassInformation._ref_to_id(rel))
            if rcv is None:
                logger.warning(
                    "RepoClassVersion referenced by rel %s could not be resolved; skipping",
                    getattr(rel, "identifier", "?"),
                )
                continue
            versions.append(rcv)
        versions = sort_versions(versions, lambda rcv: rcv.version)

        latest = versions[0] if versions else None
        discovery = normalize_discovery(latest.discovery if latest else None)
        entries.append(
            DiscoveryEntry(
                repo_class_name=repo_class.repo_class_name,
                identifier=repo_class.identifier,
                version=latest.version if latest else None,
                versions=[rcv.version for rcv in versions],
                **discovery,
            )
        )

    entries.sort(key=lambda e: e.repo_class_name)
    return entries


def index_to_json(entries: List[DiscoveryEntry]) -> str:
    return dumps([asdict(e) for e in entries])


def index_from_json(blob: str) -> List[DiscoveryEntry]:
    return [DiscoveryEntry(**item) for item in loads(blob)]


def get_or_build_index(deploy_client) -> List[DiscoveryEntry]:
    """Serve the catalog from Redis when warm, else build and store it."""
    from ._env_cache_hook import get_env_cache

    cache = get_env_cache()
    blob = cache.load_discovery_index()
    if blob is not None:
        try:
            return index_from_json(blob)
        except Exception as e:  # a stale/incompatible blob is a miss, not an error
            logger.warning(f"Discovery index cache blob unreadable ({e}); rebuilding.")
    entries = build_index(deploy_client)
    cache.store_discovery_index(index_to_json(entries))
    return entries


# ---------------------------------------------------------------------------
# Search (SPEC0002)
# ---------------------------------------------------------------------------
def _hits(needle: str, *haystacks: str) -> bool:
    return any(needle in h.lower() for h in haystacks if h)


def search_index(
    entries: List[DiscoveryEntry],
    q: str = "",
    kind: Optional[str] = None,
    repo_class_name: Optional[str] = None,
) -> List[Dict]:
    """Filter and rank the catalog.

    * ``q`` is split on whitespace; **every** token must appear (case-insensitive
      substring) in the entry's summary, a capability name/description, or an
      entry point path/description. Class names are not searched -- that is what
      ``repo_class_name`` is for.
    * ``kind`` restricts the capabilities considered (and returned) to that
      exact BACON kind; an entry without one is excluded.
    * ``repo_class_name`` is a case-insensitive prefix filter.

    Each match reports only the capabilities and entry points the query hit
    (all of them when ``q`` is empty), a ``score`` (best field weight per
    token, summed) and ``matched_fields``. Results are ordered by score
    descending, then ``repo_class_name``.
    """
    if kind and kind not in VALID_KINDS:
        raise ServiceException(
            f"kind must be one of {', '.join(VALID_KINDS)}, was {kind}."
        )
    tokens = [t for t in (q or "").lower().split() if t]
    prefix = (repo_class_name or "").lower()

    matches: List[Dict] = []
    for entry in entries:
        if prefix and not entry.repo_class_name.lower().startswith(prefix):
            continue
        caps = (
            [c for c in entry.capabilities if c["kind"] == kind]
            if kind
            else list(entry.capabilities)
        )
        if kind and not caps:
            continue

        score = 0
        matched_fields: List[str] = []
        matched_caps: List[Dict] = caps
        matched_eps: List[Dict] = list(entry.entry_points)
        if tokens:
            cap_hits = set()
            ep_hits = set()
            for token in tokens:
                best = 0
                if _hits(token, entry.summary):
                    best = max(best, _WEIGHT_SUMMARY)
                    _append_unique(matched_fields, "summary")
                for i, cap in enumerate(caps):
                    if _hits(token, cap["name"]):
                        best = max(best, _WEIGHT_CAPABILITY_NAME)
                        cap_hits.add(i)
                        _append_unique(matched_fields, "capability.name")
                    elif _hits(token, cap["description"]):
                        best = max(best, _WEIGHT_CAPABILITY_DESCRIPTION)
                        cap_hits.add(i)
                        _append_unique(matched_fields, "capability.description")
                for i, ep in enumerate(entry.entry_points):
                    if _hits(token, ep["path"], ep["description"]):
                        best = max(best, _WEIGHT_ENTRY_POINT)
                        ep_hits.add(i)
                        _append_unique(matched_fields, "entry_point")
                if best == 0:
                    score = 0
                    break
                score += best
            if score == 0:
                continue
            matched_caps = [c for i, c in enumerate(caps) if i in cap_hits]
            matched_eps = [e for i, e in enumerate(entry.entry_points) if i in ep_hits]

        matches.append(
            {
                "repo_class_name": entry.repo_class_name,
                "version": entry.version,
                "summary": entry.summary,
                "score": score,
                "matched_fields": matched_fields,
                "capabilities": matched_caps,
                "entry_points": matched_eps,
                "related_docs": list(entry.related_docs),
                "capability_count": len(entry.capabilities),
            }
        )

    matches.sort(key=lambda m: (-m["score"], m["repo_class_name"]))
    return matches


def _append_unique(items: List[str], value: str) -> None:
    if value not in items:
        items.append(value)
