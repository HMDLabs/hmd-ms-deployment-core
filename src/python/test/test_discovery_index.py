"""Unit tests for the NERD0013 discovery catalog index (discovery_index.py).

These cover SPEC0002 (matching rules) and SPEC0003 (index construction):
  * the index holds one entry per RepoClass built from its *latest* version;
  * construction never issues per-version ``get_entity`` reads (bulk load only);
  * tolerant normalization of a missing or malformed ``discovery`` mapping;
  * free-text tokens match across summary / capability / entry-point fields;
  * ``kind`` is an exact filter and an unknown kind is rejected;
  * ``repo_class_name`` is a case-insensitive prefix filter;
  * ranking prefers a capability-name hit over a summary-only hit;
  * an empty query returns every class with its full capability list;
  * the JSON round-trip used by the Redis cache is lossless.
"""

import pytest
from hmd_cli_tools import ServiceException
from hmd_graphql_client.hmd_memory_client import MemoryClient
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_schema_loader import DefaultLoader

from hmd_ms_deployment_core.class_information import ClassInformation
from hmd_ms_deployment_core.discovery_index import (
    VALID_KINDS,
    DiscoveryEntry,
    build_index,
    index_from_json,
    index_to_json,
    normalize_discovery,
    search_index,
)


def _new_client():
    loader = DefaultLoader("schemas/local")
    return HmdLangDeploymentClient(MemoryClient(loader, {}))


VPC_DISCOVERY = {
    "summary": "Provisions the base VPC and its subnets for an account.",
    "entry_points": [
        {"path": "src/python/vpc/ops.py", "description": "Registered operations."}
    ],
    "capabilities": [
        {
            "name": "create_vpc",
            "kind": "operation",
            "description": "Create the VPC and subnets.",
            "location": "src/python/vpc/ops.py:12",
        },
        {
            "name": "GET /apiop/vpc_status",
            "kind": "endpoint",
            "description": "Report provisioning status.",
        },
    ],
    "related_docs": [{"title": "User guide", "path": "docs/user_guide.rst"}],
}

MONITORING_DISCOVERY = {
    "summary": "Ships log retention and metrics collection.",
    "capabilities": [
        {
            "name": "hmd monitoring rotate-logs",
            "kind": "cli_command",
            "description": "Rotates log files past the retention window.",
        }
    ],
}


def _entry(name, version="1.0.0", discovery=None, versions=None):
    d = normalize_discovery(discovery)
    return DiscoveryEntry(
        repo_class_name=name,
        identifier=f"id-{name}",
        version=version,
        versions=versions or ([version] if version else []),
        summary=d["summary"],
        entry_points=d["entry_points"],
        capabilities=d["capabilities"],
        related_docs=d["related_docs"],
    )


def _sample_entries():
    return [
        _entry("hmd-vpc", discovery=VPC_DISCOVERY),
        _entry("hmd-monitoring", discovery=MONITORING_DISCOVERY),
        _entry("hmd-bare"),  # registered, no discovery block
    ]


# --------------------------------------------------------------------------
# normalize_discovery
# --------------------------------------------------------------------------
def test_normalize_discovery_tolerates_missing_and_malformed():
    assert normalize_discovery(None) == {
        "summary": "",
        "entry_points": [],
        "capabilities": [],
        "related_docs": [],
    }
    # Non-list sections and non-dict items are dropped rather than raising.
    d = normalize_discovery(
        {
            "summary": 42,
            "entry_points": "nope",
            "capabilities": [{"name": "ok", "kind": "function"}, "junk", None],
            "related_docs": None,
        }
    )
    assert d["summary"] == "42"
    assert d["entry_points"] == []
    assert d["capabilities"] == [
        {"name": "ok", "kind": "function", "description": "", "location": ""}
    ]
    assert d["related_docs"] == []


