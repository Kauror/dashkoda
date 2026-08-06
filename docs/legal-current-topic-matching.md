# Automatic Koda.ee links on legal-work topics

A legal-work topic on `/oigusloome/` is a **link to its Koda.ee consultation
page** when a deterministic matcher decided, automatically, that the two are the
same thing. Every other topic is plain text.

There is no manual approval, no manual link field, no override, no LLM and no
embedding anywhere in this feature. A topic becomes clickable because the
matcher classified it `matched`, and for no other reason.

```text
public koda.ee "Hetkel käsil" listing (+ its pager)
  → detail pages linked from that listing, and nothing else
  → validate and normalise to plain text
  → canonical JSON → SHA-256 → metadata-only artifact → ImportRun
  → immutable CurrentTopicSnapshot
                                    ↘
                                      deterministic matcher (no LLM)
                                    ↗
current LegalWorkSnapshot, open records only
  → immutable LegalCurrentTopicMatchSnapshot
  → read-only admin (inspection)
  → topic_links.resolve_topic_links()  ← one query, exact-snapshot rules
  → /oigusloome/ and the overview card: a link, or plain text
```

Collection and matching happen only in scheduled commands. **A page render reads
PostgreSQL and makes no outbound request of any kind.**

## Scope

Collected: the listing at `https://www.koda.ee/et/meie-moju/hetkel-kasil`,
including its `?page=N` pager, and the detail pages linked from teaser cards on
it whose canonical path starts with `/et/meie-moju/hetkel-kasil/`.

Deliberately **not** collected, and not modelled either — there is no table, no
column and no placeholder for any of it:

- `/et/meie-moju/hetkel-kasil/arhiiv`, the archive of finished consultations;
- `Meie arvamus`, the Chamber's published opinions;
- general Koda.ee news;
- public PDFs, internal PDFs, attached draft legislation, ministry documents;
- any non-Koda host, any user-supplied URL, any URL found outside a listing card.

Opinions and PDFs remain **future work**. Nothing in this repository models them.

## Collection contract

One fixed endpoint, configured in `config/settings/base.py` and reachable by no
other route:

| Setting | Value |
| --- | --- |
| `KODA_CURRENT_TOPICS_URL` | `https://www.koda.ee/et/meie-moju/hetkel-kasil` |
| `KODA_CURRENT_TOPICS_SOURCE_SLUG` | `koda-public-current-topics` |
| `KODA_CURRENT_TOPICS_PATH_PREFIX` | `/et/meie-moju/hetkel-kasil/` |
| `KODA_CURRENT_TOPICS_ARCHIVE_PATH` | `/et/meie-moju/hetkel-kasil/arhiiv` |
| `KODA_CURRENT_TOPICS_MAX_PAGES` | 5 |
| `KODA_CURRENT_TOPICS_MAX_ITEMS` | 50 |
| `KODA_CURRENT_TOPICS_MAX_BYTES` | 4 MiB per response |
| `KODA_CURRENT_TOPICS_BODY_MAX_LENGTH` | 6000 characters |

Transport is `apps.core.public_http.fetch` with the existing Koda.ee allowlist
(`koda.ee`, `www.koda.ee`): HTTPS only, every redirect hop re-checked before it
is requested, a fresh session per call so no cookie survives a run, no
authentication, bounded retries, explicit size caps and sanitized errors. No
response body ever reaches a log, an audit summary or the database.

There is **no `--url` option**, no form and no admin field through which anyone
can introduce an address.

Every candidate link must be HTTPS on an allowed host, sit under the path
prefix, not be the listing itself, not be the archive, not repeat, and be within
the URL length bound. A listing exceeding `KODA_CURRENT_TOPICS_MAX_ITEMS` is
**rejected, not truncated**.

