# Internal membership history

The Chamber's own membership figures, as reported to its board, plus the
staff-only form that is how every future report is entered.

## What this source is, and what it is not

`membership-internal-board-reports` holds what the Chamber's board was told about
its own membership: total members, paying members, membership-fee income against
budget, new and departed members, and the distribution of joiners and leavers by
company size and by reason for leaving.

It is **a different source from `koda-public-members`**, which counts the member
profiles published in the public Koda.ee directory. The two are never merged,
never added, never averaged and never continued into one another. Concretely:

- they are separate `DataSource` rows and separate models;
- `apps/membership/selectors.py` reads only the public model and
  `apps/membership/internal_selectors.py` reads only the internal ones;
- the Liikmeskond page presents them under two headings, `Avalik
  liikmekataloog` and `Sisemine liikmeskonna aruanne`;
- when the two numbers differ the page says the definitions differ. A difference
  is expected, not a fault.

Neither source is described as paid membership, accounting membership, invoiced
membership or active CRM contracts. The internal source is what the board
reports said; the public source is how many profiles the website publishes.

The public homepage "uusi liikmeid sel aastal" metric remains out of scope and
does not exist anywhere. The internal new-member figures below are a different
thing: they come from the board reports and are labelled as internal reported
data wherever they appear.

## What is never stored

- no original Word document — the canonical CSV package is the contract, and the
  documents were never copied into this application;
- no extracted prose. The package's `raw_reference` column carries sentences
  lifted out of the source documents; it is validated as a required column and
  deliberately never read;
- no individual member: no name, no registration code, no per-member payment
  status. An absent column cannot leak.

## Package contract

The one-time import accepts a ZIP with this structure, and refuses a directory
of loose CSV files — the manifest and its checksums are what make the data
approved.

```text
IMPORT_README.md
manifest.json
data/
  source_documents.csv
  membership_snapshots.csv
  monthly_new_members.csv
  membership_size_movements.csv
  membership_removal_reasons.csv
  extraction_warnings.csv
  conflicts.csv
  coverage.csv
review/
  final_report.json
  membership_history_review.xlsx
```

Everything may sit inside a single top-level directory or at the archive root;
two competing roots are refused because `manifest.json` would then be ambiguous.

`apps/membership/package.py` treats the archive as hostile until every check
passes, and it holds no Django import so the whole contract can be exercised
without PostgreSQL:

1. no absolute path, no `..` segment, no backslash, no symlink;
2. bounded member count, member size, total uncompressed size and compression
   ratio, checked against the declared *and* the extracted size;
3. `IMPORT_README.md` and `manifest.json` are required;
4. the manifest's `schema_version` must be one this importer knows (`1.0`);
5. every declared file is verified by server-computed SHA-256 and byte size;
6. a member the manifest does not list is refused, not ignored;
7. every CSV must present its exact expected header, in order;
8. UTF-8 with or without a BOM;
9. every cross-table reference must resolve before anything is returned.

`coverage.csv` is validated but not imported. It reports how many documents each
month had, which is derivable from what *is* imported, and storing it would
create a second independently drifting answer to the same question.

Conflict rows identify the disagreeing documents by their original path. Those
paths are resolved to document identifiers during parsing and then dropped, so
no filesystem path travels past `MembershipHistoricalSourceDocument`, which is
the one admin-only table allowed to hold one.

### Dates coarser than a day

Some comparison rows name only a year, because the board document restated an
earlier year in a column headed `2014` rather than on a date. A `DateField` needs
one concrete day, so a coarse value is anchored to the **end** of the period it
describes. `observation_date_precision` travels with the row, the admin shows
it, and the interface renders such an observation as a year. Anchoring to the
start would have placed a year-end figure eleven months before the fact it
describes.

## Models

All in `apps/membership/models/internal.py`.

| Model | Holds |
| --- | --- |
| `InternalMembershipObservation` | one reported observation, or one row of evidence for a date |
| `MembershipHistoricalSourceDocument` | provenance for one board-report document; metadata only |
| `MembershipMonthlyNewMemberValue` | how many members joined in one calendar month |
| `MembershipSizeMovement` | joined/removed counts per company-size band |
| `MembershipRemovalReason` | departures per reported reason |
| `MembershipDataIssue` | one imported quality warning, and its resolution |
| `MembershipMetricConflict` | two documents disagreeing about one metric on one date |

`MembershipMetricConflict` is separate from `MembershipDataIssue` because the key
is different — a date and a metric rather than a warning identifier — and
because the selectors query it directly to decide which single metric point to
withhold while leaving the rest of that observation visible.

### Evidence hierarchy

