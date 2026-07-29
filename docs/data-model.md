# Source, import and audit data model

PR-05 adds the shared foundation every later data module builds on: where
information came from, which original file backs it, which import attempt
produced it, and who did what. It adds **no business data, no importer and no
dashboard route**.

## Module boundaries

| App | Owns | Does not own |
| --- | --- | --- |
| `sources` | source registration, private artifacts, the import-run registry, the source/import admin workflow, private artifact access | any domain data, any parsing, any scheduling |
| `audit` | append-only records of significant actions | anything domain-specific; it has no dependency on `sources` |
| `legal_work` | imported legal-work snapshots and rows, their selectors and page, the workbook importer, the OneDrive feed state | generic artifact registration, the generic import lifecycle, audit infrastructure, authentication, any other OneDrive file |

`legal_work` is the first module to consume this foundation end to end. Its
models, importer, Graph collector and scheduling are documented separately in
[legal-work-feed.md](legal-work-feed.md); only its place in the shared flow is
described here.

`audit` deliberately has no foreign key into `sources`. `object_type`,
`object_id` and `correlation_id` already tie an event to whatever it describes,
and keeping the dependency out means the audit trail never has to change when a
domain module does.

## The accepted data flow

```text
data source
  → immutable SourceArtifact or controlled reference
  → ImportRun dry-run / preview
  → validation
  → immutable domain snapshot
  → selectors / services
  → dashboard
```

No automated adapter, importer or agent may ever write directly into the
authoritative, viewer-visible layer.

The legal-work feed is the first module to run the whole flow. Its authority
comes from the workbook being a **prepared, deterministic export** that a person
generates and reviews, not from an automated interpretation of a working file:
DashKoda parses no legacy operational document and infers nothing. Publication
is all-or-nothing, and a failed import never replaces the last good snapshot.

A later membership importer will add the draft/verification steps between
validation and publication, because that data is edited by people rather than
regenerated wholesale.

## DataSource

| Field | Type | Notes |
| --- | --- | --- |
| `slug` | slug, unique | stable identifier |
| `name` | char(200) | |
| `source_type` | choice | document, spreadsheet, registry, website, manual, other |
| `authority_tier` | choice | primary, secondary, supplementary, unclassified |
| `authority_rank` | positive small int | **lower means higher authority** |
| `responsible_person` | char(200), blank | |
| `expected_update_frequency` | choice | daily … irregular, unknown |
| `stale_after_days` | positive int, nullable | null means staleness is not tracked |
| `description` | text, blank | |
| `is_active` | bool | |
| `created_at` / `updated_at` | timestamps | |

Constraints: `authority_rank >= 1`, and `stale_after_days` is null or `>= 0`.

The choice sets are deliberately generic. **The Chamber's real authority order is
still an open decision gate** and will be confirmed before any real import;
`authority_rank` carries the ordering, and `authority_tier` is only a coarse
human label. Nothing membership-specific belongs on this model.

A source is never physically deleted once anything references it: both
`SourceArtifact.source` and `ImportRun.source` use `PROTECT`, and the admin
hides the delete action for a referenced source. Normal administration sets
`is_active = False`; inactive sources stay fully queryable.

## SourceArtifact

| Field | Type | Notes |
| --- | --- | --- |
| `source` | FK, PROTECT | |
| `original_name` | char(255) | metadata only, never used as a path |
| `mime_type` | char(128) | as reported, not trusted |
| `size_bytes` | positive big int | counted server-side while streaming |
| `sha256` | char(64), indexed | computed server-side, never client-supplied |
| `file` | private FileField | see storage below |
| `external_reference` | char(500) | |
| `access_level` | choice | `staff_only`, `restricted` |
| `uploaded_at` | timestamp | |
| `uploaded_by` | FK, SET_NULL | survives user removal |

Constraints:

- `sourceartifact_file_xor_external_reference` — exactly one of the two is
  present; neither both nor neither;
- `sourceartifact_unique_source_checksum` — unique on `(source, sha256)` where a
  checksum exists. The same content may be registered under a *different*
  source, because provenance differs even when bytes do not.