One unreachable detail page fails the **whole run**, unlike the events calendar
which skips one and keeps the rest. A catalogue with a gap makes the matcher
report `unmatched` for a record whose page merely timed out, and — now that
matching reaches the interface — silently drops a link the reader had yesterday.
The previous catalogue stays published instead.

### Three properties of the live markup that shape the parser

Verified against the site, not assumed:

1. **The listing is paginated.** Eight cards a page, two pages at the time of
   writing. Reading only the first page silently drops the tail.
2. **The listing card carries no year.** It prints a day and an abbreviated
   month in two spans. The *detail* page prints a full `dd.mm.yyyy`, so that is
   where `published_date` comes from; the listing supplies ordering only.
3. **`field--name-body` is not unique to the article.** The site's
   language-switcher block carries the same class, so the body region requires
   `field--name-body` *and* `field--type-text-with-summary`. The page also
   repeats its own node in a sideblock, so each region captures its first
   occurrence only.

Parsing is depth-tracked with the standard library's `html.parser`, scoped to
element subtrees rather than to string positions. **No HTML parsing dependency
was added.**

## What is stored

`CurrentTopicItem` retains normalised plain text and nothing else: `content_key`,
`canonical_url`, `title`, `listing_summary`, `body_text`, `published_date`,
`feedback_deadline`, `named_organization`, `source_order`. Raw HTML is never
stored; scripts, styles, navigation and markup are dropped during extraction.

**Deadline extraction** is anchored on `hiljemalt`, which is how every current
page states it. Supported: `4. märtsiks`, `26. märtsiks`, `12. märtsil`,
`9. märtsiks 2027`, `09.03.2027`. A missing year is taken from the publication
date, rolling forward once when the deadline would otherwise precede it. Without
a publication date, or when two deadlines on one page disagree, **no deadline is
stored** — a deadline the matcher half-believes is worse than an absent one.

**Organisation extraction** uses a closed vocabulary of the ~20 bodies these
pages name as the drafter, matched as lowercase stems so Estonian case endings
are covered without a morphological analyser. An unknown organisation yields an
empty value, never a guess.

**Change detection** hashes the normalised fields the matcher consumes, sorted
by content key. Markup churn, build hashes and two reordered unchanged cards all
produce the same checksum and publish no new snapshot.

## The matcher

`apps/legal_work/current_topic_matching.py`. Deterministic code over the two
normalised texts. No model, no embedding, no vector store, no external service,
and no dependency added.

Inputs: open records (`is_open=True`) in the **current** `LegalWorkSnapshot`, and
every item in the **current** `CurrentTopicSnapshot`. Closed records are out of
scope — a matter already answered and sent raises a different question from a
live consultation.

**Why the title is not the primary signal.** The workbook records the instrument
— *"pakendiseaduse muutmise seaduse eelnõu"* — while Koda.ee publishes the
invitation — *"Mida arvad plaanitavatest pakendiseaduse muudatustest?"*. Scoring
title against title would systematically under-rate true pairs, so the legal
topic is scored against title, listing summary and article text together.

**Why character n-grams matter as much as tokens.** Estonian inflects.
`pakendiseadus` and `pakendiseaduse` share no token and share every 4-gram but
one.

### Normalisation

`apps/legal_work/text_normalisation.py`, version `1.0`, folded into the matcher
version so a change to any rule is visibly a different matcher.

NFC normalisation then case folding; Estonian quotation marks (`„ “ ”`) and
dashes (`– — −`) folded to ASCII; whitespace collapsed; **diacritics preserved**,
because stripping them would merge `ohutus` and `õhutus`; editorial phrases
removed as whole phrases (`mida arvad`, `anna teada`, `jaga mõtteid`, `kas
toetad`, `plaanitavad muudatused`, `eelnõu kohta` and variants), so `eelnõu
kohta` disappears while `eelnõu` alone survives; a short stop list; a separate
`GENERIC_TOKENS` set which is **damped, not removed**; acronyms read before case
folding; identifiers matched narrowly (`123 SE`, `45 OE`, Riigi Teataja
references) because a bare number is not an identifier — the pages write "eelnõu
punktid 1, 2 ja 4", which are paragraph references.

