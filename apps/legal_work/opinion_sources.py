"""Where opinion documents come from, and the rules for reading them safely.

Two providers behind one narrow interface: the bootstrap ZIP the Chamber handed
over, and the directory that will accumulate documents from now on. Both read
only from `LEGAL_OPINION_SOURCE_ROOT`, which is configuration. **No command
accepts a path**, so there is no input — from an operator, a viewer or a
scheduled job — that can steer a read anywhere else.

A ZIP is an untrusted container even when it came from a colleague. Every entry
is checked before a single byte is read: no traversal, no absolute path, no
symlink, no nested archive, no duplicate path, nothing over the per-entry cap
and nothing whose compression ratio suggests a decompression bomb. Entries are
read into memory one at a time and **never extracted into the source
directory**, which stays exactly as the Chamber left it.

The manifest is the unit of change detection. It is deterministic — sorted by
source key — and its checksum covers each entry's identity and content digest,
so an unchanged inbox is recognised without re-validating or re-extracting
anything.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from django.conf import settings

from .opinion_storage import digest_bytes, source_root

# Entry names that are containers rather than documents.
ARCHIVE_SUFFIXES = frozenset({".zip", ".rar", ".7z", ".gz", ".tar", ".tgz", ".bz2", ".xz"})

# Partial copies and editor droppings.
TEMPORARY_PREFIXES = ("~$", ".~", "._")
TEMPORARY_SUFFIXES = (".part", ".crdownload", ".tmp", ".partial", ".filepart")

MAX_ENTRY_NAME_LENGTH = 400


class SourceRejected(RuntimeError):
    """Raised when a source container breaks a rule that makes it unsafe to read.

    Also raised when a container cannot be read *at all* — a truncated upload, a
    file that is not really a ZIP, an unsupported compression method. Those are
    the same answer as far as the catalogue is concerned: this source cannot be
    trusted, so the previous catalogue stays the answer. Letting `BadZipFile`
    escape instead would end the run in a traceback and take the feed state with
    it.
    """


# Everything `zipfile` raises when a container is corrupt rather than merely
# unusual. `BadZipFile` descends from `Exception` directly — not from `OSError`
# or `ValueError` — so it has to be named explicitly or it escapes every
# reasonable except clause.
UNREADABLE_ARCHIVE = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    NotImplementedError,
    EOFError,
    OSError,
    ValueError,
)


@dataclass(frozen=True)
class SourceEntry:
    """One document offered by a provider, already read and hashed."""

    provider: str
    key: str
    filename: str
    payload: bytes
    sha256: str
    byte_size: int
    order: int


@dataclass(frozen=True)
class ManifestEntry:
    """One document's identity, without its bytes."""

    provider: str
    key: str
    filename: str
    sha256: str
    byte_size: int
    order: int

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "key": self.key,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
        }


class OpinionSourceProvider(Protocol):
    """Everything the catalogue build needs from a source of documents."""

    name: str

    def manifest(self) -> list[ManifestEntry]: ...

    def read(self, key: str) -> SourceEntry | None: ...


