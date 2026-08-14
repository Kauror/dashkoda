# Source, import and audit data model

The shared foundation every data module builds on: where information came from,
which original content backs it, which import attempt produced it, and who did
what. The `sources` and `audit` apps hold **no business data and no dashboard
route** of their own; `legal_work` is the first module to consume them end to
end.

## Module boundaries

| App | Owns | Does not own |
| --- | --- | --- |
| `sources` | source registration, private artifacts, the import-run registry, the source/import admin workflow, private artifact access | any domain data, any parsing, any scheduling |
| `audit` | append-only records of significant actions | anything domain-specific; it has no dependency on `sources` |
| `legal_work` | imported legal-work snapshots and rows, their selectors and page, the workbook importer, the OneDrive feed state | generic artifact registration, the generic import lifecycle, audit infrastructure, authentication, any other OneDrive file |
| `visibility` | manually observed audience sizes, their metric registry, selectors, staff entry workflow, the Koduleht page and the optional Google Analytics website-traffic collector | any social-platform collector or credential, any individual subscriber, follower or visitor |
| `shop` | the imported Koda.ee Commerce dataset — product identities, dated catalogue observations, public path mappings, aggregated daily facts, the source-coverage state, the manual package importer and the E-pood pages | GA4 collection, path canonicalisation, membership purchases, any customer, participant or submission, any collector |

`legal_work` is the first module to consume this foundation end to end. Its
models, importer, collector and scheduling are documented separately in
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
refuses anything containing `@` or `?`. A sharing URL is therefore structurally
incapable of becoming an external reference.

### Two shapes of external reference

The XOR rule is unchanged, but an external-reference artifact may or may not
carry a content identity, and that is what decides whether it can be imported:

| Shape | Has `sha256` | Meaning | Importable |
| --- | --- | --- | --- |
| registration only | no | a pointer to material this application does not hold and cannot verify | no |
| metadata-only content identity | yes | a collector downloaded the bytes, computed the digest and size here, and is not keeping the file | yes |

`register_external_reference` accepts an optional `sha256`, `size_bytes` and
`mime_type`. When a checksum is supplied it must be exactly 64 lowercase
hexadecimal characters, the size must be positive and within
`SOURCE_ARTIFACT_MAX_BYTES`, and `(source, sha256)` must still be unique — the
same rules an upload passes. Without a checksum the artifact stays a
registration-only reference exactly as before.

Several producers of the second shape exist, each with its own fixed non-secret
label: `onedrive-public:oigusloome` for the legal-work workbook,
`koda-public:company-list`, `koda-public:news-feed` and `koda-public:events` for
the three public Koda.ee feeds, `manual:membership-report` for a typed board
report, and `manual:smaily-audience`, `manual:facebook-followers`,
`manual:linkedin-followers`, `manual:instagram-followers` and
`manual:youtube-subscribers` for the typed audience figures. Because no file is
stored, the admin offers no download for these artifacts and the download route
returns `404`.

A social profile URL is never one of these labels. The model independently
refuses any reference containing `@` or `?`, and the visibility service uses the
fixed prefix plus the submission's correlation ID.

For the public feeds the checksum covers **normalised canonical JSON**, not the
response body: a CMS re-render changes markup without changing meaning, and
hashing the raw bytes would republish identical data every morning. See
[koda-public-feeds.md](koda-public-feeds.md).

### Immutability

`source`, `file`, `sha256`, `size_bytes` and `external_reference` are fixed once
registered. `SourceArtifact.save()` compares them against the stored row and
raises `ImmutableFieldError` on any change. Correctable metadata such as
`mime_type` stays editable. The admin exposes no change or delete action at all,
so the normal workflow cannot reach these paths.

Every other published record enforces the same rule through one guard,
`apps.core.immutability.ImmutableWriteGuard`. A model names the fields that may
still move (`MUTABLE_FIELDS`), what to raise and what to say; with no mutable
fields it is frozen entirely. Each domain keeps its own exception type, so
`except NewsImmutable` still means news.