### Weighted signals

| Signal | Weight | Definition |
| --- | --- | --- |
| character n-gram | 0.30 | the better of Dice over title+summary 4-grams and containment of the record's *discriminating* 4-grams in the whole page |
| rarity coverage | 0.30 | the share of the record's idf mass the entry accounts for; generic tokens keep 25 % of their weight |
| uncommon-token overlap | 0.20 | hits on tokens only one catalogue entry uses, saturating at three |
| deadline agreement | 0.12 | 1.0 exact, 0.7 within 3 days |
| organisation overlap | 0.08 | 1.0 identical key, 0.6 partial |

Scores are stored on a **0–100** scale as decimals, so a threshold comparison
means the same thing in PostgreSQL, in Python and in a test.

The last two signals only exist when **both sides** state the fact, so the
applicable weights are **renormalised**. Without that, a record with no deadline
could reach at most 88 and one with neither at most 80, and one threshold would
silently mean something stricter for exactly the sparse records this feature
exists to enrich.

### Evidence-only signals

Recorded as codes, never weighted, because they fire rarely on the real pages:
`identifier-match`, `identifier-conflict`, `acronym-match`, `date-proximate`.

### Contradictions

A contradiction **blocks acceptance outright** rather than shaving points off,
because a high text score is exactly the situation in which a contradiction
matters most.

| Code | Rule |
| --- | --- |
| `deadline-conflict` | both sides state a deadline and they differ by more than 14 days |
| `impossible-chronology` | the page was published more than 60 days before the record was received |
| `organization-conflict-unsupported` | the two name different bodies **and** share no discriminating token |
| `generic-overlap-only` | the only words in common are generic legal vocabulary |

A conflicting organisation alone does **not** block: the workbook's `recipient`
records who the opinion is sent to, which is usually but not always who drafted
the instrument.

### Thresholds and decisions

```text
matcher version   1.0-norm1.0
automatic match   score ≥ 62.00  and margin ≥ 12.00  and no blocking contradiction
plausible floor   score ≥ 38.00  and no blocking contradiction
```

- **matched** — clears the automatic threshold and the margin over the next
  acceptable candidate. **This is the only decision that becomes a link.**
- **ambiguous** — a plausible candidate exists but not every high-confidence
  condition holds. Plain text.
- **unmatched** — no candidate is both plausible and unblocked. Plain text. The
  rejected front-runner is still recorded with its score and evidence, which is
  what makes the admin useful for calibration.

These thresholds were chosen from synthetic tests written to resemble the real
corpus, where true pairs score 65–98 and false pairs 12–19. They are revisited
from what production produces; the read-only admin exists to make that
inspectable, and a change ships as a new `MATCHER_VERSION`.

**The base rate is the real risk.** Nine catalogue entries face roughly thirty
open records, so most open records genuinely have no match. A matcher scoring
every record against its nearest of nine will find "the best of nine" every
time, and the best of nine is usually wrong. The plausibility floor is the
load-bearing threshold, and a run reporting mostly `unmatched` is correct rather
than broken.

## How a link reaches the page

`apps/legal_work/topic_links.py`. One bounded query per page, no matter how many
rows are drawn.

### Exact eligibility

A viewer-facing link is offered only when **all** of these hold:

1. a current `LegalWorkSnapshot` exists;
2. a current `CurrentTopicSnapshot` exists;
3. a current `LegalCurrentTopicMatchSnapshot` exists;
4. that match snapshot references **that exact** current legal snapshot;
5. that match snapshot references **that exact** current-topic snapshot;
6. the match row references the exact `LegalWorkItem` being displayed;
7. the decision is exactly `matched`;
8. `best_candidate` is present;
9. the candidate belongs to the referenced current-topic snapshot;
10. the candidate URL still passes the defensive URL validation below.

