# Legal-work feed (Õigusloome)

The first DashKoda module backed by real business data. It imports one prepared
Excel workbook from OneDrive, publishes it as an immutable snapshot, and renders
it at `/oigusloome/`.

**DashKoda never reads the lawyers' operational workbook and never writes to
OneDrive.** A separate preparation utility turns that operational file into the
canonical workbook described here; DashKoda only consumes the result, read-only.

## What the dashboard shows, and what it deliberately does not

Shown: topics currently being worked on, the most recently sent opinions, the
newest received topics, and the data's freshness.

Deliberately absent, in the model as well as the interface: the responsible
lawyer, lawyer workload, member-feedback statistics, links to Chamber opinions,
matching to opinion documents, historical multi-year analysis, and any AI
interpretation. These are not hidden columns — the fields do not exist, so they
cannot leak.

## The workbook contract

Four sheets: `CONTROL`, `OVERVIEW`, `DATA`, `WARNINGS`.

`DATA` is authoritative and must contain the Excel Table **`tbl_oigusloome`**
with exactly these columns, in this order:

```text
record_id, source_year, source_nr, topic, act_type, received_date,
deadline_date, sent_date, sent_status, recipient, stage, stage_key,
next_step, is_open, warning_codes, source_row, refreshed_at
```

`OVERVIEW` is formatted for people and is **never** read as a data source.
Deriving records from cell positions would break the moment someone adjusted
the layout.

`CONTROL` must provide `dataset_key`, `schema_version`, `source_file_name`,
`source_sheet`, `source_modified_at`, `source_sha256`, `generated_at`,
`reporting_date`, `total_record_count`, `open_record_count`,
`sent_record_count`, `not_sent_record_count`, `warning_record_count`,
`refresh_status` and `generator_version`. Extra keys are ignored rather than
rejected, so the generator can add metadata without breaking the importer.

`dataset_key` must be `oigusloome`. Supported `schema_version` values are
**`1.0` and `1.1`**.

### Why two schema versions

The brief specified `1.0`; the generator emits `1.1`. The `DATA` table is
identical between them — 1.1 only added CONTROL metadata (`overview_year`,
`preview_limit`) and multi-year support. Rather than silently accepting any
version, the importer holds an explicit supported set and rejects everything
else. The version a file declared is recorded on its snapshot.

### Why row uniqueness is a composite

`source_row` is the row number inside its own year sheet, so it repeats across
years — in the real workbook a single `source_row` value appears up to three
times. Uniqueness is therefore `(snapshot, source_year, source_row)`, alongside
`(snapshot, record_id)`. A plain unique constraint on `source_row` would reject
the real workbook.

### Validation rules

The importer rejects the whole file, rather than repairing it, when:

- a sheet is missing, or the `tbl_oigusloome` table is absent;
- column names or their order differ;
- `dataset_key` is wrong or `schema_version` unsupported;
- a `record_id`, or a `(source_year, source_row)` pair, repeats;
- a cell inside `DATA` contains a formula;
- a date column holds text, or `is_open` is not a real boolean;
- `sent_status` is outside `pending / sent / not_sent / invalid`;
- a `sent` record has no `sent_date`, or a non-sent record carries one;
- `topic` is missing;
- `CONTROL` disagrees with the rows actually parsed.

`warning_record_count` counts *records carrying at least one code*, not rows on
the `WARNINGS` sheet: a record with two warnings appears twice there and counts
once here.

## Data model

| Model | Holds |
| --- | --- |
| `LegalWorkSnapshot` | one complete import: source, artifact, import run, declared schema version, reporting date, counts, `is_current` |
| `LegalWorkItem` | one immutable row belonging to a snapshot |
| `LegalWorkFeedState` | when the remote was last checked, last changed, last imported, and how the last attempt ended |

### Snapshot publication

Publication is all-or-nothing:

1. the whole snapshot and all its rows are written inside one transaction;
2. the previous current snapshot is retired and the new one becomes current;
3. the import run is completed and the audit events are recorded.

If anything fails, the transaction rolls back, the import run is closed as
failed, and **the previously current snapshot stays exactly as it was**. A
partial snapshot is never visible.