One board report can yield two rows: its own current figures
(`merged_same_document`) and a comparison column restating an earlier year
(`reported_comparison`). Both are stored. Precedence, lowest first:

1. `manual` — a staff correction outranks every extraction;
2. `merged_same_document` — a document's own reading is first-hand;
3. `reported_comparison` — a later report restating an earlier year, second-hand.

Ties are then broken by quality status, then extraction confidence, then a stable
identifier, so the ordering is total and two runs cannot disagree. Exactly one
row per source and date carries `is_preferred_for_date`.

A comparison row is used only when no direct or manual reading exists for that
date. It is never deleted for losing: keeping it is what makes the provenance of
a disagreement readable later.

### Quality statuses

| Status | Meaning |
| --- | --- |
| `verified` | nothing disputed |
| `provisional` | reported before the period closed |
| `review_required` | an internally impossible value; the affected metrics are withheld |
| `conflicted` | another document disagrees; the disputed metric is withheld |
| `superseded` | replaced by a correction; no longer read by any selector |

### Immutability

A published observation is immutable. Only `quality_status` and
`is_preferred_for_date` may move, and the model refuses any other change. Child
movements and removal reasons refuse every change. A correction is therefore a
**new** observation that names the one it replaces; the replaced row keeps its
numbers, its children and its place in the audit trail.

## Quality policy

Defined once in `apps/membership/quality.py` and applied by both the importer and
the manual form, so no template contains a quality condition.

- **Evidence is never discarded to make a chart tidy.** A conflicted or
  impossible value stays in the database with the provenance that explains it.
  What changes is whether a selector will draw it, and that decision is
  reversible. Deleting the evidence would not be.
- **Omission is per metric, not per observation.** If two reports disagree about
  the fee budget on one date, the member count from that date is still shown.
- **Missing is not zero.** A withheld metric produces no point, never a zero, and
  never a line drawn across the gap.

Two specific rules are worth stating because both have real examples in the
approved package:

- **More paying members than members** withholds both figures and marks the
  observation `review_required`. Whichever number is wrong, the pair is not a
  fact — but the board reported them, so both are kept.
- **A collection percentage above 100 is not an error.** Revenue can exceed a
  budget. It is accepted whenever the reported amounts imply it, and questioned
  only when it disagrees with the amounts reported beside it, in which case the
  percentage alone is withheld and the amounts stay chartable.

Monthly values keep three states apart permanently: a **verified** value is a
number; a **conflict** keeps `new_members` null because no single figure is
authoritative; a month nobody reported has no row at all. An explicitly entered
`0` is a real value and stays distinct from all three. A database constraint
prevents a conflict row from ever carrying a value.

## Selectors

`apps/membership/internal_selectors.py`. Every one reads PostgreSQL only, uses
bounded date ranges, returns preferred non-superseded observations by default,
and returns `None` — never `0` — for anything absent.

```text
get_internal_membership_latest()
get_internal_membership_observations(date_from, date_to, metric)
get_internal_membership_trend(date_from, date_to)
get_paid_membership_trend(...)
get_fee_collection_trend(...)
get_monthly_new_members(years, include_provisional=True)
get_membership_size_movement(observation_id)
get_removal_reasons(observation_id)
get_internal_membership_quality_summary()
get_manual_entry_defaults(reporting_year)
get_observation_detail(observation_id)
```

`get_internal_membership_quality_summary()` returns counts only. No warning code,
no filesystem path, no parser detail and no conflicting value leaves it, which is
what lets the page state honestly how many points were omitted without exposing
why in terms a viewer cannot act on.

Resolving a conflict in the admin restores the metric to the charts. Nothing has
to be re-imported.

## One-time import

```bash
python manage.py import_membership_history --package <path> --dry-run --json
```

Arguments: `--package` (required), `--dry-run`, `--json`.

A dry run validates the whole contract, records the attempt and writes no domain
row. A live run writes every table inside one transaction and publishes only
after all of it succeeded; a failure at the last row leaves the database exactly
as it was. The import key is the package digest plus the importer's schema
version, so an identical repeat reports `unchanged` and writes nothing.

`--json` prints aggregate counts and identifiers only. It never prints source
content.

The package file is not stored. The registered artifact is metadata-only,
carrying the server-computed checksum and size under the fixed non-secret
reference `package:membership-history:<sha256>`. An artifact is importable when
it has a trusted checksum, not when it still has a file.

## Manual entry

Route, staff only:

```text
/admin/membership/internal-report/new/
/admin/membership/internal-report/<id>/correct/
/admin/membership/internal-report/<id>/
```

Wrapped in `admin.site.admin_view`, so an active Django staff account is
required. The viewer PIN middleware guards `/admin/` in addition: a viewer must
pass both gates and has no staff account to pass the second one with.

