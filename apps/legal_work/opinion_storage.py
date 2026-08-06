"""The managed private blob store: content-addressed, immutable, atomic.

Two roots, and no third place a byte can come from or go to. The source root is
evidence the Chamber owns and DashKoda only reads; the store root is the copy
DashKoda owns and is answerable for. Both are settings, so nothing an operator
types and nothing a viewer sends can name a path.

The store key is derived from the document's own SHA-256, which makes three
properties fall out rather than needing enforcement: the same bytes are stored
once however many filenames they arrive under, a blob cannot be silently
replaced by different bytes, and verifying the store is re-reading files and
re-hashing them.

Writes go temporary file -> fsync -> verify size and hash -> atomic rename. A
crash therefore leaves either nothing or a complete correct blob, never a
truncated one that would later hash as corrupt.

Quarantined bytes are kept out of `blobs/` entirely. They are never addressable
by a viewer, and nothing in the request path can construct a path into that
directory.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

CHUNK_SIZE = 512 * 1024

BLOBS_DIR = "blobs"
TEMPORARY_DIR = "temporary"
QUARANTINE_DIR = "quarantine"

# Two hex characters of the digest, so a directory holds a few hundred files
# rather than tens of thousands.
FANOUT = 2


class StorageError(RuntimeError):
    """Raised when the managed store cannot honour a write or a read."""


class BlobMismatch(StorageError):
    """Raised when existing bytes disagree with the digest they are stored under.

    Never repaired automatically. A blob that does not hash to its own name is
    evidence of corruption or tampering and is an operator's decision.
    """


def source_root() -> Path:
    return Path(settings.LEGAL_OPINION_SOURCE_ROOT).resolve()


def store_root() -> Path:
    return Path(settings.LEGAL_OPINION_STORE_ROOT).resolve()


def blobs_root() -> Path:
    return store_root() / BLOBS_DIR


def storage_key(digest: str) -> str:
    """The store-relative key for a digest. Pure function of the digest."""
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise StorageError("A storage key needs a lower-case hex SHA-256 digest.")
    return f"{BLOBS_DIR}/{digest[:FANOUT]}/{digest}.pdf"


def blob_path(digest: str) -> Path:
    return store_root() / storage_key(digest)


def resolve_within_store(key: str) -> Path:
    """Resolve a stored key and prove it stays under the store root.

    The defence is `Path.resolve()` plus `is_relative_to`, not string
    inspection: it survives `..`, absolute keys, doubled separators and a
    symlink planted inside the store, because it compares the *resolved* target.
    """
    root = store_root()
    candidate = (root / key).resolve()
    if not candidate.is_relative_to(root):
        raise StorageError("Refusing a storage key that resolves outside the store.")
    return candidate


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_file(path: Path) -> tuple[str, int]:
    """Stream a file once, returning its digest and size."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


@dataclass(frozen=True)
class StoredBlob:
    """Where a document ended up, and whether this run is what put it there."""

    digest: str
    key: str
    byte_size: int
    reused: bool


def ensure_directories() -> None:
    for name in (BLOBS_DIR, TEMPORARY_DIR, QUARANTINE_DIR):
        (store_root() / name).mkdir(parents=True, exist_ok=True)


def store_blob(payload: bytes, *, expected_digest: str | None = None) -> StoredBlob:
    """Put bytes in the store under their own digest, atomically and once.

    Returns `reused=True` when the digest is already present. In that case the
    existing file is checked for size agreement and left untouched — the whole
    point of content addressing is that identical bytes need writing once.
    """
    digest = digest_bytes(payload)
    if expected_digest is not None and expected_digest != digest:
        raise BlobMismatch("The document did not hash to the digest it was announced under.")

    key = storage_key(digest)
    final = resolve_within_store(key)
    if final.exists():
        existing = final.stat().st_size
        if existing != len(payload):
            raise BlobMismatch(
                "A stored blob disagrees in size with new bytes carrying the same digest."
            )
        return StoredBlob(digest=digest, key=key, byte_size=len(payload), reused=True)

    final.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = store_root() / TEMPORARY_DIR
    temporary_dir.mkdir(parents=True, exist_ok=True)

    handle, temporary_name = tempfile.mkstemp(dir=temporary_dir, suffix=".part")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as writer:
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())

        written = temporary.stat().st_size
        if written != len(payload):
            raise StorageError("A managed blob was not written in full.")
        if digest_bytes(temporary.read_bytes()) != digest:
            raise BlobMismatch("A managed blob failed verification before being published.")

        # Atomic within a filesystem, and both paths are under the store root.
        # `replace` rather than `rename` so a concurrent writer that won the
        # race leaves correct bytes rather than an error, the bytes being
        # identical by construction.
        os.replace(temporary, final)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)

    _fsync_directory(final.parent)
    return StoredBlob(digest=digest, key=key, byte_size=len(payload), reused=False)


def quarantine_blob(payload: bytes, *, digest: str, reason: str) -> str:
    """Keep rejected bytes for diagnosis, outside anything a viewer can reach.

    Returns the store-relative key. Nothing in the request path builds a path
    into this directory, and no model field ever points at one.
    """
    safe_reason = "".join(c for c in reason if c.isalnum() or c in "-_")[:40] or "unknown"
    key = f"{QUARANTINE_DIR}/{digest[:FANOUT]}/{digest}.{safe_reason}.bin"
    target = resolve_within_store(key)
    if target.exists():
        return key
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(dir=store_root() / TEMPORARY_DIR, suffix=".part")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as writer:
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return key


def read_blob(digest: str) -> bytes:
    path = blob_path(digest)
    if not path.exists():
        raise StorageError("The managed blob is missing from the store.")
    return path.read_bytes()


def blob_exists(digest: str) -> bool:
    return blob_path(digest).exists()


def verify_blob(digest: str, *, expected_size: int | None = None) -> tuple[bool, str]:
    """Re-read and re-hash one blob. Reports; never repairs, never deletes."""
    path = blob_path(digest)
    if not path.exists():
        return False, "missing"
    actual, size = digest_file(path)
    if actual != digest:
        return False, "digest_mismatch"
    if expected_size is not None and size != expected_size:
        return False, "size_mismatch"
    return True, "ok"


def store_usage() -> dict[str, int]:
    """Aggregate counts for the verification command. No names, no paths."""
    root = blobs_root()
    if not root.exists():
        return {"blob_files": 0, "blob_bytes": 0}
    files = 0
    total = 0
    for path in root.rglob("*.pdf"):
        files += 1
        total += path.stat().st_size
    return {"blob_files": files, "blob_bytes": total}


def clear_temporary() -> int:
    """Remove abandoned partial writes. Touches only the temporary directory."""
    temporary = store_root() / TEMPORARY_DIR
    if not temporary.exists():
        return 0
    removed = 0
    for path in temporary.glob("*.part"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def _fsync_directory(path: Path) -> None:
    """Make a rename durable where the platform supports it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError, AttributeError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # Directory fsync is not available on every filesystem or platform;
        # the rename itself is still atomic, which is the property relied on.
        pass
    finally:
        os.close(fd)


def free_bytes() -> int:
    """Space left under the store root, for the verification command."""
    usage = shutil.disk_usage(store_root())
    return usage.free
