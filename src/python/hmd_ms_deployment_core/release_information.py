"""Release registry (NERD0016).

A Release is a named series of ReleaseVersions; a ReleaseVersion is an exact,
immutable set of RepoClassVersion pins plus the tested environment's BOM. It
records what was *tested together* and moves through the SPEC0017 lifecycle::

    candidate -> verifying -> verified -> released
                           -> failed
    candidate | verified -> superseded

Pins are stored twice: as ``release_version_pins_repo_class_version`` edges
(graph queries: "which releases pin this version") and on the
``artifacts`` collection, which is read whole and carries each pin's librarian
``content_path`` and digest.
"""

import copy
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from hmd_base_service.exceptions import ServiceException
from hmd_lang_deployment.bundle_version import BundleVersion
from hmd_lang_deployment.hmd_lang_deployment_client import HmdLangDeploymentClient
from hmd_lang_deployment.release import Release
from hmd_lang_deployment.release_has_release_version import (
    ReleaseHasReleaseVersion,
)
from hmd_lang_deployment.release_version import ReleaseVersion
from hmd_lang_deployment.release_version_based_on_release_version import (
    ReleaseVersionBasedOnReleaseVersion,
)
from hmd_lang_deployment.release_version_from_bundle_version import (
    ReleaseVersionFromBundleVersion,
)
from hmd_lang_deployment.release_version_pins_repo_class_version import (
    ReleaseVersionPinsRepoClassVersion,
)

from ._artifact_presence_hook import DIGEST_MISMATCH, PRESENT
from .class_information import ClassInformation
from .version import VersionSpecifier, sort_versions

logger = logging.getLogger(__name__)

CANDIDATE = "candidate"
VERIFYING = "verifying"
VERIFIED = "verified"
FAILED = "failed"
RELEASED = "released"
SUPERSEDED = "superseded"

ALLOWED_TRANSITIONS = {
    CANDIDATE: {VERIFYING, SUPERSEDED},
    VERIFYING: {VERIFIED, FAILED},
    VERIFIED: {RELEASED, SUPERSEDED},
    FAILED: set(),
    RELEASED: set(),
    SUPERSEDED: set(),
}

AWAITING_REGISTRATION = "awaiting_registration"

# neuronsphere.lock (hmd-cli-neuronsphere internal/lock): the schema version it
# writes, its header keys, and the entry keys a release keeps beyond its own.
LOCK_SCHEMA_VERSION = 1
_LOCK_HEADER = ("version", "repo_class_name", "generated_from")
_LOCK_ENTRY_EXTRAS = ("profiles", "satisfies", "source")


PinSet = Set[Tuple[str, str]]


def pin_set_from_definition(definition: List[Dict]) -> PinSet:
    """``{(repo_class_name, version)}`` from ChangeSet.definition / BOM items.

    A pin is a (class, version) pair, not a class: one environment routinely
    runs a generic class (a bucket, credentials) at several versions at once."""
    return {
        (item["repo_class_name"], item["repo_class_version"])
        for item in definition
        if item.get("repo_class_name") and item.get("repo_class_version")
    }


def pin_set_from_mapping(pins: Dict) -> PinSet:
    """``{class: version | [versions]}`` as a pin set."""
    out = set()
    for cls, versions in pins.items():
        for version in versions if isinstance(versions, list) else [versions]:
            out.add((cls, version))
    return out


def pins_mapping(pin_set: PinSet) -> Dict:
    """A pin set for display: ``{class: version}``, or a sorted list of
    versions for a class pinned at several."""
    grouped: Dict[str, List[str]] = {}
    for cls, version in pin_set:
        grouped.setdefault(cls, []).append(version)
    return {
        cls: versions[0] if len(versions) == 1 else sorted(versions)
        for cls, versions in sorted(grouped.items())
    }


