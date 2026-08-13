# Consultation links on legal-work topics

A legal-work topic on `/oigusloome/` is a **link to its Koda.ee consultation
page** while that consultation is still the record's live business. Two sources
supply it, in a fixed order:

1. the **current** `Hetkel käsil` listing — the consultation is still open;
2. the **archive** — the consultation has closed but the legal matter has not.

Everything else is plain text. There is no manual approval, no manual link
field, no override, no LLM and no embedding anywhere in this feature.

For the current-listing collector and matcher in detail, see
[legal-current-topic-matching.md](legal-current-topic-matching.md). This document
covers the lifecycle across both sources and the archive specifics.

## The lifecycle

```text
a draft arrives                      → record open, nothing sent
Koda.ee opens a consultation         → current listing → current matcher → LINK
the consultation closes              → leaves current, enters archive
                                     → archive matcher → LINK (same URL)
the Chamber sends its opinion        → sent → PLAIN TEXT
                                     → (future: link to the opinion itself)
the matter concludes                 → closed → PLAIN TEXT
```

The address does not change when a consultation moves into the archive —
verified against the live site — so the fallback continues an existing link
rather than producing a new one.

## Consultation eligibility

Stated once, in `apps/legal_work/consultation.py`, and consumed by four query
paths: current matching, archive matching, viewer link resolution and the tests.

```python
CONSULTATION_ELIGIBLE = Q(is_open=True) & ~Q(sent_status=SentStatus.SENT)
```

| Record state | Consultation link? |
| --- | --- |
| open, nothing sent | **yes** |
| open, `not_sent` or `invalid` | **yes** — no opinion has gone out |
| open, **sent** | no |
| closed, anything | no |

**A sent record renders as plain text, deliberately.** Once the Chamber has
answered, the consultation page is finished business and what a reader wants is
the opinion. DashKoda does not have opinions yet — `Meie arvamus`, news items and
PDFs are a later resource pipeline — so the topic simply stops being a link.
That is a known gap, not an oversight, and it is the reason the eligibility rule
exists rather than just "is_open".

Applying this rule changed *which records the current matcher considers* without
changing how any of them is scored, so its version moved **1.0 → 1.1** while
every weight and threshold stayed byte-for-byte identical.

## The archive

### What it is

143 listing pages of eight entries, reaching back to 2016 — about **1140**
consultations. The pager publishes its own last page, so the end of the archive
is read in the first request rather than probed by fetching until something
looks empty.

### The fact that shapes everything

**Archive cards carry no year.** A card prints a day and an abbreviated month
and nothing else — `27 dets` on the page from 2016, `23 juuli` on the newest.
An entry's real date is knowable only from its detail page. Therefore:

- `published_date` stays null on an index-only row. Inferring a year across a
  decade from a day and a month would be a guess, and a guessed date feeds the
  matcher's chronology contradictions.
- Hydration cannot be targeted by date before it happens. The *background* pass
  therefore walks newest-first and stops once the window closes; the priority
  pass ignores age entirely, because it selects by which record needs the page.
- An **index-only entry can never be matched.** It has an editorial headline and
  nothing to date, weigh or contradict. A check constraint refuses to call a row
  hydrated when it carries no text, the matcher excludes unhydrated rows from
  the corpus, and the viewer resolver refuses them again.

### Two hydration priorities

The **complete listing index covers the whole archive**, every year. What
hydration decides is only which detail pages are *read*, and it decides that in
two independent priorities.

**Priority A — candidates for records that need a link, at any age.** Every
consultation-eligible record in the current legal snapshot that the current
matcher did not match is shortlisted against the entire index. Those pages are
read first and **their age is irrelevant**.

This matters because consultation eligibility is *status*-based — open, and no
opinion sent — and says nothing about when the consultation ran. Gating
hydration by age would make an eligible record's link depend on how long ago the
Chamber was asked, which is not a rule anyone intended. An older consultation
can therefore supply a link for as long as its legal record remains eligible.

**Priority B — recent background coverage.** Whatever budget remains fills unread
entries inside `KODA_ARCHIVE_HYDRATION_WINDOW_DAYS = 365`, newest first, stopping
after `KODA_ARCHIVE_WINDOW_STOP_AFTER_OLDER = 8` consecutive older entries. It
keeps the recent corpus complete for inspection, for rarity statistics and for
legal records that have not arrived yet. **It never displaces Priority A.**

Order within one run:

1. shortlisted candidates for currently eligible records, any year;
2. previously failed candidate pages, retried;
3. newest unread entries inside the recent window;
4. nothing else.

A page shortlisted by five records is fetched once — the pass walks a
deduplicated set.

### Shortlisting

`MAX_SHORTLIST_PER_RECORD = 12` candidates per record, ranked by how many
discriminating words they share with it, with a floor of
`MIN_SHARED_SIGNIFICANT_TOKENS = 1`. Ties break on the archive's own order, so
two runs over the same inputs shortlist the same pages.

The shortlist cannot use dates: archive listing cards carry no year, so before
hydration there is nothing to filter on. It works from title and summary alone,
which is exactly what an unread entry offers. **A shortlisted page is never
matched from listing metadata** — it must still be hydrated and validated first.

