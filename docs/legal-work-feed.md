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

## Microsoft Graph configuration

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

## Synchronization

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

A template is provided at
[`ops/unraid/sync_oigusloome.sh.example`](../ops/unraid/sync_oigusloome.sh.example).
Copy it, set the deployment directory, and schedule:

```text
0 7 * * *
```

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

No token, client secret, signed URL or workbook content is ever written to the
database, the audit trail, the logs or the interface. `LegalWorkFeedState`
stores only non-secret content metadata (etag, size, modification time). Import
diagnostics carry warning-code counts, never rows. Audit summaries carry counts
and dates, never file content.

## Current limitations

- Live Graph acceptance has not been performed: no credentials existed during
  development, so the collector is covered by mocked transports only.
- The importer requires the `tbl_oigusloome` Excel Table. A workbook generated
  without it is rejected.
- Only the current snapshot is read. There is no history view and no trend.
- `--force` cannot re-publish byte-identical content; change the workbook or
  wait for the generator to produce new bytes.
- No chart is rendered: a list communicates these values more honestly.

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
| `Microsoft Graphi seadistus on puudulik` | one of the five environment variables is unset |
| `Microsoft Graph keeldus ligipääsust (403)` | `Files.Read.All` is missing or not admin-consented |
| exit code `3` | another synchronization was still running |
| `importrun_unique_successful_live_import` | this exact content already imported successfully |
