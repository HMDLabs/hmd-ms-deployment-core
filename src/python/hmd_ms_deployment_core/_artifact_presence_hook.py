"""Private seam for checking that a Release's pinned artifacts have arrived.

``install_release`` (NERD0016 SPEC0005) must confirm each lock entry's build zip
is present in this control plane's Artifact Librarian at the entry's
``content_path`` with bytes matching its digest. The core does not build a
librarian client itself; the premium package registers one at setup, as it
already does for its artifact monitors. With nothing registered,
:func:`get_artifact_presence` returns ``None`` and install reports the artifact
check as unavailable.

Internal implementation detail shared with the premium package, not a plugin
API: keep it underscore-prefixed and undocumented.
"""

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PRESENT = "present"
AWAITING_REPLICATION = "awaiting_replication"
DIGEST_MISMATCH = "digest_mismatch"


class LibrarianArtifactPresence:
    """Checks a content path against an ``HmdLibrarianClient``."""

    def __init__(self, librarian_client):
        self.librarian_client = librarian_client

    def status(self, content_path: str, digest: Optional[str]) -> str:
        found = self.librarian_client._get(
            {"attribute": "content_item_path", "operator": "=", "value": content_path}
        )
        if not found:
            return AWAITING_REPLICATION
        if not digest:
            return PRESENT
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "artifact")
            self.librarian_client.download_file_from_url(found, Path(path))
            with open(path, "rb") as fl:
                actual = hashlib.sha256(fl.read()).hexdigest()
        expected = digest.split(":", 1)[-1]
        return PRESENT if actual == expected else DIGEST_MISMATCH


_factory: Optional[Callable[[], object]] = None


def register_artifact_presence(factory: Optional[Callable[[], object]]) -> None:
    """Register a zero-arg factory returning an object with
    ``status(content_path, digest) -> str``."""
    global _factory
    _factory = factory


def get_artifact_presence():
    if _factory is None:
        return None
    try:
        return _factory()
    except Exception as ex:
        logger.warning("Artifact presence check unavailable: %s", ex)
        return None
