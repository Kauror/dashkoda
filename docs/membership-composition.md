# Membership composition

What kinds of organisations the Chamber's membership is made of — size classes,
counties, sectors, tenure bands and joining years — derived from a dated roster
export and stored as counts only.

## What this source is, and what it is not

`membership-roster-composition` holds aggregates from the Chamber's own member
list. It is a **third** membership source and is never merged with the other
two:

| Source | Counts |
| --- | --- |
| `koda-public-members` | member profiles published in the Koda.ee directory |
| `membership-internal-board-reports` | membership as the board's reports state it |
| `membership-roster-composition` | organisations in one dated roster export |

The first two are membership *totals* that measure different things and are
never added, averaged or continued into one another. The third is not a total at
all: it answers "what kinds of organisations are these", and its row count is a
property of one export on one day. The page says so wherever the number appears,
because three unlabelled member counts on one screen would invite exactly the
comparison none of them supports.

## Privacy: what this importer never stores

The roster carries a company name, a registry code, a street address, a
director's name, two contact addresses and a free-text comment.

**None of it is persisted, logged, printed or audited by this importer.** Three
independent things would each have to fail before an identity could be stored:

1. **the reader never builds a record.** `composition_import.read_roster`
   streams a row out of the workbook, hands six scalars to
   `composition.build_member_row`, and lets the row go on the next iteration.
   There is no list of members, no DataFrame, no intermediate file;
2. **the classifier returns buckets.** `MemberRow` has nine fields, all of them
   vocabulary keys or an integer count of days. There is no name, code, address
   or comment field to carry one;
3. **the models cannot hold one.** `MembershipCompositionValue` stores a
   vocabulary key, a vocabulary label and an integer.

The same rule governs what the importer *says*. Diagnostics, `--json` output,
audit summaries and every exception message carry counts, column names and
vocabulary terms only. A parse failure names the column, never the value.

`tests/membership/test_composition_import.py` imports a workbook full of
invented identities and then searches every field of both models, the
`ImportRun` and the audit trail for any of them. Nothing may match.

The workbook itself is not stored either. The registered artifact is
metadata-only, carrying the server-computed checksum under the fixed non-secret
reference `roster:membership-composition:<sha256>`. Storing the file would put a
member list on a served path and inside every backup.

**Since August 2026 this is a statement about this importer, not about the
whole application.** The member register reads the same export and deliberately
*keeps* a curated subset of its rows, because the members-list page and the
roster-versus-directory comparison cannot be built from counts. That decision,
the columns it covers and the ones still modelled nowhere are documented in
[member-register.md](member-register.md). Nothing here changed: the composition
models still cannot hold an identity, and the two importers remain separate
sources with separate snapshots.

## Models

`apps/membership/models/composition.py`.

| Model | Holds |
| --- | --- |
| `MembershipCompositionSnapshot` | one dated reading: its date, checksum, row count, mapping versions, median tenure and per-dimension coverage |
| `MembershipCompositionValue` | one count, at the grain (snapshot, population, dimension, category) |

Two models rather than one per chart: a table per dimension would multiply every
time a question was added, and a single unconstrained key/value store would let
anything be written. The vocabularies are constrained in code and the
combination is unique.

A published snapshot is immutable. A newer export is a **revision**: the
previous snapshot stops being current, keeps its rows and its checksum, and
gains a pointer to the one that replaced it. There is no delete action.

## Classification

`apps/membership/composition.py`. Nothing in it touches Django or the database,
so the whole vocabulary is testable against synthetic rows without PostgreSQL.

Four rules run through it:

- **an unrecognised value is `unknown`, never the nearest neighbour.** A status
  the Chamber adds next year appears as unclassified and is counted in the
  coverage figure rather than folded silently into `Koja liige`;
- **zero employees is its own band.** Fifteen rows of the current roster report
  it, and putting a nought into `1–9` would be a guess dressed as a measurement;
- **tenure is measured from the snapshot date, not from today.** A June export
  must not gain six months of tenure by being read in December;
- **a thin category is suppressed, not zeroed.** A ratio built on three
  organisations is noise.

### Dimensions

| Dimension | Source column | Notes |
| --- | --- | --- |
| `status` | `Staatus` | three literal values, mapped explicitly |
| `legal_form` | `Vorm` | OÜ, AS, MTÜ, SA, TuÜ, FIE |
| `employee_size` | `Töötajate arv` | Eurostat classes, plus a separate zero band |
| `region` | `Maakond` | fifteen counties, folded to ASCII before lookup |
| `sector` | `Nace kood` | NACE Rev. 2 **section**, from the division |
| `tenure_band` | `Algus kp.` | completed years at the snapshot date |
| `join_cohort` | `Algus kp.` | the calendar year the member joined |

`Töötaja vahemik` is **deliberately not read**. Excel has coerced two thirds of
its values into dates, so `1-4` arrives as a timestamp; the integer column
beside it is complete and unambiguous. A test asserts the corrupted column stays
unread.

