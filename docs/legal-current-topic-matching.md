# Automatic matching of legal-work records to Koda.ee current topics

**Status: implemented in code, shadow-only, and not scheduled.** Nothing in this
document reaches a viewer. No link produced by this feature appears on
`/oigusloome/`, on the overview, or anywhere else a viewer can see, and no
schedule installs either command on the host. The feature exists so that its
proposals can be measured against reality before anyone decides whether they are
good enough to publish.

```text
public koda.ee "Hetkel käsil" listing (+ its pager)
  → detail pages linked from that listing, and nothing else
  → validate and normalise to plain text
  → deterministic canonical JSON → SHA-256
  → metadata-only SourceArtifact → ImportRun
  → immutable CurrentTopicSnapshot
                                    ↘
                                      deterministic matcher (no LLM)
                                    ↗
current LegalWorkSnapshot, open records only
  → immutable LegalCurrentTopicMatchSnapshot
  → read-only Django admin
  ✗ no viewer page, no public_url, no fifth freshness source
```

## Scope of this first phase

Collected:

- the listing at `https://www.koda.ee/et/meie-moju/hetkel-kasil`, including its
  `?page=N` pager;
- the detail pages linked from **teaser cards on that listing** whose canonical
  path starts with `/et/meie-moju/hetkel-kasil/`.

Deliberately **not** collected, and not modelled either — there is no table, no
column and no placeholder for any of it:

- `/et/meie-moju/hetkel-kasil/arhiiv`, the archive of finished consultations;
- `Meie arvamus`, the Chamber's published opinions;
- general Koda.ee news;
- public PDFs, internal PDFs, attached draft legislation, ministry documents;
- any non-Koda host, any user-supplied URL, any URL found outside a listing card.

There is no manual mapping form, no override, no approval workflow, no OpenAI
client, no embedding, no sentence transformer and no vector database.

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
can introduce an address. Collection happens only in the scheduled command; a
page render never performs a request.

Every candidate link must be HTTPS on an allowed host, sit under the path
prefix, not be the listing itself, not be the archive, not repeat, and be within
the URL length bound. A listing exceeding `KODA_CURRENT_TOPICS_MAX_ITEMS` is
**rejected, not truncated**: publishing an arbitrary prefix of an unexpectedly
large listing would silently lose records.

One unreachable detail page fails the **whole run**, unlike the events calendar,
which skips one and keeps the rest. A calendar with a gap is still a calendar; a
catalogue with a gap makes the matcher report `unmatched` for a legal record
whose page merely timed out, and a wrong shadow result is worse than yesterday's
correct one. The previous catalogue stays published.

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
element subtrees rather than to string positions, so a link elsewhere on the
page cannot be mistaken for a catalogue entry. **No HTML parsing dependency was
added.**

## What is stored

`CurrentTopicItem` retains normalised plain text and nothing else:

| Field | Source |
| --- | --- |
| `content_key` | SHA-256 prefix of the canonical URL path |
| `canonical_url` | the validated absolute detail URL |
| `title` | the detail page's `<h1>`, falling back to the card title |
| `listing_summary` | the card's visible summary, else the page intro |
| `body_text` | intro plus article text, bounded |
| `published_date` | the detail page's `dd.mm.yyyy` |
| `feedback_deadline` | extracted only when explicit and unambiguous |
| `named_organization` | matched against a closed vocabulary |
| `source_order` | listing position |

Raw HTML is never stored. Scripts, styles, navigation and markup are dropped
during extraction, not filtered afterwards.

### Deadline extraction

Anchored on `hiljemalt`, which is how every current page states it, so an
unrelated commencement date in the prose is never read as the Chamber's own
deadline. Supported forms: `4. märtsiks`, `26. märtsiks`, `12. märtsil`,
`9. märtsiks 2027`, `09.03.2027`.

When the year is absent it is taken from the publication date, rolling forward
once when the deadline would otherwise fall before publication — which is how a
December announcement names a January deadline. Without a publication date there
is no calendar context and **no deadline is stored**. Two disagreeing deadlines
on one page also store nothing: a deadline the matcher half-believes is worse
than an absent one.

