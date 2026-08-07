# Matching sent legal work to Chamber opinion documents

Phase 2. A legal-work record whose opinion has already gone out can now link to
an internal resource page carrying the letter that answered it. Records still
open and unanswered keep their consultation links exactly as before.

## The problem the workbook does not solve

A resource address has to keep working after tomorrow's import, so it needs a
durable identity for the legal matter. The workbook offers two candidates and
**neither works**.

Measured across seven production legal snapshots:

| Candidate key | Duplicates | Stable across all 7 | Denoting *different* matters |
|---|---|---|---|
| `record_id` | 0 | 604 / 610 | **128 (120 materially different)** |
| `(source_year, source_nr)` | 1 | 604 / 609 | **128 (120 materially different)** |
| `(normalized topic, received date)` | 1 | 601 / 613 | **0** |

`record_id` tracks a row's **position**. `OIG-2025-0124` was source row 124 and
"Kagu-Eesti ettevõtluse arengutoetus" in the first snapshot, then row 125 and an
unrelated matter in every snapshot after — one inserted row renumbered
everything below it. `source_nr` shifts identically.

A resource page keyed on either would, sooner or later, show a reader the wrong
Chamber opinion.

## Durable identity (Design B)

```
matter_key = SHA-256 over canonical JSON of
             {identity version, normalized topic, received date}
```

Canonical JSON rather than concatenation, so no crafted topic can collide with a
different topic and date. Normalisation folds case and whitespace but **keeps
diacritics**: `ohutus` and `õhutus` are different words and different matters.

The content key's lifecycle across the same seven snapshots is clean — 601
present throughout, 7 contiguous additions, 5 contiguous removals, and **zero
gap patterns**: nothing disappears and returns.

`LegalMatter` carries an opaque UUID, and that UUID is the only thing that
appears in a URL. `LegalMatterAlias` records every observed `record_id`,
`source_nr` and `source_row` per snapshot as **immutable provenance** — what an
operator sees in the spreadsheet and will quote in a question. **Nothing
resolves a matter by an alias**; that is the positional identifier the durable
key replaced.

### Versioning and collisions

`IDENTITY_VERSION` participates in the hash, so a future canonicalisation change
mints new identities rather than silently reinterpreting established ones. Old
resource addresses stop resolving instead of quietly pointing somewhere else.

When two materially different records in one snapshot claim the same key, the
matter is flagged `has_ambiguous_identity`, **excluded from linking**, and left
for a general correction to the derivation. No production row is edited to break
the tie — doing so would hide the very thing that needs fixing.

**Known limitation:** editing a topic mints a new matter, and the old address
stops resolving. That happened five times across seven snapshots. It is the safe
direction: a dead link is recoverable, a link to the wrong opinion is not.

## Eligibility

```python
OPINION_ELIGIBLE = Q(sent_status=SENT) & Q(sent_date__isnull=False)
```

One definition, consumed by the matcher population, link resolution, the
resource page, the document endpoint and the tests.

Sent **without** a date is deliberately excluded. The date is the strongest
signal available, and a record that cannot say when its opinion went out cannot
be matched confidently — matching it on subject similarity alone is exactly how
a plausible wrong link is made.

The consultation rule requires *not sent*; this one requires *sent*. The two
populations are disjoint by construction and a test asserts that no record can
satisfy both.

## The matcher

```
opinion-1.1-norm<normaliser>-extract<extractor>
```

Its own weights, thresholds and rarity corpus — never a consultation matcher's.
A consultation page is an editorial invitation written by Koda.ee; an opinion
letter is formal correspondence carrying an outgoing date, an outgoing number,
an addressee and a subject line that no consultation page has.

Weights, renormalised over the signals that actually apply so a record missing
one is not capped: date 0.34, subject 0.26, instrument 0.18, rarity 0.12,
recipient 0.10. Thresholds: match 70.00, ambiguous 45.00, minimum winning margin
12.00.

### Dates carry this matcher, and the calibration is measured

Across the 759-document handover, **369 letters carry exactly their filename's
date and 269 carry that date plus one day** — the filename records drafting, the
letter's own `Meie <date>` records sending, usually the next working day.

