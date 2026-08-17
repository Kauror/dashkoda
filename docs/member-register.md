# The member register

The Chamber's own member list, imported by hand, shown as a list, and compared
against what Koda.ee publishes today.

Two sources, and the whole design follows from the difference between them:

| Source | What it is | Freshness |
| --- | --- | --- |
| `membership-member-register` | the CRM export's own rows | accurate on its export date, then ages |
| `koda-member-directory` | the registration codes the public directory publishes | collected daily |

The roster knows employee counts, counties, sectors and joining dates; the
directory knows who is published right now. Neither can answer the other's
question, which is why the page shows both with their own dates rather than one
merged list.

## What is stored, and what is deliberately not

`apps/membership/models/register.py` is the exception to this app's older rule
that no row-level member data is stored anywhere in it. The rule stood until
August 2026 and `composition.py` still honours it; the register was a
deliberate product decision for the members-list page, because a list cannot be
drawn from counts and two sources cannot be compared per member without a
per-member identity.

Stored per roster row:

> name, legal form, member number, status (vocabulary key **and** the source's
> own wording), registry code, county, city, country, employee count,
> membership start date, NACE code and label, website

Present in the export and **modelled nowhere**:

> street address, postal index, general e-mail, billing e-mail, phone, fax,
> director's name, director's e-mail, VAT number, free-text comment, NACE
> comment

There is no column any of them would fit in, which keeps the boundary
structural rather than a rule an importer has to remember. A director's name in
particular is personal data the dashboard has no question for. Adding a column
later must be as deliberate as this module was.

The directory side stores less again: a registration code and a profile path,
plus when each was first and last seen. No name, county or phone number is
collected from koda.ee — the roster names every matched member, and an
unmatched code is shown by its own public profile link.

The export file itself is never stored. The registered artifact is
metadata-only, carrying the server-computed checksum under the fixed non-secret
reference `roster:member-register:<sha256>`.

## Importing a roster

The CRM writes **UTF-16 with a byte-order mark, tab-separated, named `.csv`**.
Both the encoding and the delimiter are detected from the bytes; opening the
file as UTF-8 succeeds well enough to produce garbage rather than an error, so
guessing is not an option. Dates are text (`dd.mm.yyyy`), which is the one thing
the CSV path makes easier than the xlsx path the composition importer needs.

```bash
python manage.py import_member_register --roster <path> --snapshot-date 2026-08-15 --dry-run --json
```

Then the same command without `--dry-run`. The snapshot date is required rather
than read from the file name — the export states no date of its own, and a
rename must not be able to change what the data means.

Re-running the identical file reports `unchanged` and writes nothing. A newer
export needs `--supersede-previous`, which retires the current snapshot without
deleting it or its rows.

`SCHEMA_VERSION` in `register_import.py` is part of the import key and must be
bumped whenever the parser's output shape changes. A version that does not move
with the reader is how this repository has previously produced a run that
decides to republish and a database that answers "that already exists".

The importer reports rather than refuses: duplicate registry codes (first row
wins), rows without a code, unreadable or future start dates, and statuses
outside the vocabulary each appear as a counted diagnostic. A roster with a few
of those is still a roster; refusing it would leave the page with nothing rather
than with a measured gap.

## Collecting the directory

`sync_koda_public --source directory`, on the same daily schedule as everything
else in that command. It reads the same `company-list` endpoint as the member
**count** and is deliberately a separate source with its own lock, import run
and transaction: the count is a settled aggregate series, and neither a failure
nor a schema change on the row level may touch it. The two fetch the list
twice, which is the price of that isolation and is the intended trade.

The rows are a **carry-forward register** rather than a snapshot per run: the
directory changes a handful of codes a month, and one snapshot per day would
store thousands of rows to record that four moved. A code seen today has its
`last_seen_at` refreshed, a new code is created, and a code that stops
appearing is marked unpublished with the moment it went. Nothing is deleted, so
`first_seen_at` answers "since when has this member been listed?" directly and
a restored member keeps its original date.

Each *distinct* published set is still registered as an artifact and an import
run carrying its canonical checksum, so what the directory published on a given
day stays provable. The reconciliation, though, is **not** gated on that run
being new: a member unpublished on Monday and restored on Tuesday returns the
list to a byte-identical set, and the register still has to bring the row back.
Reconciliation is idempotent, which is what makes running it every time safe.

The same plausibility guard the count uses applies here, and for the same
reason — a movement past both thresholds is far more likely to be a source
fault than membership news, and unpublishing hundreds of rows on a bad fetch is
the expensive kind of wrong. The run fails closed and the register is left as
it was.

## On the page

`/liikmeskond/?fookus=nimekiri`. A table, a search box, a status filter and a
pager — no chart, so the focus ships no chart JavaScript. Every control is an
ordinary GET, so a search, a filter and a page number are all bookmarkable and
survive the back button. Filtering and pagination happen in SQL; the view never
holds the whole register.

The list states its snapshot date before the first row. A members list rendered
without its date reads as current, and this one is a manual export that ages
until the next import.

## The comparison

Below the list, the two sources compared **by registration code only** — the
one field both of them state.

What it produces is three counts and two lists: matched, roster-only (named,
with each member's status) and directory-only (a code and its profile link).
What it deliberately does not produce is a combined, corrected or reconciled
membership number. No measurement in this application produces one, and a
difference does not mean either source is wrong: the roster ages between
exports, and a profile can be unpublished while the membership continues.

Roster rows without a registry code are counted out of the comparison and the
count is disclosed, because a member with no code cannot be looked for in the
directory.

## What this is not

- **not a membership total.** The roster's row count is a property of one
  export on one day and belongs beside neither the public directory count nor
  the internal board report. Three unlabelled member counts on one screen would
  invite exactly the comparison none of them supports — see
  [metric-contract.md](metric-contract.md);
- **not a live view of the CRM.** There is no API and no scheduled collection
  for the roster. It is as fresh as the last manual import;
- **not a directory scraper.** Only the fixed `company-list` endpoint is read,
  on the existing host allowlist. No profile page is fetched, and no route,
  form or setting anywhere lets a URL be supplied.