The form has six sections — report identity, main reported facts, the monthly
grid, joined/removed by size band, removal reasons, and the validation preview.
Every field except the date may be left blank, because older reports genuinely
omit figures and a form that demanded them would invite someone to type a number
nobody reported.

The flow is two-step and **stateless**. "Kontrolli" re-renders the same form with
the preview filled in and saves nothing; "Kinnita ja salvesta" is a second
submission of the identical fields and is the only action that writes. There is
no draft record and no session copy, so closing the tab leaves nothing behind.

### Hard errors

Missing or invalid date; negative counts or amounts; paying members greater than
total members; a monthly value after the observation month; a complete size or
reason table whose totals disagree with the corresponding reported figure; a
correction whose target has a different date without the explicit confirmation
tick.

### Warnings

A reported percentage that disagrees with the amounts; a collection above 100 %
that is nevertheless consistent; a monthly sum that differs from the year-to-date
figure; a report older than the latest observation; a substantial change from the
previous observation; missing optional sections. A warning asks for a second
look, not for a retype — confirming past it is the acknowledgement.

The public directory count is **never** a validation target.

### Publication

The submitted values become canonical JSON — sorted keys, fixed separators, ISO
dates, exact decimal strings — which is hashed. That hash is the content
identity, so an accidental double submit is recognised as the same report and
redirects to the record that already exists. A metadata-only artifact carries the
identity under `manual:membership-report:<uuid>`, an `ImportRun` records the
attempt, and publication goes through the same domain service the import uses.
Post/Redirect/Get throughout.

### Corrections

`Loo parandatud versioon` opens the form prefilled, including the child rows, so
a correction that changes one figure does not silently drop the distribution the
original carried. Saving creates a new revision, marks the previous observation
superseded and not preferred, and leaves its values and children untouched. A
changed month creates a superseding monthly value rather than editing the old
one. A superseded observation cannot be corrected again — the correction that
replaced it is the current record. There is no delete action anywhere.

## Audit

| Action | When |
| --- | --- |
| `membership.history_imported` | a live package import succeeded |
| `membership.history_unchanged` | an identical package was re-run |
| `membership.history_failed` | an import failed and rolled back |
| `membership.manual_observation_created` | a staff user published a report |
| `membership.manual_observation_superseded` | a correction retired a record |
| `membership.issue_resolved` | someone recorded a resolution |

Details carry the source slug, checksums, observation dates, aggregate row
counts, record identifiers, the reporting year, the correlation ID and the
actor. They never carry CSV bodies, source prose, user-entered notes, session
data or tracebacks. The note a user types is stored on the observation, where it
belongs, and is deliberately not copied into the audit summary.

## Chart semantics

Payloads are built on the server in `apps/membership/charts.py` and read from a
non-executable `application/json` block, so no chart needs an inline script or a
relaxed Content Security Policy. Every chart carries a text summary and a data
table that stay in the document — not a fallback, but the same numbers for every
reader.

| Chart | Form | Notes |
| --- | --- | --- |
| Total and paid members | line, real time axis | irregular observation dates, no interpolation |
| Monthly new members | line, months I–XII | provisional marked; conflicts absent, never zero |
| Fee collection | bars plus two percentage lines | reported and calculated shown separately; no gauge |
| Joined vs removed by size | diverging horizontal bars | canonical band order; supporter separate |
| Removal reasons | horizontal bars | counts and shares; no pie |

A chart is not rendered at all when it has nothing to draw, and the chart bundle
is loaded only on pages that draw one.

Viewer-facing quality copy stays at the level of `Ajalooline sisemine aruanne`,
`Osad ajaloolised punktid on vastuolude tõttu graafikult välja jäetud` and
`Kuvatakse kinnitatud või eelistatud vaatlus`, plus a count of what was omitted.
Source paths, warning identifiers, parser internals, raw conflicts and stack
traces are never shown.

## Deployment: the one-time import

Not yet performed. When it is, the package is copied to a temporary path on the
server and the sequence is:

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --dry-run --json
```

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --json
```

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-import-package.zip --json
```

The dry run validates and publishes nothing; the second call imports; the third
must report `unchanged`. The temporary copy can be removed afterwards — the
registered artifact carries the content identity, and the application never needs
the file again.

## No automation yet

There is no schedule, no recurring ingestion, no SharePoint, Graph or Power
Automate route, and no endpoint that accepts a remote file or URL for this
dataset. Future reports are entered through the staff form.

When an automated route is eventually built it can replace the manual form
without rewriting a single historical row, because both writers already publish
through the same domain service and both are governed by the same quality
policy.