Conditions 1–9 are expressed **in the query itself**, including the two that
catch staleness — the displayed row must belong to the same legal snapshot the
match was computed from, and the candidate to the same catalogue snapshot, both
written as `F()` comparisons. A stale pair can never be fetched, let alone
rendered.

When any condition fails the topic renders as plain text. There is no fallback
to an older snapshot, to the highest-scoring ambiguous candidate, to a
title-derived URL guess, to another Koda.ee feed, or to anything entered by
hand. **A stale match is no match. No link is preferable to a wrong link.**

### Defensive URL validation

The collector validates a URL before storing it; this validates it again before
rendering it, which also covers a row written before a rule tightened. Required:
HTTPS; hostname exactly `koda.ee` or `www.koda.ee`; path beginning
`/et/meie-moju/hetkel-kasil/`; not the listing; not the archive; no username or
password in the URL; within the stored length bound.

Deliberately **no availability check**. Whether the page still responds is not
knowable without a request, and a render never makes one. A stored URL that
fails validation renders as plain text and raises nothing a viewer can see.

### One resolution per page

The Õigusloome page draws the same record in up to four lists — `Lähenevad
tähtajad`, `Hetkel töös`, `Viimati välja läinud`, `Uusimad sisse tulnud` — and
the overview card lists it under three tabs. Every collection is materialised
first, the whole set of displayed ids is resolved in **one** query, and the
resulting `legal_item_id → URL` mapping builds every presentation object on the
page.

That is what guarantees a record is linked in *every* list it appears in or in
none of them. There is one lookup and one answer, not one lookup per list, so
the two cannot disagree.

### Presentation objects

`LegalTopicPresentation` is a frozen dataclass holding the imported `item` and a
resolved `public_url` that may be empty; `DeadlinePresentation` adds the urgency
wording. **No model instance is mutated and no attribute is attached to one**, so
a presentation decision can never be persisted by accident. Templates reach the
imported row through `.item`; the shared `legal_topic` component reads only
`.topic` and `.public_url` and knows nothing about matching, decisions, scores
or snapshots.

## Viewer behaviour

On `/oigusloome/` and on the overview's Õigusloome card:

| Decision | Rendering |
| --- | --- |
| `matched`, all eligibility rules satisfied | link to the Koda.ee page, with a visually-hidden "(avaneb uuel lehel)" note |
| `ambiguous` | plain text |
| `unmatched` | plain text |
| no current match snapshot | plain text |
| match belongs to an older legal snapshot | plain text |
| candidate belongs to an older catalogue snapshot | plain text |
| stored URL fails validation | plain text |

Ordinary viewers never see a score, a runner-up score, a margin, a confidence, an
evidence or contradiction code, the matcher version, the words matched/ambiguous/
unmatched, or any collection or matching state. No matching badge and no
data-quality warning is added to the viewer page.

The catalogue is **not** a fifth global freshness source. The denominator stays
four, counting the modules a viewer actually reads.

## Current-only semantics

This phase uses only the current `Hetkel käsil` catalogue, and the consequence is
deliberate:

- when a consultation closes, Koda.ee drops it from the listing;
- the next collection publishes a catalogue without it;
- after the next match run, that page can no longer supply a link;
- the legal record stays visible on the page as **plain text**.

DashKoda does not retain a page as an active link merely because it matched
yesterday, does not carry a link forward by `record_id` — record ids are not
assumed stable across legal snapshots — and does not collect the archive to
paper over this. That is an accepted limitation of the first implementation.

## Failure and last-good behaviour

The Õigusloome page always renders.

**Collection fails** — the previous catalogue snapshot and the previous match
snapshot both stay in storage, legal data is untouched, and links appear only
where the current match snapshot still satisfies the eligibility rules above.