A missing deadline is a valid page and never rejects it.

### Organisation extraction

A closed vocabulary of the ~20 bodies these pages name as the drafter, matched
as lowercase stems so Estonian case endings ("Rahandusministeeriumis",
"Kliimaministeeriumi") are covered without a morphological analyser. Longer
names are tried first so "Majandus- ja kommunikatsiooniministeerium" is never
shadowed by a shorter partial. An organisation outside the vocabulary yields an
empty value; this field reports what the page said and never a guess.

### Change detection

The canonical checksum covers the **normalised fields the matcher consumes**,
sorted by content key. Markup churn, a new build hash, whitespace changes and
two reordered cards with unchanged content all produce the same checksum and are
correctly reported as `unchanged`, publishing no new snapshot.

## The matcher

`apps/legal_work/current_topic_matching.py`. Ordinary deterministic code over
the two normalised texts. No model, no embedding, no vector store, no external
service, and no dependency added.

### Inputs

- open records (`is_open=True`) in the **current** `LegalWorkSnapshot`;
- every item in the **current** `CurrentTopicSnapshot`.

Closed records are outside this phase: a matter that has been answered and sent
raises a different question from a live consultation link.

### Why the title is not the primary signal

The two sides name the same thing differently and always will. The workbook
records the instrument — *"pakendiseaduse muutmise seaduse eelnõu"* — while
Koda.ee publishes the invitation — *"Mida arvad plaanitavatest pakendiseaduse
muudatustest?"*. Scoring title against title would systematically under-rate true
pairs, so the legal topic is scored against the whole of what an entry says:
title, listing summary and article text. The formal instrument is almost always
named in the body even when the headline avoids it.

### Why character n-grams matter as much as tokens

Estonian inflects. `pakendiseadus` and `pakendiseaduse` share **no token** and
share every 4-gram but one. The alternative is a morphological analyser, which
would be one more thing this repository has to keep correct for no other reason.

### Normalisation

`apps/legal_work/text_normalisation.py`, version `1.0`, folded into the matcher
version so a change to any rule is visibly a different matcher.

- NFC Unicode normalisation, then case folding;
- Estonian quotation marks (`„ “ ”`) and dashes (`– — −`) folded to ASCII;
- whitespace collapsed; tokens keep internal hyphens and ordinals;
- **diacritics preserved** — stripping them would merge `ohutus` and `õhutus`;
- editorial phrases removed as whole phrases: `mida arvad`, `anna teada`,
  `jaga mõtteid`, `kas toetad`, `plaanitavad muudatused`, `eelnõu kohta` and
  their variants — so `eelnõu kohta` disappears while `eelnõu` alone survives;
- a short stop list of grammatical words and consultation vocabulary;
- a separate `GENERIC_TOKENS` set (`seadus`, `eelnõu`, `määrus`, `muutmise`,
  `ministeerium`, …) which is **damped, not removed**, and which defines the
  `generic-overlap-only` contradiction;
- acronyms read before case folding (`FATCA`, `OECD`, `CARF`);
- identifiers matched narrowly: `123 SE`, `45 OE`, Riigi Teataja references. A
  bare number is not an identifier — the pages write "eelnõu punktid 1, 2 ja 4",
  which are paragraph references.

Nothing here stems, lemmatises or calls a service.

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
could reach at most 88 and one with neither at most 80, and a single threshold
would silently mean something stricter for exactly the sparse records this
feature exists to enrich.

### Evidence-only signals

Recorded as codes, never weighted, because they fire rarely on the real pages
and tuning weights on cases the matcher has not yet met would be guesswork.
Shadow evaluation measures them first.

- `identifier-match` / `identifier-conflict`
- `acronym-match`
- `date-proximate` — publication within 45 days of the record's receipt

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

A conflicting organisation alone does **not** block. The workbook's `recipient`
records who the opinion is sent to, which is usually but not always who drafted
the instrument, so it blocks only when nothing else supports the pair.

### Thresholds and decisions

```text
matcher version   1.0-norm1.0
automatic match   score ≥ 62.00  and margin ≥ 12.00  and no blocking contradiction
plausible floor   score ≥ 38.00  and no blocking contradiction
```