### What `backfill_complete` means

Three things, all true at once:

- the listing index is whole (`index_complete`);
- **every priority candidate for the current eligible population is read or has
  definitively failed**;
- the recent background window is complete.

It does **not** mean all ~1140 detail pages were fetched — full hydration of the
historical archive is not required and is not attempted. It can legitimately
return to false when a new legal snapshot introduces a record whose candidate has
never been read, which is the state correctly reporting that there is work to do.

A run whose priority candidates are still pending does **not** report
`unchanged`, even when the listing itself has not moved: identical rows plus
unfinished work is not "nothing to do".

Progress is exposed as `priority_candidate_count`, `priority_detailed_count`,
`priority_pending_count`, `priority_failed_count`, `recent_detailed_count`,
`recent_pending_count`, `index_complete` and `backfill_complete`.

### Collection modes

| Mode | Listing pages walked | When |
| --- | --- | --- |
| `--full` | every page | initial backfill, and to re-settle presence |
| default | until `KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP = 2` consecutive pages hold only known, unchanged entries | daily |

Both modes build towards the complete index; `--full` re-walks every page in one
run, the default relies on entries already known from previous runs.

A day never archives sixteen consultations, so two pages is ample. An
incremental run carries forward the presence and hydration of everything it did
not visit: **not having looked is not evidence of absence.** Only `--full` may
mark an entry absent, and even then its historical snapshot rows are kept.

`--max-detail-pages N` bounds detail requests per run; the default is
`KODA_ARCHIVE_MAX_DETAIL_PAGES_PER_RUN = 60`. Hydration accumulates across runs
because each snapshot carries forward what earlier ones read, so the backfill is
resumable rather than restarting.

A detail-page failure is **recorded, not raised**: the entry keeps its place in
the index with a short machine-readable code (`http_404`, `unavailable`,
`unparsable`), is excluded from matching, and is retried on a later run. One dead
page among eleven hundred must not throw away a backfill.

### Settings

| Setting | Value |
| --- | --- |
| `KODA_ARCHIVE_URL` | `https://www.koda.ee/et/meie-moju/hetkel-kasil/arhiiv` |
| `KODA_ARCHIVE_SOURCE_SLUG` | `koda-public-archived-topics` |
| `KODA_ARCHIVE_MAX_PAGES` | 400 (observed 143) |
| `KODA_ARCHIVE_MAX_ITEMS` | 5000 |
| `KODA_ARCHIVE_MAX_BYTES` | 4 MiB per response |
| `KODA_ARCHIVE_REQUEST_PAUSE_SECONDS` | 0.5 |
| `KODA_ARCHIVE_HYDRATION_WINDOW_DAYS` | 365 |
| `KODA_ARCHIVE_MAX_DETAIL_PAGES_PER_RUN` | 60 |
| `KODA_ARCHIVE_KNOWN_PAGES_BEFORE_STOP` | 2 |

No `--url` option, no admin field, no viewer-supplied URL. Transport is
`apps.core.public_http.fetch` on the existing Koda.ee allowlist. Every entry link
must be HTTPS on an allowed host, under `/et/meie-moju/hetkel-kasil/`, not the
current listing root, not the archive root, free of userinfo and of a query
string, within the length bound and unique in the snapshot. Both host spellings
normalise to `www.koda.ee` so the two catalogues compare equal strings.

The archive walk is the one long run of requests DashKoda makes to a third
party, so it pauses between them. `robots.txt` places no restriction on this
path and no crawl delay.

## Two matchers, deliberately separate

| | current | archive |
| --- | --- | --- |
| version | `1.1-norm1.1` | `archive-1.0-norm1.1` |
| field size | ~7 | ~170 and growing |
| automatic match | 62.00 | **72.00** |
| plausible floor | 38.00 | **48.00** |
| minimum margin | 12.00 | **18.00** |
| generic-token damping | 0.25 | **0.15** |
| identifier conflict | evidence only | **blocking** |
| corpus for rarity | current entries | **archive entries** |

The thresholds were **not copied**. 62/38/12 was tuned against a seven-document
field and is not evidence about a field of two hundred: the chance that some
entry happens to share vocabulary rises with the size of the field, so the same
score means less. The archive additionally refuses any candidate sharing no
discriminating word with the record at all.

Weights are independently named (`ARCHIVE_WEIGHT_*`) and shifted towards the
signals that survive a large field — rarity coverage and uncommon-token overlap
gain, deadline loses. What the two share is the transparent machinery: the same
normaliser, the same n-gram and rarity primitives, the same evidence vocabulary.
Those are mechanics, not calibration.

**The two never share an idf corpus.** A word rare among seven live
consultations is unremarkable among a decade of them.

## Precedence and overlap

The archive matcher considers a record only when it is consultation-eligible
**and** the current matcher did not match it. Current `ambiguous` and
`unmatched` records are eligible for the fallback; current `matched` records are
not.