A database constraint enforces at most one current snapshot per source.
Snapshots are immutable apart from the `is_current` flag, which has to move;
rows are fully immutable. Neither is editable through the admin.

## Manual import

```powershell
docker compose exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx --dry-run
docker compose exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx
```

It registers the file through the ordinary source service, reuses an existing
artifact when the checksum matches, and then runs **the same importer** the
scheduled sync uses. A dry run validates everything and publishes nothing.

## Two collection routes

| Route | Command | Needs | Status |
| --- | --- | --- | --- |
| public sharing link | `sync_oigusloome_public` | `OIGUSLOOME_PUBLIC_URL` | the MVP route |
| Microsoft Graph | `sync_oigusloome` | five Graph variables and an Entra application | available, not required |

Both publish through the same importer, the same import registry and the same
immutable snapshot. They differ only in how the bytes arrive and in what is kept
afterwards.

Neither is installed as a schedule by this repository.

## MVP route: the public read-only sharing link

```text
public read-only OneDrive link
  → scheduled outbound HTTPS download
  → temporary XLSX
  → metadata-only artifact
  → existing importer
  → PostgreSQL snapshot
```

**No Microsoft Entra application, Graph credential, rclone, Power Automate,
webhook or upload endpoint is involved.** One outbound HTTPS request per run.

### Configuration

One variable:

```text
OIGUSLOOME_PUBLIC_URL
```

The complete view-only sharing URL for `dashkoda_oigusloome.xlsx`. It is
optional and blank by default: the web application starts and every page renders
without it, and only `sync_oigusloome_public` requires it. When it is missing
that command exits `1` with a message naming the variable — never its value.

**Treat the URL as a bearer-style secret.** The link is anonymously readable, so
whoever holds it can download the workbook. It belongs only in the server's own
environment file. It is never committed, never written to PostgreSQL, never
logged, never placed in an audit summary and never shown in the interface or the
admin. The command deliberately offers no `--url` option, so the URL cannot
reach shell history or a process listing either.

### What one run does

1. takes the same PostgreSQL advisory lock the Graph route takes;
2. ensures the `oigusloome-onedrive` data source exists and records
   `last_checked_at`;
3. adds or replaces `download=1` on the configured URL — without it Microsoft
   answers with an HTML viewer page rather than the file;
4. downloads to a secure temporary directory, following at most five redirects,
   every hop HTTPS, with explicit connect and read timeouts and no
   authentication header or persisted cookie;
5. proves the result is an XLSX package: non-zero size, the ZIP signature,
   `zipfile.is_zipfile`, and the `[Content_Types].xml` and `xl/workbook.xml`
   members. `Content-Type` is a signal only — Microsoft may legitimately label a
   real download `application/octet-stream`, and an HTML page labelled
   `application/octet-stream` is still refused;
6. computes SHA-256 and the byte count from the bytes actually received;
7. reuses the artifact with that checksum, or registers a new **metadata-only**
   one;
8. runs the existing importer against the temporary path;
9. publishes the snapshot and updates the feed state;
10. deletes the temporary directory — on success, on unchanged, on validation
    failure, on download failure and on any unexpected exception.

**No permanent copy of the workbook is kept.** Nothing is written to
`SOURCE_ARTIFACT_ROOT` by this route.

### The metadata-only artifact

The artifact records what the content *was*, not where to get it again:

| Field | Value |
| --- | --- |
| `source` | `oigusloome-onedrive` |
| `external_reference` | `onedrive-public:oigusloome` |
| `original_name` | `dashkoda_oigusloome.xlsx` |
| `mime_type` | the XLSX MIME type |
| `size_bytes`, `sha256` | computed server-side from the downloaded bytes |
| `file` | **empty** |

`onedrive-public:oigusloome` is a fixed, non-secret label. The sharing URL must
never be the external reference, and the model refuses any reference containing
`@` or `?`, so a URL with query parameters cannot be stored there even by
mistake.

An artifact is importable when it carries a trusted SHA-256, not when it still
has a file. An external reference registered *without* a checksum remains
non-importable, exactly as before.

### Idempotency: the checksum is authoritative

There is no etag and no remote modification time to compare, so every run
downloads and the digest decides:

| Situation | Result |
| --- | --- |
| same checksum, a successful live import exists | `unchanged` |
| same checksum, only dry-run imports | reuse the artifact, run the live import |
| same checksum, only failed imports | reuse the artifact, retry the import |
| new checksum | register a new metadata-only artifact and import |

The third and fourth rows are why `--force` is unnecessary and why a dry run can
never block the later live import of the same bytes.

### Commands

```powershell
docker compose exec -T web python manage.py sync_oigusloome_public --dry-run --json
docker compose exec -T web python manage.py sync_oigusloome_public --json
```

Exit codes: `0` imported, unchanged or a successful dry run; `1` failed; `3`
another synchronisation was already running.

`--json` emits exactly one line containing only `result`, `detail`,
`snapshot_id`, `reporting_date`, `rows_imported`, `dry_run` and warning-code
counts. No URL, host, path, header, cookie, workbook content or topic name.

A dry run validates the live workbook and publishes nothing; only
`last_checked_at` moves.

On failure the previous snapshot stays current, the feed state records `failed`
with a sanitized truncated message, and the dashboard keeps showing the last
good data with an explicit "last check failed" note.

### Feed state in this mode

`last_checked_at`, `last_successful_sync_at`, `last_changed_at`, `last_result`,
`last_error_summary`, `remote_size_bytes` and `current_snapshot` are all
maintained. `remote_etag` stays blank and `remote_modified_at` stays null:
this route has no trustworthy non-secret value for either, and the checksum
belongs on the artifact. Storing a digest in an etag field would make both
fields lie.

### Limitations of the public-link route

- **Anonymous-link access can be revoked or forwarded.** Whoever holds the URL
  can download the workbook, and an administrator who regenerates or revokes the
  link breaks the synchronisation until `OIGUSLOOME_PUBLIC_URL` is updated. A
  revoked link surfaces as a `403`/`404` failure, not as silent staleness.
- **Microsoft may change sharing-link behaviour.** The `download=1` parameter and
  the response shape are Microsoft's, not a documented API contract. If the
  response becomes HTML the download is refused rather than imported, and the
  previous snapshot stays published.
- There is no change notification. The workbook is fetched once each morning, so
  a mid-day regeneration is picked up the next day.
- Because no file is retained, the staff artifact download is unavailable for
  these artifacts by construction; the checksum is the record of what was
  imported.

## Microsoft Graph configuration

Optional. Not required for the MVP route above.

Read-only, app-only, one workbook. Environment variables:

```text
MS_GRAPH_TENANT_ID
MS_GRAPH_CLIENT_ID
MS_GRAPH_CLIENT_SECRET
OIGUSLOOME_DRIVE_ID
OIGUSLOOME_ITEM_ID
```

The web application starts fine without them, so local development and CI need
no credentials. Only `sync_oigusloome` and `resolve_oigusloome_share` require
them, and they fail with an explicit list of what is missing.

### Tenant setup checklist for the administrator

The code cannot create the Entra application or grant tenant permissions.

1. Register an application in Microsoft Entra ID (single tenant is sufficient).
2. Use **app-only** (client credentials) authentication. No delegated sign-in
   and no interactive login exists in the scheduled command.
3. Grant the **application** permission **`Files.Read.All`** on Microsoft Graph.
   That is the least-privileged permission Microsoft documents for downloading
   drive-item content, and it is all the sync needs. Do not grant write access.
4. Grant tenant admin consent for that permission.
5. Create a client secret (or a certificate) and store it only in the
   deployment's environment, never in Git.
6. Resolve the stable drive and item IDs (below) and set the two
   `OIGUSLOOME_*` variables.

If the organization can scope access more tightly than tenant-wide
`Files.Read.All` — for example with `Sites.Selected` on a specific SharePoint
site — prefer that, and treat `Files.Read.All` as the pilot fallback.

### Resolving the stable identifiers

A sharing URL is not a runtime identifier: it can be revoked or regenerated.
Resolve it once, store the IDs, and never put the URL in configuration.

Preferred, works with `Files.Read.All`:

```powershell
docker compose exec -T web python manage.py resolve_oigusloome_share --user <upn> --path "Documents/dashkoda_oigusloome.xlsx"
```

Fallback, when the path is unknown:

```powershell
docker compose exec -T web python manage.py resolve_oigusloome_share --url "<sharing URL>"
```

Microsoft's `/shares/` endpoint requires the broader `Files.ReadWrite.All`
application permission, so use the fallback only for this one-off lookup and do
not leave that permission granted. The command prints the file name, drive ID,
item ID and size — never a token, an authorization header, a client secret or a
signed download URL.

## Graph synchronization

```powershell
docker compose exec -T web python manage.py sync_oigusloome --json
docker compose exec -T web python manage.py sync_oigusloome --dry-run
docker compose exec -T web python manage.py sync_oigusloome --force
```

One run:

1. takes a PostgreSQL advisory lock, so overlapping runs cannot both import;
2. ensures the `oigusloome-onedrive` data source exists;
3. records `last_checked_at`;
4. reads the item's metadata from Graph;
5. skips the download when the remote etag says nothing changed;
6. otherwise downloads, size-capped while streaming, to a temporary directory;
7. computes SHA-256 locally and reuses the artifact when the content matches;
8. registers a new immutable artifact and runs the importer;
9. publishes the snapshot and updates the feed state.

Results: `imported`, `unchanged`, `failed`, `locked`.

Exit codes: `0` imported or unchanged, `1` failed, `3` another run in progress.

`--force` re-downloads and re-imports even when the remote looks unchanged. Note
that re-importing byte-identical content is refused by the import registry,
because only one successful live import may exist per import key; that is the
idempotency guarantee working, and the current snapshot survives untouched.

### Failure behaviour

On any failure the previous snapshot stays current, the feed state records
`failed` with a sanitized and truncated message, no temporary file remains, and
the import run is closed as failed. The dashboard keeps showing the last good
data together with an explicit "last check failed" note.

### Network behaviour

Every request has an explicit timeout. Throttling (`429`) and transient `5xx`
responses are retried a bounded number of times, honouring `Retry-After` as
Microsoft's throttling guidance requires, with exponential backoff otherwise.
Downloads follow Graph's `302` to a pre-authenticated URL, and the bearer token
is deliberately **not** forwarded to that host.

## Data freshness in the interface

Four distinct states, because "the page loaded today" is not a claim about the
data:

| State | Shown |
| --- | --- |
| never synchronised | `Andmeallikas ei ole veel ühendatud.` |
| imported | `Andmed seisuga <workbook reporting date>` and the last successful sync time |
| unchanged | last checked time, plus `Andmed ei ole pärast eelmist importi muutunud.` |
| failed with older data | last successful sync, and `Kuvatakse viimase eduka impordi andmeid.` |

Viewers never see exception text. A sanitized diagnostic is visible to staff
through the feed-state and import-run admin.

## 07:00 scheduling

DashKoda contains no scheduler: no Celery, no Redis, no APScheduler and no
polling thread. Scheduling belongs to the host.

Templates are provided for both routes; the MVP deployment needs only the first:

- [`ops/unraid/sync_oigusloome_public.sh.example`](../ops/unraid/sync_oigusloome_public.sh.example)
  — the public sharing link
- [`ops/unraid/sync_oigusloome.sh.example`](../ops/unraid/sync_oigusloome.sh.example)
  — Microsoft Graph

Copy one, replace `<DASHKODA_DEPLOYMENT_DIRECTORY>`, make it executable, run it
by hand once, and only then schedule:

```text
0 7 * * *
```

Both templates take the same host `flock` path, so scheduling both by mistake
cannot start two runs at once. The PostgreSQL advisory lock is the primary
overlap protection and holds even across hosts and containers; the `flock` is
defence in depth.

**Cron uses the host clock.** Confirm the Unraid host is on `Europe/Tallinn`
before enabling it — Estonia observes daylight saving, so a host left on UTC
runs the job an hour off for part of the year:

```bash
timedatectl
date +'%Z %z %H:%M'
```

Expect `EET +0200` in winter and `EEST +0300` in summer. The application's own
advisory lock is the overlap guarantee; the `flock` in the template is optional
defence in depth.

**This pull request does not install or enable the schedule.**

## Secret handling

