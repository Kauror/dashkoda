# Chamber opinion documents — the private catalogue

The Chamber's outgoing opinion letters, as PDFs, catalogued so that a legal-work
record which has already been answered can later point at the document that
answered it.

This is unlike every other DashKoda feed. There is no website and no URL: the
documents are read from a directory on the host, they are **private
correspondence**, and they are never served from a public path. Phase 1 — what
this document describes — builds the catalogue and nothing else. **No viewer
page changes, and no legal topic gains a link.** Linking is Phase 2.

## The two roots

| | Path in the container | Host path | Mode |
|---|---|---|---|
| Source inbox | `/data/opinions/source` | `/mnt/user/appdata/dashkoda/opinions/source` | **read-only** |
| Managed store | `/data/opinions/store` | `/mnt/user/appdata/dashkoda/opinions/store` | read-write |

Both are settings (`LEGAL_OPINION_SOURCE_ROOT`, `LEGAL_OPINION_STORE_ROOT`).
**No command takes a path or a URL.** There is therefore no operator input, no
viewer input and no scheduled-job argument that can steer a read or a write
somewhere else, and no filename can reach shell history or a process listing.

The source inbox is evidence the Chamber owns; DashKoda only reads it, and never
unpacks the bootstrap archive into it. The managed store is DashKoda's own copy
and the thing it is answerable for.

## What may be in the source inbox

- the bootstrap archive, `Opinions.zip` (name configurable);
- loose `.pdf` files;
- year folders containing `.pdf` files.

Anything else is ignored rather than refused — a stray readme must not make a
handover unreadable. A file is only read once it has stopped changing
(`LEGAL_OPINION_MIN_STABLE_AGE_SECONDS`), so a PDF still being copied is never
hashed half-written.

The recurring-folder workflow is what production runs. Since the 2025+2026
activation the active source is **loose PDFs in year folders** under
`source/onedrive/2025/` and `source/onedrive/2026/`, read by `DirectoryProvider`.

No ZIP remains in the source root. Both archives are preserved deliberately
*outside* it, where nothing ingests them automatically:

- `opinions/bootstrap-archive/Opinions-2026-pilot.zip` — the 34-document pilot
  the first catalogue was built from;
- `opinions/bootstrap-archive/Opinions-full-2020-2026.zip` — the complete
  767-document historical handover.

The pilot ZIP was moved out *before* the expanded catalogue was published, so
the first 2025+2026 catalogue already carries its final directory-backed source
identity rather than changing identity a second time later.

**Active years are 2025 and 2026 only.** 2020–2024 stay inside the full archive
until a separate decision activates them. That is an operator choice about what
sits in the source directory; there is no year filter in application code and
the collector remains year-agnostic.

## The archive is treated as untrusted

Every entry is checked before a byte is read. The whole archive is refused if it
contains a path traversal, an absolute or drive-qualified path, a symlink, a
nested archive, a duplicate entry path, an entry over the size cap, or an entry
whose compression ratio suggests a decompression bomb.

## Managed storage

Blobs are content-addressed by their own SHA-256:

```
/data/opinions/store/
    blobs/35/3557….pdf
    quarantine/
    temporary/
```

Three properties fall out of that rather than needing enforcement: identical
bytes are stored once however many filenames they arrive under; a blob cannot be
silently replaced by different bytes; and verifying the store is re-reading and
re-hashing it.

Writes are temporary file → `fsync` → verify size and digest → atomic rename, so
a crash leaves either nothing or a complete correct blob. **A source file
disappearing never removes a managed blob or any historical row**, and there is
no garbage collection.

Reads resolve the stored key and prove the *resolved* path stays under the store
root, which survives `..`, absolute keys and a symlink planted inside the store.

## Validation

Each document is checked for size, a PDF signature, structural validity, page
count, encryption, and active content. Results are `valid` or one of
`quarantined_encrypted`, `quarantined_active_content`, `quarantined_invalid`,
`quarantined_too_large`, `quarantined_too_many_pages`.

Nothing a document asks for is ever executed, opened or followed. Quarantined
bytes are kept outside `blobs/` for diagnosis and are never viewer-accessible.

**Active content is decided from the parsed object model, not from raw bytes.**
Measured against the 759-document bootstrap catalogue, a byte scan for `/JS`
matches four documents and `/AA` two more — every one a coincidental sequence
inside a Flate-compressed object stream, with nothing of the sort in the object
model. Quarantining on that would have destroyed six valid opinion letters. A
raw scan for long distinctive names survives only as a backstop for a document
whose structure could not be walked at all.

Ordinary hyperlinks are recorded as a warning and are never a reason to reject:
opinion letters cite web pages, and DashKoda never opens them.

## Extraction

`pypdf` reads the text. It was chosen because the application image had **no PDF
tooling at all** — no Poppler, no library — so a dependency was unavoidable, and
pypdf is pure Python (no Dockerfile change), permissively licensed, and covers
structure validation, page count, encryption detection, text extraction and
object-model inspection in one place. PyMuPDF is a large AGPL C extension;
pdfminer.six cannot inspect actions; Poppler would mean image changes and
parsing subprocess output.