# --------------------------------------------------------------------------
# SPEC0003: index construction
# --------------------------------------------------------------------------
def test_build_index_picks_latest_version_per_class():
    client = _new_client()
    ci = ClassInformation(client)
    ci.add_repo_version_by_name(
        "hmd-vpc", "1.0.0", {}, {}, discovery={"summary": "old", "capabilities": []}
    )
    ci.add_repo_version_by_name("hmd-vpc", "1.1.0", {}, {}, discovery=VPC_DISCOVERY)
    ci.add_repo_version_by_name("hmd-bare", "0.1.0", {}, {})

    entries = {e.repo_class_name: e for e in build_index(client)}

    assert set(entries) == {"hmd-vpc", "hmd-bare"}
    vpc = entries["hmd-vpc"]
    assert vpc.version == "1.1.0"
    assert vpc.versions == ["1.1.0", "1.0.0"]
    assert vpc.summary == VPC_DISCOVERY["summary"]
    assert [c["name"] for c in vpc.capabilities] == [
        "create_vpc",
        "GET /apiop/vpc_status",
    ]
    bare = entries["hmd-bare"]
    assert bare.version == "0.1.0"
    assert bare.summary == "" and bare.capabilities == []


def test_build_index_includes_classes_with_no_versions():
    client = _new_client()
    from hmd_lang_deployment.repo_class import RepoClass

    client.upsert_repo_class_hmd_lang_deployment(RepoClass(repo_class_name="empty"))

    entries = build_index(client)

    assert len(entries) == 1
    assert entries[0].repo_class_name == "empty"
    assert entries[0].version is None and entries[0].versions == []


def test_build_index_uses_two_bulk_reads_and_no_entity_lookups():
    """SPEC0003: two bulk searches (classes, versions) plus one relationship read
    per class -- never a per-version entity fetch, which is the N+1 that
    ``list_repo_classes`` used to pay."""
    client = _new_client()
    ci = ClassInformation(client)
    for i in range(3):
        ci.add_repo_version_by_name(f"rc{i}", "1.0.0", {}, {}, discovery=VPC_DISCOVERY)
        ci.add_repo_version_by_name(f"rc{i}", "1.1.0", {}, {}, discovery=VPC_DISCOVERY)

    base = client._base_client
    counts = {"search": [], "rels_from": 0, "get_entity": 0}
    orig_search = base._do_search_entity
    orig_rels_from = base._do_get_relationships_from
    orig_get = base._do_get_entity

    def counting_search(entity_name, filter_):
        counts["search"].append(entity_name)
        return orig_search(entity_name, filter_)

    def counting_rels_from(*args, **kwargs):
        counts["rels_from"] += 1
        return orig_rels_from(*args, **kwargs)

    def counting_get(*args, **kwargs):
        counts["get_entity"] += 1
        return orig_get(*args, **kwargs)

    base._do_search_entity = counting_search
    base._do_get_relationships_from = counting_rels_from
    base._do_get_entity = counting_get
    try:
        entries = build_index(client)
    finally:
        base._do_search_entity = orig_search
        base._do_get_relationships_from = orig_rels_from
        base._do_get_entity = orig_get

    assert len(entries) == 3
    assert sorted(counts["search"]) == [
        "hmd_lang_deployment.repo_class",
        "hmd_lang_deployment.repo_class_version",
    ]
    assert counts["rels_from"] == 3
    assert counts["get_entity"] == 0


# --------------------------------------------------------------------------
# SPEC0002: search semantics
# --------------------------------------------------------------------------
def test_search_matches_summary_capability_and_entry_point_tokens():
    entries = _sample_entries()

    by_summary = search_index(entries, q="metrics")
    assert [m["repo_class_name"] for m in by_summary] == ["hmd-monitoring"]
    assert by_summary[0]["matched_fields"] == ["summary"]

    by_cap_name = search_index(entries, q="create_vpc")
    assert [m["repo_class_name"] for m in by_cap_name] == ["hmd-vpc"]
    assert [c["name"] for c in by_cap_name[0]["capabilities"]] == ["create_vpc"]

    by_entry_point = search_index(entries, q="registered operations")
    assert [m["repo_class_name"] for m in by_entry_point] == ["hmd-vpc"]
    assert by_entry_point[0]["entry_points"] == VPC_DISCOVERY["entry_points"]

    # Every token must hit somewhere; case-insensitive.
    assert search_index(entries, q="VPC metrics") == []
    assert [m["repo_class_name"] for m in search_index(entries, q="SUBNETS vpc")] == [
        "hmd-vpc"
    ]