A consultation can briefly appear in both catalogues during the transition. The
archive matcher **excludes every canonical URL present in the exact current-topic
snapshot**, so the same page is never re-judged under the archive's different
thresholds and one record can never produce two contradictory verdicts.

## Exact eligibility for an archive link

All seventeen conditions, most of them expressed in the query itself:

1. a current legal snapshot exists;
2. a current current-topic snapshot exists;
3. a current current-topic match snapshot exists;
4. it references the exact current legal and current-topic snapshots;
5. a current archive snapshot exists;
6. a current archive match snapshot exists;
7. it references the exact current legal snapshot;
8. it references the exact current archive snapshot;
9. it references the exact current-topic match snapshot;
10. the displayed item belongs to that legal snapshot;
11. the item is consultation eligible;
12. its current-topic decision is not `matched`;
13. the archive decision is exactly `matched`;
14. the candidate belongs to the referenced archive snapshot;
15. the candidate is hydrated and present;
16. the candidate URL passes defensive validation;
17. the candidate URL is not in the current-topic snapshot.

When any fails, the topic renders as plain text. **A stale archive match is no
match.**

## Viewer resolution

`apps/legal_work/topic_links.py`. Every collection on the page is materialised
first, then:

1. one bounded query resolves current-listing links for the whole displayed set;
2. one bounded query resolves archive links for whatever is left;
3. the combined `legal_item_id → URL` mapping builds every presentation object.

**Two queries per page whatever it draws** — never one per row, never one per
list. That single mapping is what makes a record behave identically in the
deadline strip, all three tables and the overview card.

Nothing distinguishes a current link from an archived one in the interface. The
anchor text is the legal-work topic from the workbook, and viewers never see a
source, a score, a margin, evidence codes, a matcher version, backfill state or
any decision label.

The archive is **not** a fifth global freshness source. The denominator stays
four. Archive health and backfill progress are visible in the read-only admin.

## Failure behaviour

The Õigusloome page always renders.

| What fails | Consequence |
| --- | --- |
| archive collection | previous archive snapshot stays current; links unaffected |
| archive matching | previous archive match snapshot stays current; no partial snapshot |
| current collection or matching | archive fallback also stops, because it defers to a current run that is no longer current |
| the workbook moves to a new snapshot | both matchers' snapshots become stale; plain text until they re-run |

An archive failure cannot affect the legal workbook sync, the current legal
snapshot, current-topic collection or matching, the event programme, the public
feeds, `/oigusloome/`, the overview or the global freshness count. Separate
source, separate advisory lock, separate transaction.

A short plain-text interval between source runs is acceptable. A stale link is
not.

## Commands

```bash
python manage.py sync_legal_archived_topics [--dry-run] [--json] [--full] [--max-detail-pages N]
```

```bash
python manage.py match_legal_archived_topics [--dry-run] [--json]
```

Exit codes `0` / `1` / `3` as elsewhere. Each takes its own advisory lock,
distinct from the other three legal jobs'. JSON carries aggregates only —
counts, snapshot ids, progress flags and the matcher version — never a title,
summary, URL or page text.

The archive match run reports `unchanged` when the legal snapshot, the archive
snapshot, the current-topic match snapshot **and** the matcher version are all
identical to a previous run.

## Admin

Read-only, like everything else here. The archive admin adds what the current
one does not need: **backfill visibility**. Detail status is a column and a
filter, so an operator can see directly that an entry has not been read rather
than inferring it from a missing link. The match list carries the current-topic
decision beside its own, because a reviewer's first question about an archive
match is what the current matcher said.

No add, change, delete, approve, reject, suppress, force-match or manual-URL
action anywhere.

## Intended schedules

| Time (Europe/Tallinn) | Job |
| --- | --- |
| 07:00 | `sync_oigusloome_public` |
| 05:45 | `sync_legal_current_topics` |
| 05:50 | `match_legal_current_topics` |
| 06:00 | `sync_legal_archived_topics` |
| 06:15 | `match_legal_archived_topics` |

Separate wrappers, separate flock files, separate logs. The fifteen-minute gap
before matching gives a daily incremental archive run room to finish.

**The initial full backfill is run by hand before the schedules are enabled**,
and `--full` never goes into the daily job.

## Known limitations

- Thresholds are calibrated on synthetic data and live evaluation; they are
  revisited from what production produces, as a new matcher version.
- Archive cards carry no year, so listing-only shortlisting is weak: the
  prefilter works from title and summary alone, and Koda.ee headlines are
  editorial questions. A record whose archive page shares no discriminating word
  with its listing text is not shortlisted and so is not hydrated by priority;
  the background window may still reach it if it is recent.
- The recent background window is one year, so the *corpus* the archive matcher
  computes rarity over is skewed towards recent consultations even though older
  candidates are hydrated on demand.
- `Meie arvamus`, opinion PDFs and news remain future work and are not modelled.
  A sent record therefore has no resource to link to and stays plain text.
- A consultation that closes and is matched from the archive keeps the same URL,
  so a reader following it reaches an archived page — correct, but it does not
  say "this consultation has closed". That wording belongs to the later resource
  work.