External references must not embed credentials or signed parameters; the model
refuses anything containing `@` or `?`.

### Immutability

`source`, `file`, `sha256`, `size_bytes` and `external_reference` are fixed once
registered. `SourceArtifact.save()` compares them against the stored row and
raises `ImmutableFieldError` on any change. Correctable metadata such as
`mime_type` stays editable. The admin exposes no change or delete action at all,
so the normal workflow cannot reach these paths.

### Private storage

Files never enter PostgreSQL. They are written through
`PrivateArtifactStorage`, rooted at `SOURCE_ARTIFACT_ROOT`:

- **production** requires the setting from the environment with no fallback, so
  a misconfigured deployment fails loudly instead of writing originals to an
  ephemeral container path;
- **local** defaults to `.private-media/source-artifacts/`, which is git-ignored;
- **tests** get a per-test temporary directory from an autouse fixture and leave
  nothing behind;
- **Compose** mounts the named `source_artifacts` volume at
  `/srv/dashkoda/source-artifacts`.

The root is outside `STATIC_ROOT` and outside every `STATICFILES_DIRS` entry, so
WhiteNoise cannot reach it. There is no media URL and no media route.
`PrivateArtifactStorage.url()` raises rather than returning a path, so a template
cannot accidentally leak one.

The stored path is `sources/<source id>/<random uuid><extension>`. The client
filename never becomes a path component; only its extension survives, lowercased
and checked against the allowlist.

### Upload rules

- maximum size `SOURCE_ARTIFACT_MAX_BYTES`, default **25 MiB**;
- empty uploads refused;
- allowlist: `.csv`, `.tsv`, `.txt`, `.json`, `.xml`, `.pdf`, `.doc`, `.docx`,
  `.xls`, `.xlsx`, `.ppt`, `.pptx`. Everything else — executables, scripts,
  archives, macro-enabled office formats — is refused;
- SHA-256 and byte count are computed by streaming the upload in 64 KiB chunks;
- a duplicate for the same source is refused before anything is written.

**DashKoda never parses these files.** They are stored and checksummed only.

### Access

There is no viewer-facing download. The only route is
`/admin/sources/sourceartifact/<pk>/download/`, which requires, in order:

1. the viewer PIN gate (it is under `/admin/`);
2. Django admin authentication as active staff;
3. the explicit `sources.download_sourceartifact` permission;
4. superuser status as well, when `access_level` is `restricted`.

Responses are `Content-Type: application/octet-stream`, `Content-Disposition:
attachment`, `Cache-Control: private, no-store` and `X-Content-Type-Options:
nosniff`. Every successful download is audited; a refused one is not.

## ImportRun

| Field | Type | Notes |
| --- | --- | --- |
| `source`, `artifact` | FK, PROTECT | must agree |
| `importer_name`, `schema_version` | char | explicit, fixed once executed |
| `import_key` | char(64), indexed | see below |
| `dry_run` | bool, default true | |
| `status` | choice | pending, running, succeeded, failed |
| `started_at`, `finished_at` | timestamps, nullable | |
| `rows_added`, `rows_skipped`, `rows_invalid` | positive ints | |
| `warnings`, `errors` | JSON lists | structured for a later QA report |
| `initiated_by` | FK, SET_NULL | |
| `correlation_id` | UUID, indexed | ties the run's audit events together |

### Import key and idempotency

```text
import_key = SHA-256( importer_name ␟ schema_version ␟ artifact_sha256 )
```

The parts are stripped, the digest is lowercased, and they are joined with `\x1f`
— a separator that cannot occur inside any part, so two different triples cannot
collide by concatenation.

Uniqueness is conditional:
`importrun_unique_successful_live_import` applies only where
`status = succeeded AND dry_run = false`. So a dry run may be repeated freely and
never blocks a later real import, a failed run may be retried, and only one
successful real import can exist per key.

An external-reference artifact has no checksum, so it cannot be imported yet;
`build_import_run` refuses it rather than inventing a key.