- **matched** — the best acceptable candidate clears the automatic threshold and
  the margin over the next acceptable candidate.
- **ambiguous** — at least one acceptable candidate exists but not all the
  high-confidence conditions hold. A best candidate scoring above the automatic
  threshold with too small a margin carries `narrow-margin`.
- **unmatched** — no candidate is both plausible and unblocked. The rejected
  front-runner is still recorded with its score and evidence, because that is
  what threshold calibration is made of.

Every number on a row describes the same field: the acceptable candidates when
there are any, and the rejected front-runner when there are none.
`candidate_count` is the number of **plausible** candidates.

**These thresholds are starting points chosen from synthetic tests, not
validated truth.** They were selected so that, on synthetic pairs written to
resemble the real corpus, true pairs score 65–98 and false pairs score 12–19.
Real data will not be that clean.

### The base rate is the real risk

Nine catalogue entries face roughly thirty open legal records, so **most open
records genuinely have no match**. A matcher scoring every record against its
nearest of nine will find "the best of nine" every time, and the best of nine is
usually wrong. The absolute plausibility floor is therefore the load-bearing
threshold, and a first shadow run that reports mostly `unmatched` is the correct
result rather than a broken one.

## Why `LegalWorkItem` is not modified

Every import rebuilds a complete new snapshot from the workbook. A match result
written onto an imported row would be erased by the next morning's
synchronisation, so the results live in their own immutable snapshot keyed to the
exact rows they describe. Both relations use `related_name="+"`, so no reverse
accessor exists from a workbook row — the first step towards a selector
decorating viewer data is closed off in the schema rather than in review.

The Excel contract is untouched. Nothing is ever written back to the workbook or
to OneDrive.

## Why no LLM

For nine catalogue entries a deterministic matcher is reproducible, auditable
from its stored evidence codes, free, offline, and correct in a way a model's
output cannot be verified to be. Every score can be recomputed from the inputs
and explained by the codes stored beside it. Before spending a model on the
problem, the shadow evaluation below has to show that deterministic signals are
not enough — and it has not been run yet.

## Snapshots and last-good behaviour

| Model | Immutable except | Current |
| --- | --- | --- |
| `CurrentTopicSnapshot` | `is_current` | one per source |
| `CurrentTopicItem` | — | — |
| `LegalCurrentTopicMatchSnapshot` | `is_current` | exactly one, enforced by a partial unique index |
| `LegalCurrentTopicMatch` | — | one decision per legal item per snapshot |

A match snapshot carries **no source, no artifact and no import run**: nothing
was downloaded and no file exists. Its identity is exactly
`(legal_snapshot, current_topic_snapshot, matcher_version)`, a unique constraint,
which is what makes "identical inputs report unchanged" a single `exists()`
rather than a checksum over fabricated bytes.

Before a single row is written the publication service verifies that every
decision names a record in `snapshot.legal_snapshot`, every candidate belongs to
`snapshot.current_topic_snapshot`, exactly one decision exists per open record,
no record was left undecided, a `matched` decision names a candidate, every score
is on scale, the runner-up does not exceed the winner, and the margin equals
their difference. After writing, the declared counts are checked against the
actual rows. Any failure aborts the transaction and leaves the previous match
snapshot current.

A Koda.ee outage or a matcher failure cannot affect the legal workbook
synchronisation, the current legal snapshot, `/oigusloome/`, global dashboard
freshness or the three existing public Koda.ee feeds. Each runs under its own
source, its own advisory lock, its own import run and its own transaction.

## Commands

```bash
python manage.py sync_legal_current_topics [--dry-run] [--json]
```

```bash
python manage.py match_legal_current_topics [--dry-run] [--json]
```

Exit codes, matching the repository's convention: `0` imported, unchanged or a
successful dry run; `1` failed; `3` another run held the lock. Each command takes
its own PostgreSQL advisory lock, distinct from each other and from the workbook
synchronisation's.

JSON output carries aggregates and identifiers only:

```json
{"result": "imported", "detail": "…", "dry_run": false, "snapshot_id": 4,
 "legal_item_count": 31, "current_topic_count": 9, "matched_count": 2,
 "ambiguous_count": 3, "unmatched_count": 26, "matcher_version": "1.0-norm1.0"}
```

No topic, no candidate title, no URL, no page text, no evidence text and no HTML
ever appears in command output, in a log or in an audit summary.

### Expected future schedule — not installed

When shadow evaluation passes, the intended UTC cron pair is collection shortly
after the workbook synchronisation, then matching after it. **Neither is
installed by this pull request**, and nothing in this repository schedules them.

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
matcher version. They never carry a title, topic text, a URL, page body or HTML.

## Admin inspection

Everything is registered through the project's `ReadOnlyAdmin`. There is **no
add, change, delete, approve, reject or override action anywhere**, and that is
the product rather than a precaution: this phase measures the matcher, it does
not operate it. A manual mapping workflow is a different feature with different
failure modes, and adding a button now would quietly turn a measurement exercise
into a data-entry one.

`Koda.ee hetkel käsil teemad` shows title, canonical URL, publication date,
feedback deadline, named organisation, a summary excerpt and the snapshot.

`Õigusloome sobitamise tulemused` shows the legal record id and topic, the
decision, score, runner-up score, margin, plausible-candidate count, the
candidate's title and address as an openable link, the evidence codes and the
matcher version. Filters: decision, score band (high / mid / low), evidence code,
current snapshot, matcher version, legal snapshot and current-topic snapshot.

The candidate link is a staff inspection tool behind `/admin/`. It is not the
viewer-facing link, and nothing on `/oigusloome/` reads this model.

## Production shadow acceptance

Run after this pull request is reviewed and merged, on the deployment, in this
order.

1. Collect the live catalogue and confirm what it found:

   ```bash
   docker compose exec web python manage.py sync_legal_current_topics --json
   ```

2. Run the matcher:

   ```bash
   docker compose exec web python manage.py match_legal_current_topics --json
   ```

3. Confirm both are idempotent — a second run of each must report `unchanged`
   and must not create a second snapshot.

4. In `/admin/`, open `Õigusloome sobitamise tulemused` filtered to the current
   snapshot and inspect **every** row. The catalogue is small enough that no
   sampling is needed.

5. Record each row's real outcome externally as correct / incorrect / no-match.
   Do not record it in DashKoda: there is no field for it, deliberately.

6. Measure precision, recall, ambiguous rate, false-link rate and unmatched rate,
   on the `matched` decisions specifically.

7. Adjust the thresholds, weights, stop vocabulary and contradiction rules in
   `current_topic_matching.py` and `text_normalisation.py`, bump
   `MATCHER_VERSION`, and re-run. A version change publishes a new snapshot and
   leaves the previous one intact for comparison.

**The bar for exposing links to viewers is zero false links across a full
inspected run, sustained over several catalogue refreshes.** A wrong link on a
legal record is worse than no link: it sends a lawyer to the wrong consultation.

## Known limitations

- Thresholds are calibrated on synthetic data only. This is the whole reason the
  feature is shadow-only.
- Nine entries make the idf corpus very small, so rarity weighting is coarse.
- A record whose page names the instrument nowhere — neither headline, summary
  nor body — cannot be matched by text at all.
- Identifier and acronym signals are unweighted because the current pages carry
  almost none. If shadow evaluation shows they fire, they should be weighted.
- The organisation vocabulary is a fixed list and will need updating when a
  ministry is renamed.
- Records with neither a deadline nor a named organisation are scored on three
  signals rather than five, so their scores are noisier even after
  renormalisation.
- The archive is not collected, so a consultation that closes between two runs
  simply leaves the catalogue.

## Next planned pull request

Expose verified `matched` decisions to viewers: supply `public_url` from the
current match snapshot for high-confidence decisions only, keep plain text for
`ambiguous` and `unmatched`, reuse the existing `legal_topic` component
unchanged, add last-good match behaviour, install the two schedules, and show
enrichment status on the legal-work module only — **not** as a fifth global
freshness source. That pull request depends entirely on the acceptance above
passing.