**The workbook moved to a newer legal snapshot but matching has not run yet** —
current legal data is shown with plain-text topics. Yesterday's match rows are
never applied to new legal items.

**Matching fails** — the previous match snapshot stays current, no partial
snapshot is published, and no stale link is shown against a different legal
snapshot. Publication is verified before a single row is written and the whole
publication is one transaction.

A Koda.ee or matching failure cannot affect the legal workbook synchronisation,
the current legal snapshot, global dashboard freshness or the three existing
public Koda.ee feeds. Each runs under its own source, advisory lock, import run
and transaction.

## Snapshots

| Model | Immutable except | Current |
| --- | --- | --- |
| `CurrentTopicSnapshot` | `is_current` | one per source |
| `CurrentTopicItem` | — | — |
| `LegalCurrentTopicMatchSnapshot` | `is_current` | exactly one, enforced by a partial unique index |
| `LegalCurrentTopicMatch` | — | one decision per legal item per snapshot |

A match snapshot carries **no source, no artifact and no import run**: nothing
was downloaded and no file exists. Its identity is exactly
`(legal_snapshot, current_topic_snapshot, matcher_version)`, a unique constraint,
which makes "identical inputs report unchanged" a single `exists()`.

## Why `LegalWorkItem` is not modified

Every import rebuilds a complete new snapshot from the workbook, so a match
result written onto an imported row would be erased overnight. The results live
in their own immutable snapshot keyed to the exact rows they describe, and the
address is resolved at **read time**. Both match relations use
`related_name="+"`, so no reverse accessor exists from a workbook row.

`LegalWorkItem` has no `public_url`, no `current_topic_url`, no relation field,
no editable mapping field, no score and no decision. The Excel contract, workbook
generation, workbook validation, snapshot publication, the synchronisation
commands, record ids and source rows are all unchanged, and nothing is ever
written back to the workbook or to OneDrive.

## Why no LLM

For nine catalogue entries a deterministic matcher is reproducible, auditable
from its stored evidence codes, free, offline, and correct in a way a model's
output cannot be verified to be. Every score can be recomputed from the inputs
and explained by the codes stored beside it.

## Admin inspection

Everything is registered through the project's `ReadOnlyAdmin`. There is **no
add, change, delete, approve, reject, override, force-match, suppress or
manual-URL action anywhere.** Adding one would turn an automatic feature into a
data-entry one with a second, invisible source of truth.

What the admin is for is *understanding* — why a link appeared, why an obvious
pair did not, and what the evidence and margins looked like — so the weights and
thresholds in `current_topic_matching.py` can be corrected in code, reviewed, and
released as a new matcher version. **Staff inspection informs the matcher; it
never overrides it.**

`Koda.ee hetkel käsil teemad` shows title, canonical URL, publication date,
feedback deadline, named organisation, a summary excerpt and the snapshot.

`Õigusloome sobitamise tulemused` shows the legal record id and topic, the
decision, score, runner-up score, margin, plausible-candidate count, the
candidate's title and address, the evidence codes and the matcher version.
Filters: decision, score band, evidence code, current snapshot, matcher version,
legal snapshot and current-topic snapshot.

A row visible in the admin is not proof that a viewer sees a link: the viewer
path additionally insists both source snapshots are still current.

## Commands

```bash
python manage.py sync_legal_current_topics [--dry-run] [--json]
```

```bash
python manage.py match_legal_current_topics [--dry-run] [--json]
```

Exit codes: `0` imported, unchanged or a successful dry run; `1` failed; `3`
another run held the lock. Each takes its own PostgreSQL advisory lock, distinct
from each other and from the workbook synchronisation's.

JSON output carries aggregates and identifiers only:

```json
{"result": "imported", "detail": "…", "dry_run": false, "snapshot_id": 4,
 "legal_item_count": 31, "current_topic_count": 9, "matched_count": 2,
 "ambiguous_count": 3, "unmatched_count": 26, "matcher_version": "1.0-norm1.0"}
```