class ReleaseInformation:
    def __init__(self, client: HmdLangDeploymentClient, artifact_presence=None):
        self.client = client
        self.class_information = ClassInformation(client)
        self.rs = self.class_information.relationship_support
        self.artifact_presence = artifact_presence

    # --- lookup ----------------------------------------------------------------

    def get_release(self, release_name: str) -> Release:
        releases = self.client.search_release_hmd_lang_deployment(
            {"attribute": "release_name", "operator": "=", "value": release_name}
        )
        if len(releases) != 1:
            raise ServiceException(f"Release, {release_name}, not found.")
        return releases[0]

    def _get_or_create_release(self, release_name: str) -> Release:
        try:
            return self.get_release(release_name)
        except ServiceException:
            release = Release(release_name=release_name)
            self.client.upsert(release)
            return release

    def get_release_versions(self, release: Release) -> List[ReleaseVersion]:
        return [
            self.rs.ref_to(rel)
            for rel in self.client.get_from_release_has_release_version_hmd_lang_deployment(
                release
            )
        ]

    def get_release_version(
        self,
        release_name: str,
        version: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
    ) -> ReleaseVersion:
        """Exact ``version`` if given, else the newest version (optionally
        restricted to ``statuses``)."""
        versions = self.get_release_versions(self.get_release(release_name))
        if version:
            matches = [rv for rv in versions if rv.version == version]
            if not matches:
                raise ServiceException(
                    f"No version found for release {release_name} : {version}."
                )
            return matches[0]
        if statuses is not None:
            statuses = set(statuses)
            versions = [rv for rv in versions if rv.status in statuses]
        if not versions:
            raise ServiceException(f"Release, {release_name}, has no matching version.")
        return sort_versions(versions, lambda rv: rv.version)[0]

    def included_versions(self, rv: ReleaseVersion) -> List[Tuple[str, ReleaseVersion]]:
        """The release versions a platform release includes, as
        ``(release_name, version)`` pairs."""
        included = []
        for rel in self.client.get_from_release_version_includes_release_version_hmd_lang_deployment(
            rv
        ):
            target = self.rs.ref_to(rel)
            included.append((self.release_name_of(target), target))
        return sorted(included, key=lambda pair: pair[0])

    @staticmethod
    def is_installed_version(rv: ReleaseVersion) -> Optional[bool]:
        """``None`` until an install report exists, then whether it is complete."""
        report = rv.install_report
        return bool(report.get("installed")) if report else None

    def release_version_row(self, release_name: str, rv: ReleaseVersion) -> Dict:
        """``release_version_to_dict`` plus what the detail page needs: the
        releases it includes."""
        return {
            **release_version_to_dict(release_name, rv),
            "includes": [
                {"release_name": name, "version": included.version}
                for name, included in self.included_versions(rv)
            ],
        }

    def list_release_versions(self, release_name: str) -> List[Dict]:
        """Every version of a release, newest first, with its status and
        whether it is installed (``None`` if never reported)."""
        versions = sort_versions(
            self.get_release_versions(self.get_release(release_name)),
            lambda rv: rv.version,
        )
        return [
            {
                "release_name": release_name,
                "version": rv.version,
                "status": rv.status,
                "installed": self.is_installed_version(rv),
            }
            for rv in versions
        ]

    def list_releases(self) -> List[Dict]:
        """The catalogue: one row per release with its newest version per
        status and its newest installed version. ``kind`` is ``platform`` when
        any version includes other releases, else ``bundle``."""
        rows = []
        for release in sorted(
            self.client.search_release_hmd_lang_deployment({}),
            key=lambda r: r.release_name,
        ):
            versions = sort_versions(
                self.get_release_versions(release), lambda rv: rv.version
            )
            newest_by_status: Dict[str, str] = {}
            for rv in versions:
                newest_by_status.setdefault(rv.status, rv.version)
            installed = [rv for rv in versions if self.is_installed_version(rv)]
            rows.append(
                {
                    "release_name": release.release_name,
                    "kind": "platform"
                    if any(self.included_versions(rv) for rv in versions)
                    else "bundle",
                    "versions": [rv.version for rv in versions],
                    "newest_by_status": newest_by_status,
                    "newest_installed": installed[0].version if installed else None,
                }
            )
        return rows

    def release_name_of(self, rv: ReleaseVersion) -> str:
        rels = self.client.get_to_release_has_release_version_hmd_lang_deployment(rv)
        return self.rs.ref_from(rels[0]).release_name

    @staticmethod
    def pin_set_of(rv: ReleaseVersion) -> PinSet:
        return {(a["repo_class_name"], a["version"]) for a in (rv.artifacts or [])}

    @staticmethod
    def pins_of(rv: ReleaseVersion) -> Dict:
        """``{class: version}``, or ``{class: [versions]}`` for a class pinned
        at several versions."""
        return pins_mapping(ReleaseInformation.pin_set_of(rv))

    # --- create / lifecycle ----------------------------------------------------

    def _next_version(self, release: Release) -> str:
        """``YYYY.MMDD.N`` -- numeric, so ``sort_versions`` orders it, and
        monotonically increasing within a day."""
        today = datetime.now(timezone.utc)
        prefix = f"{today.year}.{today.month:02d}{today.day:02d}"
        taken = [
            rv.version
            for rv in self.get_release_versions(release)
            if rv.version.startswith(prefix + ".")
        ]
        ordinal = max([int(v.rsplit(".", 1)[1]) for v in taken] or [0]) + 1
        return f"{prefix}.{ordinal}"

    def create_release_version(
        self,
        release_name: str,
        pins: List[Dict],
        version: Optional[str] = None,
        status: str = CANDIDATE,
        reference_bom: Optional[List[Dict]] = None,
        config_policy: Optional[Dict] = None,
        toolchain: Optional[Dict] = None,
        default_configuration: Optional[Dict] = None,
        discovery: Optional[Dict] = None,
        attestation: Optional[Dict] = None,
        source: Optional[str] = None,
        digest: Optional[str] = None,
        based_on: Optional[ReleaseVersion] = None,
        from_bundle: Optional[BundleVersion] = None,
    ) -> ReleaseVersion:
        """Create a ReleaseVersion pinning ``pins`` (``[{repo_class_name,
        version, content_path?, digest?, external_artifacts?}]``). Every pin
        must name a registered RepoClassVersion."""
        if status not in ALLOWED_TRANSITIONS:
            raise ServiceException(f"Unknown release status: {status}.")
        release = self._get_or_create_release(release_name)
        version = version or self._next_version(release)
        VersionSpecifier.validate_version_number(version)
        if any(rv.version == version for rv in self.get_release_versions(release)):
            raise ServiceException(
                f"Release, {release_name}, already has version {version}."
            )

        rcvs = [
            self.class_information.get_repo_class_version(cls, version)
            for cls, version in sorted(
                {(pin["repo_class_name"], pin["version"]) for pin in pins}
            )
        ]

        rv = ReleaseVersion(
            version=version,
            status=status,
            source=source,
            digest=digest,
            toolchain=toolchain,
            reference_bom=reference_bom or [],
            config_policy=config_policy,
            attestation=attestation,
            default_configuration=default_configuration,
            discovery=discovery,
            evidence={},
            artifacts=copy.deepcopy(pins),
        )
        self.client.upsert(rv)
        self.client.upsert(
            ReleaseHasReleaseVersion(ref_from=release.identifier, ref_to=rv.identifier)
        )
        for rcv in rcvs:
            self.client.upsert(
                ReleaseVersionPinsRepoClassVersion(
                    ref_from=rv.identifier, ref_to=rcv.identifier
                )
            )
        if based_on is not None:
            self.client.upsert(
                ReleaseVersionBasedOnReleaseVersion(
                    ref_from=rv.identifier, ref_to=based_on.identifier
                )
            )
        if from_bundle is not None:
            self.client.upsert(
                ReleaseVersionFromBundleVersion(
                    ref_from=rv.identifier, ref_to=from_bundle.identifier
                )
            )
        return rv

    def transition(
        self, rv: ReleaseVersion, status: str, evidence: Optional[Dict] = None
    ) -> ReleaseVersion:
        """Move ``rv`` to ``status`` per SPEC0017. Re-applying the current status
        is a no-op, so a duplicated status callback is harmless."""
        if status == rv.status:
            return rv
        if status not in ALLOWED_TRANSITIONS:
            raise ServiceException(f"Unknown release status: {status}.")
        if status not in ALLOWED_TRANSITIONS.get(rv.status, set()):
            raise ServiceException(
                f"Release version {rv.version} cannot move from {rv.status} to {status}."
            )
        rv.status = status
        if evidence:
            rv.evidence = {**(rv.evidence or {}), **evidence}
        self.client.upsert(rv)
        return rv

    # --- install (SPEC0005) ----------------------------------------------------

    def _entry_status(self, entry: Dict, previous: Optional[str] = None) -> str:
        """One lock entry's arrival status. An artifact already found present
        stays present, and a digest mismatch is final, so a retry neither
        re-downloads nor re-hashes what it has already settled."""
        if previous == DIGEST_MISMATCH:
            return DIGEST_MISMATCH
        status = PRESENT if previous == PRESENT else None
        if status is None:
            status = PRESENT
            if self.artifact_presence is not None:
                status = self.artifact_presence.status(
                    entry.get("content_path"), entry.get("digest")
                )
        if status == PRESENT:
            try:
                self.class_information.get_repo_class_version(
                    entry["repo_class_name"], entry["version"]
                )
            except ServiceException:
                status = AWAITING_REGISTRATION
        return status

    def _report(self, entries: List[Dict], previous: Optional[Dict] = None) -> Dict:
        before = {
            (e["repo_class_name"], e["version"]): e.get("status")
            for e in ((previous or {}).get("entries") or [])
        }
        report = [
            {
                "repo_class_name": e["repo_class_name"],
                "version": e["version"],
                "content_path": e.get("content_path"),
                "status": self._entry_status(
                    e, before.get((e["repo_class_name"], e["version"]))
                ),
            }
            for e in entries
        ]
        return {
            "installed": all(e["status"] == PRESENT for e in report),
            "artifact_check": "available" if self.artifact_presence else "unavailable",
            "entries": report,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def install_status(
        self, release_name: str, version: str, refresh: bool = False
    ) -> Dict:
        """The install report of an installed release. The stored one by
        default; with ``refresh``, recomputed now -- still without writing."""
        rv = self.get_release_version(release_name, version)
        report = (
            self._report(rv.artifacts or [], rv.install_report)
            if refresh
            else rv.install_report
        )
        if not report:
            return {
                "release_name": release_name,
                "version": rv.version,
                "installed": None,
                "entries": [],
            }
        return {"release_name": release_name, "version": rv.version, **report}

    def pending_installs(self) -> List[ReleaseVersion]:
        """Installed releases still waiting for artifacts to arrive or be
        registered; a digest mismatch is not pending, it is blocked."""
        pending = []
        for release in self.client.search_release_hmd_lang_deployment({}):
            for rv in self.get_release_versions(release):
                report = rv.install_report or {}
                if not report or report.get("installed"):
                    continue
                if any(
                    e.get("status") == DIGEST_MISMATCH
                    for e in report.get("entries") or []
                ):
                    continue
                pending.append(rv)
        return pending

    def reinstall(self, rv: ReleaseVersion) -> Dict:
        """Re-run an installed release's install from what it recorded."""
        return self.install_release(
            {
                "repo_class_name": self.release_name_of(rv),
                **(rv.lock_header or {}),
                "resolved": rv.artifacts or [],
            },
            {
                "release_name": self.release_name_of(rv),
                "version": rv.version,
                "reference_bom": rv.reference_bom or [],
                "config_policy": rv.config_policy,
            },
        )

    def install_release(self, lock: Dict, release_json: Dict) -> Dict:
        """Record a Release delivered to this control plane and report whether
        its pinned artifacts have arrived.

        Moves no bytes and deploys nothing: the zips arrive by librarian
        replication and are registered by the ArtifactMonitor. Each lock entry
        is ``present`` (artifact here with matching digest and its version
        registered), ``awaiting_replication``, ``digest_mismatch`` or
        ``awaiting_registration`` (artifact here, version not yet registered).
        The Release is installed when every entry is ``present``.
        """
        release_name = release_json.get("release_name")
        version = release_json.get("version")
        if not release_name or not version:
            raise ServiceException(
                "release.json requires 'release_name' and 'version'."
            )
        entries = lock.get("resolved") or []
        if not entries:
            raise ServiceException("The lock pins no repo classes.")

        # The lock is the sole authority on versions; release.json must not
        # disagree with it (SPEC0003).
        lock_pins = {(e["repo_class_name"], e["version"]) for e in entries}
        bom_pins = pin_set_from_definition(release_json.get("reference_bom") or [])
        locked_classes = {cls for cls, _ in lock_pins}
        disagree = sorted(
            f"{cls} {version} (not pinned by the lock)"
            for cls, version in bom_pins - lock_pins
            if cls in locked_classes
        )
        if disagree:
            raise ServiceException(
                "The lock and release.json disagree on: " + ", ".join(disagree)
            )

        artifacts = [
            {
                "repo_class_name": e["repo_class_name"],
                "version": e["version"],
                "content_path": e.get("content_path"),
                "digest": e.get("digest"),
                "external_artifacts": (release_json.get("images") or {}).get(
                    e["repo_class_name"], []
                ),
                # The lock's own fields, kept so the lock can be exported
                # unchanged (NERD0019 SPEC0005).
                **{k: e[k] for k in _LOCK_ENTRY_EXTRAS if k in e},
            }
            for e in entries
        ]
        lock_header = {k: lock[k] for k in _LOCK_HEADER if k in lock}
        rv = self._record_installed_version(
            release_name,
            version,
            artifacts,
            {**release_json, "lock_header": lock_header},
        )
        install_report = self._report(entries, rv.install_report)
        self._wire_present_pins(rv, install_report["entries"])
        rv.install_report = install_report
        self.client.upsert(rv)
        return {"release_name": release_name, "version": version, **install_report}

    def release_version_lock(self, release_name: str, version: str) -> Dict:
        """A release's pin set as a ``neuronsphere.lock`` (JSON form), in pin
        order (NERD0019 SPEC0005). A release installed from a lock exports
        that lock; one cut here is named after the release."""
        rv = self.get_release_version(release_name, version)
        header = rv.lock_header or {
            "version": LOCK_SCHEMA_VERSION,
            "repo_class_name": release_name,
            "generated_from": f"release:{release_name}@{rv.version}",
        }
        resolved = []
        for a in rv.artifacts or []:
            entry = {
                "repo_class_name": a["repo_class_name"],
                "version": a["version"],
                "profiles": a.get("profiles") or [],
            }
            if a.get("satisfies"):
                entry["satisfies"] = a["satisfies"]
            entry["content_path"] = a.get("content_path") or ""
            for key in ("digest", "source"):
                if a.get(key):
                    entry[key] = a[key]
            resolved.append(entry)
        return {**header, "resolved": resolved}

    def _record_installed_version(
        self, release_name: str, version: str, artifacts: List[Dict], release_json: Dict
    ) -> ReleaseVersion:
        release = self._get_or_create_release(release_name)
        for rv in self.get_release_versions(release):
            if rv.version != version:
                continue
            if self.pin_set_of(rv) != {
                (a["repo_class_name"], a["version"]) for a in artifacts
            }:
                raise ServiceException(
                    f"Release, {release_name}, already has version {version} with different pins."
                )
            return rv
        rv = ReleaseVersion(
            version=version,
            status=RELEASED,
            source=release_json.get("source"),
            digest=release_json.get("digest"),
            toolchain=release_json.get("toolchain"),
            reference_bom=release_json.get("reference_bom") or [],
            config_policy=release_json.get("config_policy"),
            attestation=release_json.get("attestation"),
            default_configuration=release_json.get("default_configuration"),
            discovery=release_json.get("discovery"),
            # The publisher's evidence travels with the release, so the
            # receiver sees how it was verified (NERD0016 SPEC0028).
            evidence=release_json.get("evidence") or {},
            notes=release_json.get("notes") or [],
            artifacts=artifacts,
            lock_header=release_json.get("lock_header") or None,
        )
        self.client.upsert(rv)
        self.client.upsert(
            ReleaseHasReleaseVersion(ref_from=release.identifier, ref_to=rv.identifier)
        )
        return rv

    def _wire_present_pins(self, rv: ReleaseVersion, report: List[Dict]) -> None:
        wired = {
            ClassInformation._ref_to_id(rel)
            for rel in self.client.get_from_release_version_pins_repo_class_version_hmd_lang_deployment(
                rv
            )
        }
        for entry in report:
            if entry["status"] != PRESENT:
                continue
            rcv = self.class_information.get_repo_class_version(
                entry["repo_class_name"], entry["version"]
            )
            if rcv.identifier not in wired:
                self.client.upsert(
                    ReleaseVersionPinsRepoClassVersion(
                        ref_from=rv.identifier, ref_to=rcv.identifier
                    )
                )
                wired.add(rcv.identifier)

    def is_installed(self, rv: ReleaseVersion) -> bool:
        """Every pin is registered here and, when an artifact check is
        available, present in the librarian."""
        for artifact in rv.artifacts or []:
            if self.artifact_presence is not None and (
                self.artifact_presence.status(
                    artifact.get("content_path"), artifact.get("digest")
                )
                != PRESENT
            ):
                return False
            try:
                self.class_information.get_repo_class_version(
                    artifact["repo_class_name"], artifact["version"]
                )
            except ServiceException:
                return False
        return True

    # --- coverage (SPEC0022) ---------------------------------------------------

    def check_release_coverage(
        self, submitted: Union[Dict[str, str], List[Dict]]
    ) -> Dict:
        """Compare a set of pins (``{class: version | [versions]}``) or a
        ChangeSet definition against recorded release versions. ``pins`` in the
        result is keyed ``class@version``.

        ``match`` is ``equal`` (identical to a verified/released version),
        ``covered`` (a subset of one, with identical versions) or ``neither``.
        ``failed`` lists failed versions whose pins equal the submitted set.
        """
        pins = (
            pin_set_from_mapping(submitted)
            if isinstance(submitted, dict)
            else pin_set_from_definition(submitted)
        )
        per_pin: Dict[str, List[str]] = {f"{c}@{v}": [] for c, v in sorted(pins)}
        equal, covering, failed = [], [], []

        for release in self.client.search_release_hmd_lang_deployment({}):
            for rv in self.get_release_versions(release):
                rv_pins = self.pin_set_of(rv)
                summary = {
                    "release_name": release.release_name,
                    "version": rv.version,
                    "status": rv.status,
                }
                if rv.status == FAILED:
                    if rv_pins == pins:
                        failed.append(summary)
                    continue
                if rv.status not in (VERIFIED, RELEASED):
                    continue
                for cls, version in pins & rv_pins:
                    per_pin[f"{cls}@{version}"].append(rv.version)
                if rv_pins == pins:
                    equal.append(summary)
                elif pins <= rv_pins:
                    covering.append(summary)

        if equal:
            match, matched = "equal", equal
        elif covering:
            match, matched = "covered", covering
        else:
            match, matched = "neither", []
        return {
            "match": match,
            "release_versions": matched,
            "pins": per_pin,
            "failed": failed,
        }


def release_version_to_dict(release_name: str, rv: ReleaseVersion) -> Dict:
    return {
        "release_name": release_name,
        "version": rv.version,
        "identifier": rv.identifier,
        "status": rv.status,
        "source": rv.source,
        "digest": rv.digest,
        "pins": ReleaseInformation.pins_of(rv),
        "artifacts": rv.artifacts,
        "reference_bom": rv.reference_bom,
        "config_policy": rv.config_policy,
        "toolchain": rv.toolchain,
        "attestation": rv.attestation,
        "evidence": rv.evidence,
        "discovery": rv.discovery,
        "notes": rv.notes,
        "install_report": rv.install_report,
    }