| Gap | Treatment |
|---|---|
| 0–3 days | full credit; **never** a contradiction |
| 4–30 days | linearly decaying credit |
| 31–90 days | neither credit nor contradiction |
| over 90 days | **blocking**, except `supplementary_opinion` and `follow_up` |
| earlier than `received_date` | **blocking** — an opinion cannot predate its request |

Agreement uses whichever of the document's two dates is closer, because
insisting on one would be a coin toss about which the Chamber happened to
record. All 659 dated documents in the measured distribution fall inside the
full-credit band.

**No record, filename or document is special-cased anywhere in the matcher.**

### Blocking contradictions

Conflicting proposal identifiers; impossible chronology; a document predating its
request; only generic vocabulary in common; a quarantined, failed or
OCR-needing document; and any classification that cannot lead.

A **recipient mismatch alone never blocks** — the addressee of a letter and the
institution that owns the draft are routinely different bodies. It simply
contributes no positive evidence.

A classification alone, a date alone, or a recipient alone never creates a
match.

### Primary document preference

1. `opinion`
2. `joint_opinion`
3. `follow_up`, only when clearly the principal letter
4. `supplementary_opinion`, only when no main letter exists

`annex`, `supporting_document` and `unknown` can **never** be primary. At most
one primary relation per decision, enforced by a partial unique index rather
than by the code that writes it.

Grouping a secondary document with a primary requires both a near date and
shared subject evidence. A shared ministry and a shared week is how unrelated
business ends up attached to the wrong letter.

## Link precedence

A branch on the record's own status, not a fallback chain:

- **sent** → opinion resource when matched, else plain text
- **open and unsent** → current consultation, then archive, else plain text

A sent record must never fall through to a consultation: that page invites
comment on a draft the Chamber has already answered, so showing it beside an
answered record tells a reader the opposite of the truth.

Implemented in the existing central resolver, so every surface — the Õigusloome
tables, the latest-sent list, the deadline cards, the overview — gets the same
answer for the same record. Three bounded queries per page, none per row.

## The resource page and the document endpoint

`/oigusloome/arvamused/<uuid>/` shows the topic, sent date, recipient, act type,
the primary letter and any grouped documents. It shows **no** score, runner-up,
margin, evidence code, matcher version, decision label, storage key, source
path, digest or database id.

`/oigusloome/arvamused/dokument/<uuid>/` serves one PDF. The identifier belongs
to the **blob**, not to a snapshot-scoped catalogue entry, so a published
address keeps working across imports. The route accepts a UUID converter only,
so a traversal attempt never parses. Serving verifies the document is attached
to an accessible resource on the current snapshots, refuses quarantined blobs,
resolves strictly beneath the store root, and sets `Content-Type:
application/pdf`, `X-Content-Type-Options: nosniff`, `Cache-Control: private,
no-store` and a sanitised `Content-Disposition`.

The managed store is reachable through this view and nowhere else — no static
route, no media route, no public volume mount.

## Command

```bash
python manage.py match_legal_opinion_documents --dry-run --json
```

```bash
python manage.py match_legal_opinion_documents --json
```

Its own advisory lock. Identical inputs — the same legal snapshot, the same
catalogue snapshot and the same matcher version — return `unchanged`. JSON
output is aggregates only: counts, a snapshot id and the matcher version, never
a topic, filename, recipient, subject, document text or path. A failure leaves
the previous match snapshot current.

**Not scheduled by this repository.** The intended time is 08:00
`Europe/Tallinn`, installed as the usual UTC pair, and only after production
acceptance shows zero false primary links.

## Admin

Read-only, for every model. No approve, reject, edit, delete, manual relation,
manual primary selection, force-match, suppress-match, or per-row re-run.

Staff *can* see the score, runner-up, margin, evidence codes, contradictions and
the exact snapshots — everything needed to answer "why did this link appear, and
why did that obvious pair not?". That diagnostic detail is the point of the
admin and is deliberately absent from the viewer. A wrong decision is corrected
by changing weights or thresholds, releasing a new matcher version, and
re-running.

## Not in this phase

No recurring-folder migration, no public `Meie arvamus` collection, no public
PDF ingestion, no OCR, no search.