**One variation is deliberate and worth knowing about.** A `save()` that names no
`update_fields` rewrites every column. Almost every guarded model refuses it —
but `NewsResource` and `ShopProduct` permit it, which is how both were written
before the guard was consolidated, and `ALLOW_UNRESTRICTED_SAVE` preserves that
rather than tightening it silently. Both are re-observed catalogue rows whose
collectors always pass `update_fields`, so nothing relies on the permission
today; whether to withdraw it is a decision about those two catalogues, not a
side effect of moving code.

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

**The `sources` app never parses these files.** It stores and checksums them; a
domain module's own importer is what reads one.

A collected workbook need not be stored at all. The legal-work public-link route
writes it to a temporary directory, hands that path to the importer and deletes
the directory, so nothing is written under `SOURCE_ARTIFACT_ROOT` for that route
and the artifact carries only the content identity.

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

What makes an artifact importable is a trusted SHA-256, not whether a file is
still stored: `build_import_run` refuses an artifact with **no checksum** rather
than inventing a key. A file-backed artifact always has one. A metadata-only
external artifact has one when the collector computed it from the bytes it
actually received, which is exactly the case where the digest is as trustworthy
as an upload's.

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
terminal transitions, the legal-work events — snapshot imported, snapshot
published, synchronisation unchanged and synchronisation failed — an
imported / unchanged / failed triple for each of the three public Koda.ee feeds,
the internal membership manual-entry events, and the three visibility events:
`visibility.manual_batch_published`, `visibility.observation_published` and
`visibility.observation_superseded`. All of them go through
`apps.audit.services.record_event`; there are no signal handlers, so every writer
is findable by searching for that one function.

Visibility summaries carry the metric key, the value, the observation date, the
batch id, the source slug, the collection method, the content checksum and
whether something was superseded. They never carry the note the user typed, a
form payload, session data, a platform token or a profile URL.

Public-feed summaries carry the source slug, checksum, aggregate counts, the
observed timestamp and the record id. They never carry member names,
registration codes, member URLs, raw JSON, RSS bodies, article HTML, event-page
HTML or a traceback.

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

## The canonical CSV boundary

The internal membership history is the first dataset to arrive this way. It
consumes a **package** — a ZIP carrying a manifest, the canonical CSVs and a
SHA-256 for each — rather than loose files, because the manifest and its
checksums are what make the data approved. It supports a dry run that writes
nothing, and the same package digest plus importer schema version make a repeat
run idempotent.

DashKoda does not parse legacy DOC, DOCX, PPTX or PDF files. Turning those into a
canonical CSV package is separate data-preparation work that happens outside this
application, and the original documents never enter it.

Two membership datasets exist and they are deliberately not one:

| | `koda-public-members` | `membership-internal-board-reports` |
| --- | --- | --- |
| Counts | member profiles published on Koda.ee | membership as the Chamber's board reports define it |
| Model | `MembershipCountObservation` | `InternalMembershipObservation` and six related models |
| Selectors | `apps/membership/selectors.py` | `apps/membership/internal_selectors.py` |
| Written by | the scheduled public collector | the one-time package import and the staff form |

No selector, template or query joins them. See
[internal-membership-history.md](internal-membership-history.md) for the full
model, the quality policy and the manual workflow.

## Manually observed audience sizes

`apps/visibility` is the second dataset with no remote source. Three models:
`VisibilityEntryBatch` (one submission, one idempotency boundary, one
correlation ID) and `VisibilityObservation` (one metric on one date, immutable
apart from `is_current_for_date`).

Beside them, and collected rather than typed, is the Google Analytics history:
`Ga4DailySnapshot` (one immutable revision of one reporting day, with the
current one unique per **date** so a revised day supersedes rather than
overwrites), `Ga4PageDaily` and `Ga4ChannelDaily`. It replaced
`WebsiteTrafficObservation`, whose single current row per source could hold the
latest reading or a history but never both. See
[website-analytics.md](website-analytics.md).

The metric vocabulary is a closed `TextChoices` set of seven, deliberately not a
JSON blob: a free-text metric name would make "was this ever reported"
unanswerable in SQL. A correction supersedes rather than edits, a later date is
history rather than a correction, and a missing value is never zero. See
[visibility-manual-entry.md](visibility-manual-entry.md).

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