def manifest_checksum(entries: list[ManifestEntry]) -> str:
    """A canonical digest over identities and content, never over bytes or paths.

    Sorted by key so the same inbox always produces the same checksum whatever
    order the filesystem or the ZIP central directory reports.
    """
    canonical = json.dumps(
        [entry.as_dict() for entry in sorted(entries, key=lambda e: (e.provider, e.key))],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_temporary(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(TEMPORARY_PREFIXES) or lowered.endswith(TEMPORARY_SUFFIXES)


def _check_entry_name(name: str) -> None:
    """Reject anything that could escape, alias or explode. Raises or returns."""
    if len(name) > MAX_ENTRY_NAME_LENGTH:
        raise SourceRejected("A source entry name is longer than the archive contract allows.")
    posix = name.replace("\\", "/")
    if posix.startswith("/"):
        raise SourceRejected("A source entry uses an absolute path.")
    if len(posix) > 1 and posix[1] == ":":
        raise SourceRejected("A source entry uses a drive-qualified path.")
    parts = posix.split("/")
    if any(part == ".." for part in parts):
        raise SourceRejected("A source entry tries to escape its container.")


class BootstrapZipProvider:
    """The handover archive. Read in place; never unpacked into the source root."""

    name = "bootstrap_zip"

    def __init__(self, path: Path | None = None):
        self.path = path or (source_root() / settings.LEGAL_OPINION_BOOTSTRAP_ZIP_NAME)

    def exists(self) -> bool:
        return self.path.is_file()

    def _entries(self) -> list[zipfile.ZipInfo]:
        try:
            return self._checked_entries()
        except SourceRejected:
            raise
        except UNREADABLE_ARCHIVE as error:
            raise SourceRejected("The bootstrap archive could not be read.") from error

    def _checked_entries(self) -> list[zipfile.ZipInfo]:
        with zipfile.ZipFile(self.path) as archive:
            infos = archive.infolist()

            if len(infos) > settings.LEGAL_OPINION_MAX_SOURCE_ENTRIES:
                raise SourceRejected("The archive holds more entries than the contract allows.")

            seen: set[str] = set()
            keep: list[zipfile.ZipInfo] = []
            for info in infos:
                _check_entry_name(info.filename)

                if info.filename in seen:
                    raise SourceRejected("The archive holds a duplicate entry path.")
                seen.add(info.filename)

                # Unix mode sits in the top 16 bits; 0xA000 is a symlink.
                mode = info.external_attr >> 16
                if mode and (mode & 0xF000) == 0xA000:
                    raise SourceRejected("The archive holds a symbolic link.")

                if info.is_dir():
                    continue

                suffix = Path(info.filename.replace("\\", "/")).suffix.lower()
                if suffix in ARCHIVE_SUFFIXES:
                    raise SourceRejected("The archive holds a nested archive.")
                if info.file_size > settings.LEGAL_OPINION_MAX_PDF_BYTES:
                    raise SourceRejected("An archive entry is larger than the contract allows.")
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > settings.LEGAL_OPINION_MAX_ZIP_RATIO:
                        raise SourceRejected("An archive entry decompresses implausibly far.")

                if suffix != ".pdf" or _is_temporary(Path(info.filename).name):
                    # Not a document. Ignored rather than rejected: a stray
                    # readme must not make the whole handover unreadable.
                    continue
                keep.append(info)

            keep.sort(key=lambda info: info.filename)
            return keep

    def manifest(self) -> list[ManifestEntry]:
        entries: list[ManifestEntry] = []
        try:
            with zipfile.ZipFile(self.path) as archive:
                for order, info in enumerate(self._entries()):
                    payload = archive.read(info)
                    entries.append(
                        ManifestEntry(
                            provider=self.name,
                            key=info.filename,
                            filename=Path(info.filename.replace("\\", "/")).name,
                            sha256=digest_bytes(payload),
                            byte_size=len(payload),
                            order=order,
                        )
                    )
        except SourceRejected:
            raise
        except UNREADABLE_ARCHIVE as error:
            raise SourceRejected("The bootstrap archive could not be read.") from error
        return entries

    def read(self, key: str) -> SourceEntry | None:
        _check_entry_name(key)
        try:
            with zipfile.ZipFile(self.path) as archive:
                try:
                    info = archive.getinfo(key)
                except KeyError:
                    return None
                if info.file_size > settings.LEGAL_OPINION_MAX_PDF_BYTES:
                    raise SourceRejected("An archive entry is larger than the contract allows.")
                payload = archive.read(info)
        except SourceRejected:
            raise
        except UNREADABLE_ARCHIVE as error:
            raise SourceRejected("The bootstrap archive could not be read.") from error
        return SourceEntry(
            provider=self.name,
            key=key,
            filename=Path(key.replace("\\", "/")).name,
            payload=payload,
            sha256=digest_bytes(payload),
            byte_size=len(payload),
            order=0,
        )


class DirectoryProvider:
    """The recurring inbox: loose PDFs, or year folders of them.

    Only files that have stopped changing are offered. A PDF still being copied
    would otherwise be hashed half-written and stored as a blob that is complete
    by definition and wrong in fact.
    """

    name = "directory"

    def __init__(self, root: Path | None = None):
        self.root = (root or source_root()).resolve()

    def _candidates(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                # Never followed: a link is how a read escapes its root.
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() != ".pdf":
                continue
            if _is_temporary(path.name):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            if not self._is_stable(resolved):
                continue
            yield resolved

    def _is_stable(self, path: Path) -> bool:
        """A file is readable once it has stopped growing."""
        try:
            stat = path.stat()
        except OSError:
            return False
        age = time.time() - stat.st_mtime
        return age >= settings.LEGAL_OPINION_MIN_STABLE_AGE_SECONDS

    def _key(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def manifest(self) -> list[ManifestEntry]:
        entries: list[ManifestEntry] = []
        for order, path in enumerate(self._candidates()):
            if order >= settings.LEGAL_OPINION_MAX_SOURCE_ENTRIES:
                raise SourceRejected("The source directory holds more files than allowed.")
            payload = path.read_bytes()
            entries.append(
                ManifestEntry(
                    provider=self.name,
                    key=self._key(path),
                    filename=path.name,
                    sha256=digest_bytes(payload),
                    byte_size=len(payload),
                    order=order,
                )
            )
        return entries

    def read(self, key: str) -> SourceEntry | None:
        _check_entry_name(key)
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise SourceRejected("Refusing a source key that resolves outside the source root.")
        if not candidate.is_file() or candidate.is_symlink():
            return None
        payload = candidate.read_bytes()
        if len(payload) > settings.LEGAL_OPINION_MAX_PDF_BYTES:
            raise SourceRejected("A source file is larger than the contract allows.")
        return SourceEntry(
            provider=self.name,
            key=key,
            filename=candidate.name,
            payload=payload,
            sha256=digest_bytes(payload),
            byte_size=len(payload),
            order=0,
        )


def available_providers() -> list[OpinionSourceProvider]:
    """Every configured provider that currently has something to offer.

    The ZIP comes first so that during the Phase 3 transition, when the same
    documents exist both loose and inside the handover archive, the manifest
    order is stable. Identical bytes deduplicate to one blob regardless.
    """
    providers: list[OpinionSourceProvider] = []
    bootstrap = BootstrapZipProvider()
    if bootstrap.exists():
        providers.append(bootstrap)
    providers.append(DirectoryProvider())
    return providers


def collect_manifest() -> tuple[list[ManifestEntry], str]:
    """Every document every provider offers, plus the checksum over all of it.

    A document present in both the ZIP and the directory appears once: the same
    bytes are the same document however many places hold a copy.
    """
    entries: list[ManifestEntry] = []
    seen_digests: set[str] = set()
    order = 0
    for provider in available_providers():
        for entry in provider.manifest():
            if entry.sha256 in seen_digests:
                continue
            seen_digests.add(entry.sha256)
            entries.append(
                ManifestEntry(
                    provider=entry.provider,
                    key=entry.key,
                    filename=entry.filename,
                    sha256=entry.sha256,
                    byte_size=entry.byte_size,
                    order=order,
                )
            )
            order += 1
    return entries, manifest_checksum(entries)


def read_entry(provider_name: str, key: str) -> SourceEntry | None:
    for provider in available_providers():
        if provider.name == provider_name:
            return provider.read(key)
    return None
