"""The mechanical half of a public-workbook synchronisation.

Two feeds collect a workbook from a public OneDrive sharing link — the legal-work
one and the event-programme one — and the sequence between the download and the
import was written out twice, near enough character for character: stamp the
check, open a temporary directory, download into it, classify whatever the
downloader raised, look the checksum up, register the artifact if it is new,
call the importer, and classify whatever *that* raised.

That is transport and bookkeeping. It is the same for any workbook because it is
about files and import runs, not about legal records or events. It lives here
once.

**What this module deliberately does not own.** Everything downstream of "the
importer returned" belongs to the feed:

- the workbook parser, its schema contract, its normalisation and validation;
- the publication model and the transaction that writes it;
- the feed-state row, and what a failure records on it;
- the audit action names;
- the `SyncOutcome` type and its fields — the legal feed reports a reporting
  date, the event feed reports an export timestamp, and a shared outcome would
  have to carry both;
- the **failure-domain policy**: which exception is an operator's configuration
  mistake rather than a synchronisation failure.

So this returns a `WorkbookAttempt` — a description of what happened — and the
feed turns it into its own outcome. It takes two seams, `fetch` and
`import_artifact`, and calls back into nothing else. A shared function that also
recorded state and emitted audit events would have to be told about both feeds'
models, and that is the point at which removing duplication starts coupling two
failure domains that are deliberately independent.

`PublicUrlNotConfigured` is **re-raised, never classified**. An unset
environment variable is an operator's mistake, not a remote that misbehaved, and
each command reports it as plain text naming the missing variable.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from apps.core.feed_sync import find_published_artifact
from apps.sources.public_download import (
    XLSX_MIME_TYPE,
    PublicDownload,
    PublicDownloadError,
    PublicUrlNotConfigured,
)
from apps.sources.services import register_external_reference

#: What one attempt produced. Not a result, and deliberately not a `SyncOutcome`.
ATTEMPT_FAILED = "failed"
ATTEMPT_UNCHANGED = "unchanged"
ATTEMPT_IMPORTED = "imported"

#: Where a failure happened. Recorded so an operator can tell "the source was
#: unreachable" from "the workbook arrived and would not import".
STAGE_DOWNLOAD = "download"
STAGE_IMPORT = "import"


@dataclass(frozen=True)
class WorkbookFeed:
    """The fixed facts that distinguish one workbook feed from the other.

    Instances are constants in the owning domain module. Nothing builds one from
    request data, so which importer runs and what an artifact is labelled with
    stay decisions made in code review.
    """

    #: Recorded on the import run, and used to recognise this feed's own
    #: published content when the same checksum comes back.
    importer_name: str
    #: The canonical filename the artifact records. Never a path.
    filename: str
    #: A fixed, non-secret provenance label. **Never the sharing URL**, which is
    #: a bearer-style secret: possession of it is possession of the workbook.
    external_reference: str
    #: Distinguishes this feed's temporary directory in a process listing.
    temp_prefix: str


@dataclass(frozen=True)
class WorkbookAttempt:
    """What one download-and-import attempt produced, before any bookkeeping.

    `message` is already sanitized by whatever raised it: nothing the downloader
    or the importer raises carries the sharing URL, a redirect target or file
    content.
    """

    status: str
    download: PublicDownload | None = None
    #: The importer's own result object, untouched. Its shape is the feed's.
    result: object | None = None
    message: str = ""
    stage: str = ""

    @property
    def failed(self) -> bool:
        return self.status == ATTEMPT_FAILED


def _describe(error: Exception) -> str:
    """One sanitized line. The exception's own text, never anything added."""
    return f"{type(error).__name__}: {error}".replace("\n", " ")


def attempt_workbook_sync(
    *,
    feed: WorkbookFeed,
    source,
    fetch,
    import_artifact,
    dry_run: bool,
    allow_collapse: bool,
    actor,
    correlation_id,
) -> WorkbookAttempt:
    """Download the workbook and import it, classifying whatever goes wrong.

    `fetch` is called as ``fetch(destination)`` and must return a
    :class:`PublicDownload`. `import_artifact` is the feed's own importer.
    Both are seams so a test can drive this without a network or a workbook.

    Writes nothing to the feed state and records no audit event. The caller does
    both, because what a failure means differs per feed.
    """
    # One temporary directory for the whole attempt. `TemporaryDirectory`
    # removes it on every exit path — success, unchanged, validation failure,
    # download failure and unexpected exception alike — so the workbook never
    # outlives the command. The import happens **inside** it, because the
    # importer reads the file that only exists here.
    with tempfile.TemporaryDirectory(prefix=feed.temp_prefix) as directory:
        destination = Path(directory) / feed.filename

        try:
            download = fetch(destination)
        except PublicUrlNotConfigured:
            # An operator's configuration mistake, not a synchronisation
            # failure. The command reports it as plain text naming the missing
            # variable rather than recording it as if the remote had misbehaved.
            raise
        except PublicDownloadError as error:
            return WorkbookAttempt(status=ATTEMPT_FAILED, message=str(error), stage=STAGE_DOWNLOAD)
        except Exception as error:  # noqa: BLE001 - unattended job; nothing may escape
            # Anything else unexpected is recorded rather than allowed to escape
            # as a traceback from a cron job.
            return WorkbookAttempt(
                status=ATTEMPT_FAILED, message=_describe(error), stage=STAGE_DOWNLOAD
            )

        artifact, already_published = find_published_artifact(
            source, download.sha256, feed.importer_name
        )
        if already_published:
            return WorkbookAttempt(status=ATTEMPT_UNCHANGED, download=download)

        try:
            # A previous dry run or a previous failed run already registered
            # this content. Reuse that artifact: the checksum is unique per
            # source, so registering a second one is both impossible and wrong.
            artifact = artifact or register_external_reference(
                source=source,
                external_reference=feed.external_reference,
                original_name=feed.filename,
                mime_type=XLSX_MIME_TYPE,
                sha256=download.sha256,
                size_bytes=download.size_bytes,
                uploaded_by=actor,
                actor=actor,
                correlation_id=correlation_id,
            )
            result = import_artifact(
                artifact,
                workbook_path=download.path,
                dry_run=dry_run,
                allow_collapse=allow_collapse,
                actor=actor,
                correlation_id=correlation_id,
            )
        except Exception as error:  # noqa: BLE001
            # Deliberately broad: this runs unattended every morning, and every
            # failure — including an import-registry constraint violation — must
            # be recorded and reported. The importer has already rolled back, so
            # the previously published snapshot is intact.
            return WorkbookAttempt(
                status=ATTEMPT_FAILED, message=_describe(error), stage=STAGE_IMPORT
            )

    return WorkbookAttempt(status=ATTEMPT_IMPORTED, download=download, result=result)


__all__ = [
    "ATTEMPT_FAILED",
    "ATTEMPT_IMPORTED",
    "ATTEMPT_UNCHANGED",
    "STAGE_DOWNLOAD",
    "STAGE_IMPORT",
    "WorkbookAttempt",
    "WorkbookFeed",
    "attempt_workbook_sync",
]
