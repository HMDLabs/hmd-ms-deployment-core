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
from typing import Dict, Iterable, List, Optional, Union

from hmd_cli_tools import ServiceException
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

from ._artifact_presence_hook import PRESENT
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


def pins_from_definition(definition: List[Dict]) -> Dict[str, str]:
    """``{repo_class_name: version}`` from ChangeSet.definition / BOM items."""
    return {
        item["repo_class_name"]: item["repo_class_version"]
        for item in definition
        if item.get("repo_class_name") and item.get("repo_class_version")
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

    def release_name_of(self, rv: ReleaseVersion) -> str:
        rels = self.client.get_to_release_has_release_version_hmd_lang_deployment(rv)
        return self.rs.ref_from(rels[0]).release_name

    @staticmethod
    def pins_of(rv: ReleaseVersion) -> Dict[str, str]:
        return {a["repo_class_name"]: a["version"] for a in (rv.artifacts or [])}

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
            self.class_information.get_repo_class_version(
                pin["repo_class_name"], pin["version"]
            )
            for pin in pins
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
        lock_pins = {e["repo_class_name"]: e["version"] for e in entries}
        bom_pins = pins_from_definition(release_json.get("reference_bom") or [])
        disagree = sorted(
            f"{name} (lock {lock_pins[name]}, release.json {v})"
            for name, v in bom_pins.items()
            if name in lock_pins and lock_pins[name] != v
        )
        if disagree:
            raise ServiceException(
                "The lock and release.json disagree on: " + ", ".join(disagree)
            )

        report = []
        for entry in entries:
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
            report.append(
                {
                    "repo_class_name": entry["repo_class_name"],
                    "version": entry["version"],
                    "content_path": entry.get("content_path"),
                    "status": status,
                }
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
            }
            for e in entries
        ]
        rv = self._record_installed_version(
            release_name, version, artifacts, release_json
        )
        self._wire_present_pins(rv, report)

        return {
            "release_name": release_name,
            "version": version,
            "installed": all(e["status"] == PRESENT for e in report),
            "artifact_check": "available" if self.artifact_presence else "unavailable",
            "entries": report,
        }

    def _record_installed_version(
        self, release_name: str, version: str, artifacts: List[Dict], release_json: Dict
    ) -> ReleaseVersion:
        release = self._get_or_create_release(release_name)
        for rv in self.get_release_versions(release):
            if rv.version != version:
                continue
            if self.pins_of(rv) != {
                a["repo_class_name"]: a["version"] for a in artifacts
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
            evidence={},
            artifacts=artifacts,
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
        """Compare a set of pins (``{class: version}``) or a ChangeSet
        definition against recorded release versions.

        ``match`` is ``equal`` (identical to a verified/released version),
        ``covered`` (a subset of one, with identical versions) or ``neither``.
        ``failed`` lists failed versions whose pins equal the submitted set.
        """
        pins = (
            submitted
            if isinstance(submitted, dict)
            else pins_from_definition(submitted)
        )
        per_pin: Dict[str, List[str]] = {name: [] for name in pins}
        equal, covering, failed = [], [], []

        for release in self.client.search_release_hmd_lang_deployment({}):
            for rv in self.get_release_versions(release):
                rv_pins = self.pins_of(rv)
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
                for name, version in pins.items():
                    if rv_pins.get(name) == version:
                        per_pin[name].append(rv.version)
                if rv_pins == pins:
                    equal.append(summary)
                elif all(rv_pins.get(n) == v for n, v in pins.items()):
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
    }