def test_search_kind_filter_exact_and_invalid_kind_raises():
    entries = _sample_entries()

    cli = search_index(entries, kind="cli_command")
    assert [m["repo_class_name"] for m in cli] == ["hmd-monitoring"]
    assert all(c["kind"] == "cli_command" for c in cli[0]["capabilities"])

    # kind combines with text: only the endpoint capability is returned.
    endpoint = search_index(entries, q="status", kind="endpoint")
    assert len(endpoint) == 1
    assert [c["name"] for c in endpoint[0]["capabilities"]] == ["GET /apiop/vpc_status"]

    assert search_index(entries, kind="function") == []
    assert set(VALID_KINDS) == {
        "endpoint",
        "cli_command",
        "function",
        "class",
        "operation",
    }
    with pytest.raises(ServiceException):
        search_index(entries, kind="bogus")


def test_search_repo_class_prefix_filter():
    entries = _sample_entries()

    assert [
        m["repo_class_name"] for m in search_index(entries, repo_class_name="HMD-M")
    ] == ["hmd-monitoring"]
    assert search_index(entries, repo_class_name="vpc") == []
    assert len(search_index(entries, repo_class_name="hmd-")) == 3


def test_search_ranking_capability_name_before_summary():
    # "rotate" appears in hmd-a's summary only and in hmd-b's capability name.
    entries = [
        _entry(
            "hmd-a", discovery={"summary": "Can rotate things.", "capabilities": []}
        ),
        _entry(
            "hmd-b",
            discovery={
                "summary": "Unrelated.",
                "capabilities": [
                    {"name": "rotate-logs", "kind": "cli_command", "description": "x"}
                ],
            },
        ),
    ]

    matches = search_index(entries, q="rotate")

    assert [m["repo_class_name"] for m in matches] == ["hmd-b", "hmd-a"]
    assert matches[0]["score"] > matches[1]["score"]
    # Equal scores break on repo_class_name; class names themselves are not
    # searched (that is what repo_class_name= is for).
    both = [
        _entry("hmd-z", discovery={"summary": "Can rotate things."}),
        _entry("hmd-y", discovery={"summary": "Can rotate things."}),
    ]
    assert [m["repo_class_name"] for m in search_index(both, q="rotate")] == [
        "hmd-y",
        "hmd-z",
    ]
    assert search_index(both, q="hmd-z") == []


def test_search_empty_query_returns_all_with_full_capabilities():
    entries = _sample_entries()

    matches = search_index(entries)

    assert [m["repo_class_name"] for m in matches] == [
        "hmd-bare",
        "hmd-monitoring",
        "hmd-vpc",
    ]
    vpc = matches[-1]
    assert vpc["capabilities"] == normalize_discovery(VPC_DISCOVERY)["capabilities"]
    assert vpc["capability_count"] == 2
    assert vpc["related_docs"] == VPC_DISCOVERY["related_docs"]
    assert vpc["version"] == "1.0.0"
    assert vpc["score"] == 0 and vpc["matched_fields"] == []
    assert matches[0]["capabilities"] == [] and matches[0]["capability_count"] == 0


# --------------------------------------------------------------------------
# SPEC0004: cache serialization round-trip
# --------------------------------------------------------------------------
def test_index_json_round_trip_is_lossless():
    entries = _sample_entries()

    restored = index_from_json(index_to_json(entries))

    assert restored == entries