Geography comes from the structured county column and nothing parses an
address. Address parsing would be the one part of this importer reading a
free-text field capable of holding anything, and a structured column that is
99.9 % populated makes it unnecessary.

Sector maps NACE divisions to sections because the roster spans 78 divisions and
78 bars answer no question. The ranges are the published classification, so the
mapping can be versioned and checked rather than argued about:
`MEMBERSHIP_SECTOR_MAPPING_VERSION`.

### Populations

| Population | Means |
| --- | --- |
| `all_current` | every organisation in the export |
| `recent_joiners_365_current` | those whose recorded start date is inside the 365 days before the snapshot |

The second is deliberately long-winded, because the short version would be a
lie. It is **not** everyone who joined during the year: anyone who joined and
left again before the snapshot is not in the roster at all, and no source in
this application records them. Every label on the page says
`Viimase 12 kuu jooksul liitunud tänased liikmed`.

## Growth index

The one derived statistic, and it is a ratio of two shares:

```text
recent share of a category ÷ overall share of that category × 100
```

100 is equal representation, above is over-represented among recent joiners,
below is under-represented. That is stated on the page beside the chart. It is
descriptive: no model, no smoothing, no significance test and no claim that a
difference will persist.

Categories below `MIN_OVERALL_FOR_INDEX` (20) or `MIN_RECENT_FOR_INDEX` (5) are
**named as withheld** rather than drawn at zero or at 100, because "not measured
reliably" and "exactly average" are different statements. The floors were chosen
against the real distribution: 3 400 members and 178 recent joiners, so five
recent members is about 3 % of that population and one organisation cannot move
the index by more than about a fifth.

## What this source cannot answer

The roster's `Lõpp kp.` column is **empty for every row**. There is no departure
date anywhere in it, and no other source in this application records which
organisations left and when.

So the following are not calculated, and the reason is a lack of evidence rather
than unbuilt UI:

- one-year, three-year or any retention rate;
- churn;
- cohort survival;
- average membership lifetime.

Tenure is valid, because it is measured on members who are here. The joining-year
distribution is valid, because it describes today's membership. Neither is
retention. `join_cohort_chart` carried a footnote saying so on the page until
2026-08-17; the caveat is still true, it is just stated here rather than on the
chart now.

## Import

```bash
python manage.py import_membership_composition \
    --roster <path> --snapshot-date 2026-06-09 --dry-run --json
```

The snapshot date is **required** and is not read from the file name. A rename
must not be able to change what the data means, and every tenure and cohort in
the import is measured against this date.

Re-running the identical file reports `unchanged` and writes nothing — the
import key is the importer, the schema version and the workbook's SHA-256.
Importing a *different* file over an existing snapshot needs
`--supersede-previous`, which retires the current snapshot without deleting it.

A dry run reads and validates the whole workbook and writes no domain row, which
is also how to see what a new export would produce before it replaces what is on
the page.

### Validation

Structural violations raise and stop the import:

- a missing required column — refused rather than read leniently, because a
  page full of `Teadmata` looks like a finding;
- an empty file;
- a dimension whose categories do not reconcile with the row count;
- more recent joiners than members.

Facts about the source's own quality are reported as diagnostics and do not
block: unclassified values per dimension with their coverage percentage, start
dates after the snapshot, and unreadable start dates. A roster with a few
unclassified sectors is still a roster, and refusing it would leave the
dashboard with nothing rather than with a measured gap.

## Current roster, as imported

Aggregates only, from the export dated 2026-06-09:

| Figure | Value |
| --- | --- |
| Rows | 3 400 |
| Distinct registry codes | 3 400 — one row per organisation, no duplicates |
| Recent joiners still present | 178 |
| Median tenure | 3 824 days (10,5 years) |
| Members with 11+ years | 48,7 % |
| Status coverage | 100 % |
| Employee-size coverage | 100 % |
| Tenure-date coverage | 100 % |
| Region coverage | 99,9 % (3 rows blank) |
| Sector coverage | 99,9 % (5 rows without a usable code) |
| Legal-form coverage | 98,4 % (56 rows blank) |

## Interface

`fookus=koosseis` on `/liikmeskond/`. The focus is not offered until a roster
has been imported, because a navigation item leading to an empty page reads as a
fault.

Charts are horizontal bars throughout — Estonian sector and county names need a
readable line of text beside each bar rather than a rotated axis label — and
there is no pie anywhere: categories of similar size are hard to compare as
angles and the design system offers no pie component.

Ordinal dimensions (size, tenure) keep their scale order because the order *is*
the meaning. Nominal ones (county, sector) are ranked largest-first because for
them the ranking is most of the answer. A ranked chart folds its tail into
`Muu`, never into `Teadmata` — several small categories and "the source did not
say" are different things, and a reader cannot tell them apart once they are
added together. The chart's data table always lists every category in full.

The overview carries a four-fact preview under `Kes on meie liikmed?`, and it is
omitted entirely when no roster has been imported.