Extraction is **versioned** (`EXTRACTOR_VERSION`). A reading is an immutable row
keyed by blob and version, so improving the text layer produces new rows and
never edits an old one, and a Phase 2 match can always name the exact reading it
was based on.

Normalisation touches layout artefacts only — encoding form, line-break
hyphenation, trailing spaces, runs of blank lines. No word is substituted: this
is legal correspondence and later becomes matching evidence.

**No OCR.** A document with no extractable text, implausibly little for its page
count, or too many replacement characters is recorded as `needs_ocr` and excluded
from matching. It is never rendered to images. Measured on the bootstrap
catalogue, **zero documents need OCR.**

## Filename and header metadata, kept apart

The filename usually reads `YYYY-MM-DD - recipient - subject.pdf`. The subject
may itself contain the separator — 43 of 759 real documents do — so the split is
bounded to the first two fields.

Two filenames in the real catalogue are *partially* double-encoded: a correct
`õ` in one word and the UTF-8 bytes of `õ` read as latin-1 in another. The
repair decodes only maximal runs that are themselves valid UTF-8, which fixes
exactly those two and changes none of the other 757. The original filename is
always preserved.

The letter's own header is parsed separately, anchored on the Estonian reference
block (`Teie <date> nr <ref>`, `Meie <date> nr <ref>`) rather than on line
positions, because the letterhead changes shape across the catalogue's six
years. Coverage on the real corpus: 91% for the outgoing date, 99% for recipient
and subject.

Filename-derived and document-derived fields live in **separate columns and
neither overwrites the other**. Where they disagree a warning code records it.
This is not a defect to fix: 269 of the real letters are dated exactly one day
after their filename, because the name records drafting and `Meie <date>` records
sending. Both are true, so Phase 2 must treat a small gap as normal.

## Classification

Deterministic, from literal Estonian vocabulary matched on stems: `opinion`,
`joint_opinion`, `supplementary_opinion`, `follow_up`, `annex`,
`supporting_document`, `unknown`. No model, no embedding, no learned threshold —
a classification can be read off the document by a person.

`annex`, `supporting_document` and `unknown` may **never** be a legal topic's
primary resource. Because a false positive there means a link that can never
appear, those two labels are decided from the **filename only**: measured on the
real catalogue, reading them from the page body demoted genuine opinions, since
an opinion routinely discusses the draft's explanatory memorandum by name.

## Resumable build

1. ask every provider what it holds, and hash it — the manifest;
2. compare the manifest checksum with what is published;
3. process up to `--max-documents` entries with no terminal state;
4. publish a snapshot **only** once every entry has one.

A partial build never becomes current: the previous catalogue stays the answer
until a complete one exists. Work is never repeated — a blob is keyed by digest
and an extraction by digest plus extractor version. A document that cannot be
read is catalogued with a quarantine status and does not stop the rest.

## Commands

```bash
python manage.py sync_legal_opinion_documents --dry-run --json
```

```bash
python manage.py sync_legal_opinion_documents --max-documents 10 --json
```

```bash
python manage.py verify_legal_opinion_store --json
```

Flags: `--dry-run`, `--full`, `--max-documents N`, `--json`. **No path option and
no URL option.** Exit codes follow the other feeds: `0` imported, unchanged,
partial or dry run; `1` failed; `3` another build was already running.

A dry run scans and validates but publishes nothing and writes no managed blob.

JSON output is aggregates only: counts, a snapshot id, a 12-character checksum
prefix and the extractor version. Never a filename, recipient, subject, document
text, storage path or full digest. Audit summaries follow the same rule.

## Admin

Every model is registered read-only. There is no add, edit, delete, upload,
approve, override, reclassify or retry action. Staff can inspect the source
entry, filenames, parsed and detected metadata, classification, validation and
extraction status, page count, excerpts, warnings and a digest prefix.

The full SHA-256 and every filesystem path are withheld even from staff.

A wrong classification is corrected by changing the vocabulary and rebuilding; a
bad extraction by a new extractor version. Inspection informs the rules and never
overrides a row.

## Backup

The managed store is **not** covered by the existing PostgreSQL backup, and its
contents cannot be reconstructed from the database — the database holds text and
metadata, not bytes.

Back up `/mnt/user/appdata/dashkoda/opinions/store` alongside the database dump.
`blobs/` is the part that matters; `temporary/` is disposable and `quarantine/`
is diagnostic. The source inbox should be retained too, as the evidence the
catalogue was derived from: **do not delete either archive in
`opinions/bootstrap-archive/`** until backup policy explicitly allows it.

`verify_legal_opinion_store --json` re-reads and re-hashes every blob. It reports
and never repairs — deleting a blob that looks wrong is how a recoverable fault
becomes data loss.

## Not in this phase

No viewer link, no resource page, no PDF endpoint, no matching, no public
`Meie arvamus` collection, no OCR, no search, and no recurring-folder migration.