### State transitions

```text
pending ──▶ running ──▶ succeeded
   │            │
   └────────────┴──▶ failed
```

Terminal states have no successors. Transition rules live in
`apps/sources/services.py` (`start_import_run`, `complete_import_run`,
`fail_import_run`), not in views or admin callbacks. Database constraints back
them up: a terminal state requires `finished_at`, a non-terminal state must not
have one, and `finished_at` may not precede `started_at`.

Diagnostics carry structure (`{"row": 4, "code": "unknown_column"}`), never
secrets and never whole file contents. Only a count of errors reaches the audit
trail.

## AuditEvent

| Field | Type | Notes |
| --- | --- | --- |
| `timestamp` | timestamp, indexed | newest first |
| `actor` | FK, SET_NULL | null means a system action or a removed user |
| `action` | char(64), indexed | |
| `object_type`, `object_id` | char, indexed | text, so they outlive the object |
| `change_summary` | JSON | redacted |
| `correlation_id` | UUID, nullable, indexed | |

Recorded so far: data-source creation, material update and deactivation,
artifact registration, staff artifact download, import-run creation, import-run
terminal transitions, and the legal-work events — snapshot imported, snapshot
published, synchronisation unchanged and synchronisation failed. All of them go
through `apps.audit.services.record_event`; there are no signal handlers, so
every writer is findable by searching for that one function.

A single correlation ID threads one synchronisation attempt through its
artifact registration, its import run, its snapshot and all of its audit events.

### Append-only enforcement

Four layers:

1. `AuditEvent.save()` raises on any update;
2. `AuditEvent.delete()` raises;
3. the manager blocks `QuerySet.update()`, `QuerySet.delete()` and
   `bulk_update()`;
4. a PostgreSQL trigger, `audit_auditevent_append_only`, raises on `UPDATE` and
   `DELETE` at the table itself.

**Known limits, stated precisely rather than claimed away.** The trigger is a row
trigger, so `TRUNCATE` does not fire it — that is what lets the test suite reset
between runs. A database role with DDL rights can drop the trigger, and a
superuser with filesystem access can do anything at all. This is strong
protection against application bugs, accidental bulk operations and casual
misuse; it is not, and is not claimed to be, tamper-proof storage. Off-server
backups remain the real defence, and those are still outstanding.

### Redaction

Redaction runs inside `AuditEvent.save()`, not only in the service, so no caller
can store an unredacted summary even by using the ORM directly. Keys matching
pin, password, secret, token, api key, credential, authorization, cookie,
session key, csrf, connection string, dsn, salt, signature or hash are replaced
with `[redacted]`; `sha256`, `import_key` and `content_hash` are exempt because a
checksum is a wanted, non-secret fact. Nested dicts and lists are walked, and
strings longer than 500 characters are truncated so a file body cannot be
smuggled in.

## Future canonical CSV boundary

The membership importer arrives in PR-07, not here. When it does:

- it consumes a **canonical CSV** whose columns are fixed by the implementation
  plan;
- it always runs dry-run/preview before writing;
- the same checksum and schema version are idempotent;
- a successful import creates **draft** records only;
- `verified` status always requires a deliberate administrator action.

DashKoda does not parse legacy DOC, DOCX, PPTX, XLSX or PDF files. Turning those
into a canonical CSV is separate data-preparation work that happens outside this
application.

## External semantic-agent boundary

Later, external semantic agents may help with material that deterministic cell
or row mapping cannot handle. **None of that exists in PR-05, and none of it is
speculated about in the schema.**

When it arrives it must follow the same flow: source → artifact →
extraction/import record → validation → draft → human verification → verified
data. An agent produces evidence and provenance; it never receives authority to
publish a verified value.

Deliberately absent from PR-05, and not to be added without their own pull
request: `ExtractionRun`, `CandidateRecord`, `ReviewDecision`, prompt or model
fields, direct model API calls, AI SDK dependencies, background agent execution,
SharePoint, Microsoft Graph, email collection, website crawling, polling, Redis
and Celery.