No token, client secret, sharing URL, redirect URL, signed URL, temporary path
or workbook content is ever written to the database, the audit trail, the logs or
the interface. `LegalWorkFeedState` stores only non-secret content metadata
(etag, size, modification time). Import diagnostics carry warning-code counts,
never rows. Audit summaries carry the source slug, the checksum, the byte size,
record counts, the reporting date and the result — never file content, never a
topic name and never a URL.

## Current limitations

- **Live acceptance of the full public-link import has not been completed.** The
  download, URL handling, XLSX validation and temporary-file lifecycle *have*
  been verified against the live link, repeatedly and against more than one
  published revision of the workbook. The remaining steps — publishing a
  snapshot, reporting unchanged on a repeat run — need a PostgreSQL instance and
  a workbook published with its Excel Table intact; see the publishing rule
  above.
- Live Graph acceptance has not been performed either: no credentials existed
  during development, so that collector is covered by mocked transports only.
- The importer requires the `tbl_oigusloome` Excel Table. A workbook generated
  without it is rejected.
- Only the current snapshot is read. There is no history view and no trend.
- The Graph route's `--force` cannot re-publish byte-identical content. The
  public route needs no `--force` at all.
- No chart is rendered: a list communicates these values more honestly.

### Publishing the workbook without breaking it

The workbook must reach OneDrive **exactly as the generator wrote it**. Uploading
it by opening it in Excel Online and saving silently strips the `tbl_oigusloome`
Excel Table from `DATA`, and the importer then rejects the file.

The symptom is unmistakable: the file *grows* by a few kilobytes, `WARNINGS`
keeps `tbl_oigusloome_warnings`, only one table part remains inside the package,
and the error is `DATA lehel puudub Exceli tabel 'tbl_oigusloome'`. Every count
can still be perfectly correct.

Upload it as a plain file replace — *Upload → Files* in the OneDrive web
interface, or let the sync client push it — and never through the "Open in Excel
Online" round trip. The same applies to any manual edit: opening the generated
workbook in Excel to look at it, then saving, is enough to break it.

If it does get stripped, the round-tripped copy also syncs back down and
overwrites the good local file. Re-run the generator, or recover the earlier
version from OneDrive version history.

This is a publishing-workflow rule, not something the importer should tolerate.
The Excel Table is what makes `DATA` an addressable, contractually-shaped table
rather than an arbitrary grid, so the contract must not be relaxed to accept a
workbook without it.

## Rollback to a previous snapshot

Snapshots are retained, so recovery is a matter of moving the current flag.
There is no admin action for it by design; use the shell deliberately:

```powershell
docker compose exec -T web python manage.py shell
```

```python
from apps.legal_work.models import LegalWorkSnapshot
current = LegalWorkSnapshot.objects.get(is_current=True)
target = LegalWorkSnapshot.objects.get(pk=<previous id>)
current.is_current = False
current.save(update_fields=["is_current"])
target.is_current = True
target.save(update_fields=["is_current"])
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `puudub Exceli tabel 'tbl_oigusloome'` | the generator did not write the DATA table |
| `Toetamata skeemi versioon` | the workbook declares a version outside `{1.0, 1.1}` |
| `CONTROL ei ole DATA lehega kooskõlas` | the workbook's own summary disagrees with its rows |
| `CONTROL lehe väljad on tühjad` | a required CONTROL key was written with an empty value |
| `DATA lehel puudub Exceli tabel` after an upload | the workbook was round-tripped through Excel Online, which strips the table |
| `Avaliku töövihiku seadistus on puudulik` | `OIGUSLOOME_PUBLIC_URL` is unset |
| `Vastuseks tuli text/html asemel Exceli faili` | the link no longer permits downloading, or it now needs sign-in |
| `Jagamislink ei ole kättesaadav (404)` | the sharing link was revoked or regenerated |
| `Jagamislink keeldus ligipääsust (403)` | anonymous access to the link was withdrawn |
| `Allalaaditud ZIP-pakend ei ole XLSX töövihik` | the response was a valid archive but not a workbook |
| `Microsoft Graphi seadistus on puudulik` | one of the five environment variables is unset |
| `Microsoft Graph keeldus ligipääsust (403)` | `Files.Read.All` is missing or not admin-consented |
| exit code `3` | another synchronization was still running |
| `importrun_unique_successful_live_import` | this exact content already imported successfully |
