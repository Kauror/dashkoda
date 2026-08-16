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

## Where the package comes from

The board documents are read by a separate offline project:

**[`Kauror/membership-history-extractor`](https://github.com/Kauror/membership-history-extractor)** (private)

It is deliberately not part of this repository, and the boundary is the point:

```text
board documents  →  extractor  →  package  →  DashKoda importer  →  PostgreSQL
   (confidential)    (offline)    (the contract)   (this repo)
```

DashKoda never opens a board document. It validates a package and loads it, so
rendering membership analytics needs no LibreOffice, no legacy `.doc` parser and
no OCR in the runtime image, and the confidential corpus never approaches a
served path. The package is the only thing that crosses.

What lives over there:

- discovery and classification of the whole 2014–2026 corpus by content rather
  than filename;
- the **scope model**, which refuses to compare two numbers until it has
  established they describe the same business fact — a decision ending 25
  memberships is not the year-to-date 62 printed beside it in the same report;
- the size-band and departure-reason vocabularies, derived from a full inventory
  of the corpus rather than guessed;
- the deterministic package writer, which produces a byte-identical archive from
  the same sources.

Neither repository holds a board document, an extract or a built package.

**A rebuild is not something this application can do.** If the historical
figures need to change, the package is rebuilt over there, reviewed, and
imported through the sequence in *Deployment* below. Nothing in DashKoda edits
an imported figure — that is what makes the provenance on every row meaningful.

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
  decision_batches.csv                 # 2.0 only
  decision_batch_size_movements.csv    # 2.0 only
  decision_batch_reasons.csv           # 2.0 only
  new_member_periods.csv               # 2.0 only
  new_member_size_distribution.csv     # 2.0 only
review/
  final_report.json
  membership_history_review.xlsx
```

### Schema versions

`1.0` and `2.0` are both accepted. `2.0` adds the five files marked above and
nothing in `1.0` changed meaning, so a `1.0` package is parsed exactly as it
always was.

The bump is **major** rather than minor because a `2.0` package answers a
question `1.0` could not express at all: what one board decision did, as
distinct from what a year had done so far.

Which files are required depends on the declared version. A `1.0` package that
nevertheless carries a `2.0` table is refused rather than read leniently — the
manifest declares which contract is in force, and the two would otherwise
disagree about it.

`ParsedPackage.row_counts` reports **no key at all** for the `2.0` tables when
reading a `1.0` package. `decision_batches: 0` would say the package looked and
found none, when the truth is that it cannot describe batches. That is the same
missing-is-not-zero rule the data itself is held to.

Everything may sit inside a single top-level directory or at the archive root;
two competing roots are refused because `manifest.json` would then be ambiguous.

`apps/membership/package.py` treats the archive as hostile until every check
passes, and it holds no Django import so the whole contract can be exercised
without PostgreSQL:

1. no absolute path, no `..` segment, no backslash, no symlink;
2. bounded member count, member size, total uncompressed size and compression
   ratio, checked against the declared *and* the extracted size;
3. `IMPORT_README.md` and `manifest.json` are required;
4. the manifest's `schema_version` must be one this importer knows (`1.0` or
   `2.0`), and the files present must match the version it declares;
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
| `MembershipDecisionBatch` | one board decision's own list of departures |
| `MembershipDecisionBatchSizeMovement` | that batch by company-size band |
| `MembershipDecisionBatchReason` | that batch by reason family |
| `MembershipNewMemberPeriod` | new members over a span the board never split |
| `MembershipNewMemberSizeDistribution` | new members by size, for a month or a period |

### A decision batch is not a year-to-date figure

`MembershipDecisionBatch` is the model most likely to be misread, so the rule is
stated here as well as in the code: **a batch says what one decision did, and it
is never added to, compared with, or drawn beside a year-to-date total.** The
corpus contains a March 2021 decision ending 25 memberships beside a
`removed_members_ytd` of 62 on the same report. Those are different questions.

The model carries **two dates** because the sources do. The appendix states the
date it was compiled on (`as_of_date`, "04.03 seisuga"); the board signs later
(`decision_date`, "11.märts 2021"). Neither is derived from the other, and a
batch whose decision date the source never gave keeps `None` rather than
borrowing the as-of date. They are sometimes the same day — decision nr 6 of
2026 is both — and the interface names both only when they differ.

Nothing attaches a batch to an observation, however close the dates are.

### Two reason vocabularies, deliberately

`RemovalReasonKey` names the three aggregate categories the membership-overview
document itself reports for a year to date. `BatchDepartureReasonKey` names the
eight families the decision appendices' free text falls into. They describe
different facts at different scopes and neither replaces the other.

The batch families were derived from a full inventory of 2 258 reason rows
across 2014–2026, of which 95.1 % map into a named family. The rest stay `other`
or `unknown` rather than being forced into a neighbouring one. Mapping is
literal and clause-by-clause with no edit-distance matching, so a new wording
falls to `other` and appears in the review queue instead of being guessed at.

**`MembershipDecisionBatchReason` has no field capable of holding raw text, and
that is the guarantee.** The free reason written beside a member sometimes names
another company, so an absent column is the only reliable protection; a test
asserts the package header stays four columns wide.

### Two more size bands

`SizeBand` gained `group_company` and `unknown`. Both describe real rows: the
Chamber's own new-member template carries a `grupi ettevõte` line, and `***` is
how the source writes "size not known". 205 rows in the corpus cannot be
represented without them. A malformed `-1000` is **not** read as `1000+`, and
`50-90` is not read as `50-99`; both stay `unknown`.

`EMPLOYEE_SIZE_BANDS` now derives from `NON_EMPLOYEE_SIZE_BANDS`, so the new
keys cannot drift into a chart that means to show employee counts only.

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
get_internal_observation_span()
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
get_decision_batches(date_from, date_to, limit=60)
get_new_member_periods(date_from, date_to)
get_monthly_size_distribution(year, month)
```

`get_decision_batches()` returns `()` for a window with no decision, never a
zero: a period the board did not record that way is not a period in which nobody
left. Sizes come back in canonical band order and reasons largest-first — bands
are an ordinal scale whose order is the only thing the axis means, while reasons
have no inherent order, so ranking them is most of the answer.

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

## The page: four focuses behind one URL

`/liikmeskond/` answers four different management questions and `fookus` names
which one is drawn:

```text
fookus=ulevaade    Ülevaade               (default)
fookus=kasv        Kasv ja püsimine
fookus=koosseis    Koosseis
fookus=liikumine   Liikumine ja põhjused
```

`fookus=liikmemaks` was a fifth, holding the one fee-collection chart. Since
2026-08-16 that chart draws in the overview's trend section under the same
window control, and the retired key resolves to the overview.

It is an ordinary GET parameter, every control is a link, and an unknown value
renders the overview rather than raising — the same rule `ranges.py` applies to
a malformed date. A focus with nothing to draw is not offered at all, because a
navigation item leading to an empty page reads as a fault.

`fookus` is deliberately a new key rather than a reuse of `vaade`, which already
governs monthly-versus-cumulative inside the recruitment chart. One word
governing two unrelated things would make a bookmarked cumulative chart start
changing which page section existed.

A focus link carries the resolved window forward and nothing else. The chart
toggles are not carried, because a recruitment-chart choice means nothing on the
movement view, and landing a reader on a control state that does not apply is
how a control comes to look broken.

The overview is built to be read without interaction: the headline answers, the
membership trend with the fee history under one window, `Mis muutus?`,
`Sel aastal`, and — once a roster has been imported — a four-fact composition
preview. `apps/membership/intelligence.py`
assembles all of it and reads no database, taking the points the view already
fetched.

Two rules there are load-bearing:

- **the headline comparisons read their own bounded lookback**, not the drawn
  window. A reader who narrows the chart to six months has not asked for the
  year-ago readout to disappear;
- **the difference between `new_members_ytd` and `removed_members_ytd` is never
  called a net change.** They are two reported counts; subtracting them gives
  the gap between two reports, not the movement of the stock. The words `neto`,
  `netokasv` and `liikmeskonna muutus` appear nowhere near it, and a browser
  test asserts as much against the rendered page.

`Mis muutus?` is computed from the same numbers the charts draw, in a fixed
priority order rather than ranked by magnitude — a strip that reordered itself
would lose the reader's ability to look at the same place twice. No generated
prose, no model inference and no composite health score.

## Reconciliation

`apps/membership/reconciliation.py` checks whether a period's flows explain its
stock:

```text
expected end = start total + joined - removed
residual     = reported end - expected end
```

A residual is **evidence, never a correction**. Nothing overwrites a source or
decides which of the four figures is the one that is off. It lives under
`Andmete kohta` rather than beside a headline.

The preconditions are the substance. `new_members_ytd` counts from 1 January, so
the opening stock must be measured within `YEAR_BOUNDARY_TOLERANCE_DAYS` of the
year boundary: an October reading anchoring a year would leave November and
December in neither the opening total nor the flow counters, and the identity
would describe a period nobody measured while looking like a real finding. The
flows must also come from the same report as the closing total. A period failing
any precondition is unavailable with its reason, never a residual of zero.

## Composition is a third source

Aggregate composition of the member roster — size classes, counties, sectors,
tenure bands, joining years — lives in
[`docs/membership-composition.md`](membership-composition.md). It is a third
source and is never merged with either membership total: it answers what kinds
of organisations these are, not how many there are.

## Chart semantics

Payloads are built on the server in `apps/membership/charts.py` and read from a
non-executable `application/json` block, so no chart needs an inline script or a
relaxed Content Security Policy. Every chart carries a text summary and a data
table that stay in the document — not a fallback, but the same numbers for every
reader.

The page is four analytical tools, each answering one management question, each
carrying only the controls that govern it.

| Section | Question | Form |
| --- | --- | --- |
| Liikmeskonna areng | Is the membership growing, and how much of it has paid? | two lines, real time axis, year-ago readouts |
| Liikmemaksu laekumine | Is collection tracking towards the annual budget? | budget completion per year across the calendar year, 100% reference |
| Uute liikmete dünaamika | Is recruitment stronger or weaker than usual? | current year as bars, one benchmark line, monthly or cumulative |
| Liikmete liikumine | Which sizes are we gaining or losing, and why do members leave? | diverging bars by band; reasons ranked largest first |

Four things about these that are data decisions rather than drawing ones:

- **the fee chart draws the completion the amounts imply**, not the reported
  percentage. `quality.py` withholds the reported figure when it disagrees with
  the amounts, so the amounts are what survives a disagreement. Both keep their
  own column in the table, and a disagreement is disclosed in a footnote —
  never silently resolved. An observation with year precision has no day and is
  not placed on a within-the-year axis at all;
- **a cumulative recruitment line stops at the first unreported month.**
  Carrying on would draw a flatter slope that reads as a slowdown nobody
  measured; skipping the month would make the total mean "everything except the
  month we lost". An explicitly reported `0` accumulates like any other month;
- **a year-to-date figure is compared only against the same stretch of the
  previous year**, and refuses to exist at all if any elapsed month is unknown.
  July against a full twelve months is a collapse that never happened;
- **the departure counts in the size chart are negated for drawing only.** No
  reader-facing string ever carries that negation — not the bar label, not the
  tooltip, not the table. Net movement is derived for presentation, stored
  nowhere, and withheld for a band that reported only one direction.

Comparisons are built in `apps/membership/analytics.py`, which answers or
refuses and never reaches: a year-ago baseline is the observation nearest the
anniversary within 45 days, growth from zero has no percentage, and a
multi-year monthly average withdraws unless every named year reported that
month.

A chart is not rendered at all when it has nothing to draw, and the chart bundle
is loaded only on pages that draw one.

### Board-decision charts

Two more charts draw a decision batch: its reasons and its size bands, both
horizontal bars. They live in their own `section-decisions`, deliberately **not**
folded into the movement section. That section describes an observation's
year-to-date position; a batch describes what a single decision did. Drawing
them under one heading would invite exactly the addition this dataset exists to
prevent, so the section states the caveat on the page rather than only in a
comment:

> Ühe juhatuse otsuse enda nimekiri. Ei ole aasta algusest kogunenud arv ega ole
> sellega liidetav.

The reason chart ranks largest-first; the size chart never does, because the
bands are an ordinal scale. Each chart labels itself with both of the batch's
dates when they differ, so a reader can tell the day the members were counted
from the day the board signed.

`seed_e2e_data` publishes two batches, which is what makes the section visible to
the browser suite at all. Before that it drew nothing there, and a green suite
would have proved only that the parts work rather than that anything reaches
them — the same blind spot that hid the website-traffic section on `/nähtavus/`.

### How much history a trend draws

The overview card and the Liikmeskond page both offer the same range control,
and both read it from `apps/membership/ranges.py` — one vocabulary, so the same
window cannot be named two ways on two pages. The vocabulary is two dates,
`alates` and `kuni`. The Liikmeskond page offers them two ways: preset windows
as ordinary links that fill both dates in, and the native date fields under
`Kohandatud vahemik` for anything the presets do not cover. Both are plain GET,
so there is no JavaScript in the control and it works with the bundle blocked.

A preset the history cannot fill is not offered, for the same reason a
never-drawable window was never offered: two controls drawing the identical line
invite a reader to believe the second one failed. An eight-month history offers
only `Kõik`. The card opens on the last six
months and the page on the last twelve — both counted back from the newest
observation, so both roll forward on their own as reports arrive and neither
carries a date that has to be kept up to date. The page opened on five years
until it was clear that drew five repetitions of the same annual cycle, with
the current year squeezed into a fifth of the plot.

It replaced a row of fixed-window buttons that submitted `?vahemik=`. Those
keys (`6`, `12`, `24`, `36`, `60`, `koik`) are still read so a stale bookmark
keeps drawing the window it always drew; they are never rendered.

Three rules make a window honest, and none of them lives in a view:

- **the default window is measured from the newest observation, not from
  today.** The board report arrives when it arrives; anchoring to today would
  let a report four days late shorten the window by four days and drop its
  oldest point;
- **a window is clamped to the history.** The fields advertise the observation
  span with `min`/`max`, but attributes are advice; whatever actually arrives is
  folded back inside the observations, and the fields re-render the resolved
  window rather than the raw input. The control cannot be used to ask for an
  arbitrary or unbounded query;
- **unreadable input is not an error.** A malformed date, an unknown legacy key
  and no input at all end at the page's default window, so a stale bookmark or
  a hand-typed URL still renders the page. A history of a single observation
  date renders no control at all, because a control that cannot change anything
  reads as a control that is broken.

The card stops at three years because it draws a polyline at card width. The
long windows belong to the page, which draws the same data across the full page.

The three figures above the card's chart are the **latest** report and do not
move with the control. Narrowing the window changes how much history is drawn;
it does not change what the most recent report said.

### There is no monthly departure series

`MembershipMonthlyNewMemberValue` is the only per-month table and it counts
**joins**. Departures exist at observation granularity only: `removed_members_ytd`
(cumulative from 1 January), `suspended_members`, and the per-observation
breakdowns by size band and by reason.

A per-month departure figure could be differenced out of consecutive
`removed_members_ytd` values inside one calendar year. It is deliberately not,
and this is the reasoning rather than an omission: the value resets each January,
so January is never derivable; a month with no report yields nothing; and where
two months are missing, all the movement is attributed to the later one. None of
those three numbers was ever reported, and the quality policy above does not
allow one to be drawn as though it were.

Viewer-facing quality copy stays at the level of `Ajalooline sisemine aruanne`,
`Osad ajaloolised punktid on vastuolude tõttu graafikult välja jäetud` and
`Kuvatakse kinnitatud või eelistatud vaatlus`, plus a count of what was omitted.
Source paths, warning identifiers, parser internals, raw conflicts and stack
traces are never shown.

## Deployment: the one-time import

**A schema 1.0 package was imported on 2026-07-31**, from 148 membership-overview
documents: 296 observations, 234 monthly values, 2960 size movements, 435 removal
reasons, 522 warnings and 27 conflicts.

That extraction opened only the recurring overview documents — its candidate rule
was `filename contains 'liikmeskond seisuga'` — so the monthly "Uued liikmed"
spreadsheets, the "Otsuse nr N lisa" appendices, the formal decisions and the
board protocols were never read. Recovering them is what schema 2.0 exists for.

### Importing over an existing history

The `unchanged` check keys on importer, schema version and package digest, so it
recognises **the same package run twice and nothing else**. A rebuilt package has
a different digest, and raising the importer's schema version changes the key
even for an identical file. Neither is caught there, and each would write a
complete second copy of the history beside the first.

So a live import into a populated history stops before the transaction opens.
`--supersede-previous` is the explicit way through: it marks the existing
observations `superseded` and no longer preferred, which are the only two fields
a published observation permits changing. Nothing is deleted, no value is
rewritten, and the old rows keep their numbers, their children and their place in
the audit trail.

The sequence, with the package copied to a temporary path on the server:

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-2.0.zip --dry-run --json
```

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-2.0.zip --supersede-previous --json
```

```bash
docker compose exec -T web python manage.py import_membership_history --package /run/imports/dashkoda-membership-history-2.0.zip --supersede-previous --json
```

The dry run validates and publishes nothing; the second call imports and
supersedes; the third must report `unchanged`. A dry run is never blocked by an
existing history, so the first call is safe to run at any time.

A package that fails partway supersedes nothing — the guard runs inside the same
atomic block as the writes, so a broken rebuild cannot leave the history half
replaced.

The temporary copy can be removed afterwards: the registered artifact carries the
content identity, and the application never needs the file again.

## No automation yet

There is no schedule, no recurring ingestion, no SharePoint, Graph or Power
Automate route, and no endpoint that accepts a remote file or URL for this
dataset. Future reports are entered through the staff form.

When an automated route is eventually built it can replace the manual form
without rewriting a single historical row, because both writers already publish
through the same domain service and both are governed by the same quality
policy.