No topic, candidate title, URL, page text, evidence text or HTML ever appears in
command output, in a log or in an audit summary.

Both commands are idempotent: unchanged inputs recompute nothing.

### The automatic sequence

1. the legal workbook synchronisation publishes a legal snapshot;
2. `sync_legal_current_topics` publishes a catalogue snapshot;
3. `match_legal_current_topics` publishes a match snapshot;
4. viewer pages read the current results from PostgreSQL.

**The viewer never runs either command and never contacts Koda.ee.**

### Intended schedule — not installed

| Time (Europe/Tallinn) | Command |
| --- | --- |
| 07:00 | `sync_oigusloome_public` |
| 07:15 | `sync_legal_current_topics` |
| 07:20 | `match_legal_current_topics` |

Two **separate** wrappers, five minutes apart, each with its own flock file, its
own PostgreSQL advisory lock, its own log and its own exit code:
`ops/unraid/sync_legal_current_topics.sh.example` and
`ops/unraid/match_legal_current_topics.sh.example`. A collection that fails
therefore leaves a readable failure of its own instead of being buried inside a
combined job.

The gap is for legibility, not correctness. The two take different locks and
could safely overlap; a matcher that runs before a fresh catalogue exists simply
scores against the previous one and reports that honestly.

**This repository installs nothing.** A failure in either cannot affect the
workbook synchronisation, which is a separate job on a separate source with a
separate lock.

## Audit actions

| Action | When |
| --- | --- |
| `legal_work.current_topic_snapshot_imported` | a new catalogue published |
| `legal_work.current_topic_sync_unchanged` | checked, nothing changed |
| `legal_work.current_topic_sync_failed` | collection failed |
| `legal_work.current_topic_match_generated` | a new match snapshot published |
| `legal_work.current_topic_match_unchanged` | same inputs and version |
| `legal_work.current_topic_match_failed` | the run failed |

Summaries carry the source slug, snapshot ids, a checksum, counts and the
matcher version — never a title, topic text, URL, page body or HTML.

## Known limitations

- Thresholds were calibrated on synthetic data. Production behaviour is
  inspected in the admin and corrected in code as a new matcher version.
- Nine entries make the idf corpus very small, so rarity weighting is coarse.
- A record whose page names the instrument nowhere — neither headline, summary
  nor body — cannot be matched by text at all.
- Identifier and acronym signals are unweighted because the current pages carry
  almost none.
- The organisation vocabulary is a fixed list needing an update when a ministry
  is renamed.
- Records with neither a deadline nor a named organisation are scored on three
  signals rather than five, so their scores are noisier even after
  renormalisation.
- **Current-only**: a consultation that closes loses its link at the next match
  run, by design. Collecting the archive would change this and is out of scope.
- Only `Hetkel käsil` is matched. `Meie arvamus`, opinion PDFs and other Koda.ee
  sections are future work and are not modelled.

## Deployment and acceptance

After review and merge, on the deployment:

1. collect the live catalogue — `sync_legal_current_topics --json`;
2. run the matcher — `match_legal_current_topics --json`;
3. re-run both and confirm each reports `unchanged`;
4. in `/admin/`, open `Õigusloome sobitamise tulemused` filtered to the current
   snapshot and read every `matched` row — the catalogue is small enough that no
   sampling is needed;
5. open `/oigusloome/` and confirm the linked topics are the ones the admin
   listed as `matched`, and that everything else is plain text;
6. adjust weights or thresholds in code if a false link appears, bump
   `MATCHER_VERSION`, and re-run. A version change publishes a new snapshot and
   leaves the previous one intact for comparison;
7. install the two schedules only once the output has been read at least once.

A wrong link sends a lawyer to the wrong consultation, so a false `matched`
decision is the failure to watch for and the reason the thresholds are
deliberately conservative.
