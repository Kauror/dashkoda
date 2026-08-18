# The integrated metric contract

One row per major figure across the six dashboards and the executive overview
(`Koja töölaud`, `/`). For each: what it counts, from which source, at what
grain, over what time basis, what missing means, when a comparison is offered,
and what the overview does with it. The overview never holds a definition of
its own — every executive column below names the same function its domain
dashboard uses, and `tests/*/test_executive_consistency.py` plus
`tests/dashboard/test_executive_overview.py` pin the equalities.

Two standing rules apply to every row:

- **missing is never zero.** An unmeasured value renders as absent with its
  reason; an explicitly reported zero renders as `0` and stays distinguishable;
- **a comparison is offered only when both sides are honest.** A window
  reaching before its source's coverage, a differently-measured pair, or a
  zero denominator yields *no* figure, never a clipped or partial one.

## Liikmeskond

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Liikmeid kokku (headline on `/`) | Public directory count, newest observation | Koda.ee liikmekataloog (`apps/membership/selectors.py`) | Observation; written only when the count changes | Card unavailable | Newest reading older than 365 days; relative % only with a non-zero baseline | Liikmeskond card headline. **Never** mixed with the internal report |
| Internal member total (headline on `/liikmeskond/`) | Board-report total | Internal report (`internal_selectors.py`) | Monthly report, `observation_date` | Absent with reason | Report-internal only | Not shown on `/` as a total — ratios only |
| Tasunute osakaal | Paid members ÷ report's own total | Internal report | Report date | Absent (0% is a value and renders) | Signal when moved ≥ 2 pp against predecessor ≤ 400 days back | Supporting fact on `/`, whose period line names the report's own date beside the catalogue's; on `/liikmeskond/` it is stated beside the paid count it divides |
| Liikmemaksu laekumine | Reported collection %, else computed from the report's own amounts | Internal report | Report date | Absent | None | Supporting fact |
| Liitunud / välja arvatud YTD | Report's own YTD movement counts | Internal report | Report date | Absent | Same point in the previous year, within **15 days** of the anniversary — a tenth of the tolerance a *stock* gets, because a count that accumulates from 1 January is short of its own year if the baseline is. Refused beyond that: the count prints with no percentage | `Sel aastal`, the fourth cell of the Liikmeskond strip |
| Koosseis (composition) | Aggregate buckets from a hand-imported roster | `MembershipCompositionSnapshot` | Snapshot date (stated, never inferred from a filename) | Page degrades before first import; no fake zeros | Between snapshots only | Not on `/` |
| Liikmete nimekiri (kirjete arv) | Rows in one hand-imported roster export. **Not a membership total** | `MemberRegisterSnapshot` (`register_selectors.py`) | Snapshot date (stated, never inferred from a filename) | Focus not offered before the first import | None — two exports are two readings, not a series | Not on `/` |
| Kataloogis avaldatud kirjed | Registration codes the public directory publishes now | `MemberDirectoryEntry` (`directory_sync.py`) | Working register; `first_seen_at` / `last_seen_at` per code | A code that stops appearing is marked unpublished, never deleted | None | Not on `/` |
| Nimekirja ja kataloogi võrdlus | Set comparison of registration codes: matched, roster-only, directory-only | Both of the two rows above | Roster snapshot date × directory last check | Absent unless **both** sources exist; roster rows without a code are counted out and disclosed | n/a | Not on `/` |

The public directory and the internal report count different things and are
never merged, subtracted, averaged or continued into each other (AGENTS.md).

The register comparison is an **identity** comparison, not an arithmetic one.
It states three counts and two lists, each labelled with its own source and
date, and produces no corrected or combined membership number — a difference
means one source is fresher than the other or that a profile is unpublished,
which is not an error in either and licenses no correction to any total. The
roster row count is a fourth figure again and belongs beside none of the three
above.

## Õigusloome

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Arvamusi saadetud tänavu | Sent opinions, 1 Jan → reporting date | Workbook snapshot (`analytics.sent_year_on_year`) | Row; `sent_date`; cutoff = workbook reporting date | Absent | Same calendar day a year earlier; 29 Feb → 28 Feb; zero baseline → count, no % | Supporting fact, with the previous year's count beside it. **Not the headline** — output is a record of work done, not the current state of anything |
| Teemasid töös | Open matters now — a stock | Workbook snapshot | Reporting date | Card unavailable | None (a stock has no YTD pair) | **Õigusloome card headline** — `X teemat töös`, the figure that changes when somebody acts |
| Tänavusi teemasid | Register's annual membership = `source_year` sheet | Workbook snapshot (`topics_year_on_year`) | Sheet-year; months use `received_date` and the two deliberately disagree for a minority of rows | Absent | Year on year, same-day | Supporting fact |
| Tähtaegu 7 päeva jooksul / möödas | `deadline_pressure`: open + due within 7, and open + deadline passed (an answered matter still open is *not* late) | Workbook snapshot | Reporting date | Absent | None | `due_within_7` is a card fact; `overdue_pending` is **only** a critical signal in `Tähelepanu`, never a quiet fourth fact |
| Response window | Days received → sent, median and mean shown separately | Workbook snapshot | Row pair of dates | Rows without both dates excluded, count disclosed | Per year | Not on `/` |
| Member feedback | `feedback_member_count` and `feedback_requested_member_count` are **independent populations**; no response rate exists or may be created | Workbook snapshot | Row | NULL ≠ 0 throughout; measured zeros are real | None | Not on `/` |

## Sündmused

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Sündmusi tänavu | Canonical programme events started 1 Jan → today. **One row = one event, never an occurrence or a session** | Programme workbook (`analytics.count_year_to_date`) | Event; application day (`timezone.localdate`) | Absent | Same span a year earlier; zero baseline → count | Supporting fact, with the previous year's count beside it |
| Algab 30 päeva jooksul | Events starting inside the near-term horizon | Programme workbook | Event start date | Card unavailable | None | **Sündmused card headline** + timeline lane. `NEAR_TERM_DAYS` and the timeline's `HORIZON_DAYS` are the same thirty days, so the headline and the list below it cannot describe different sets |
| Planeerimisvaru | Stored source figure `planning_lead_days`, never recomputed | Programme workbook | Event | Old rows carry NULL until reimport; counted as unknown, not zero | Per year, negative leads excluded and disclosed | Not on `/` |
| Hinnad / price_status | Stored source values; unknown status renders as unknown, `0 €` is a real price | Programme workbook | Event | NULL / `""` = unknown | None | Not on `/` |
| Registreerimised | Commerce `event_registration` units — **gated off**: production Commerce has no such products and `member_semantics_verified` is false | Commerce bridge (`commerce.py`) | Order-line day | Whole surface withheld | n/a | **Absent by design** — Kaasamine reads no Commerce, so no row is counted in two pillars |

## Koduleht

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Seansid (`külastused`) | Sum of daily sessions over the measured window | GA4 daily rows | Day; window anchored to newest **measured** day, never today | Card unavailable | Preceding equal window, only when `build_comparison` accepts the coverage pair; refusal is named in `Andmete seis` | `Koduleht ja uudised` card headline, same summary function. Shown once on the page: the audience strip omits the website slot |
| Lehevaatamised (`vaatamised`) | Sum of daily page views | GA4 daily | Day | Absent | As above | Denominator for the news share. **Never spelled `külastused`** — a session and a page view are different measures |
| Kaasatuse määr | Engaged sessions ÷ sessions | GA4 daily | Window | Absent | Stated as a level, not a movement | Supporting fact |
| Aktiivsed kasutajad | Distinct people per day — **never summed across days or pages** | GA4 daily | Day only | Absent | None across windows | Not on `/` |
| Sisu/kanali detail | Page- and channel-level figures, gated on their own detail coverage — a site-wide figure can exist while a detail comparison is withheld | GA4 page/channel rows | Day × path / channel | Withheld independently of the site figure | Own coverage gates | Top ordinary page in the interest panel |

## Uudised

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Avaldatud uudiseid | Articles with `published_at` in the window — a **publication cohort**, not a traffic window | News catalogue | Article; publication date | `None` without a catalogue (a quiet fortnight is not an unconnected feed) | Preceding equal window of the *catalogue* (correctly ungated by GA4 coverage) | Supporting fact |
| Uudiste vaatamised | GA4 page views of catalogue articles in the measurement window | GA4 × catalogue join | Day × path; the website's own window | Absent | `previous_traffic_within` — refused when the previous window reaches before GA4 collection began; the news page and the executive share this one rule | Fact + `news-views` signal |
| Uudiste osa kodulehe vaatamistest | News views ÷ site views, **same days, same unit** (page views over page views) | GA4 | Window | Absent when denominator missing | n/a | Stated as a share, **never** added to site views |
| Esimene nädal / kuu | An article's own first 7/30 days, only when fully elapsed inside coverage | GA4 × catalogue | Article-anchored window | Unelapsed or uncovered → no figure | Against cohort median | Not on `/` |

## Otsepostitused

The Smaily material was the fifth focus of Uudised and is its own section under
Koduleht since the navigation restructure. **The definitions below did not change
with the address** — same selectors, same windows, same weighting.

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Uudiskirja avamis-/klikimäär | Weighted totals over the last 12 sends: opens ÷ delivered, clicks ÷ delivered; click-to-open separately | Smaily aggregates (`mailings_executive.get_mailings_executive`) | Send; slice over sends, not dates (cadence is irregular) | Card unavailable | Previous 12-send block, stated in **percentage points** | **Otsepostitused card headline** is e-Teataja's open rate, with its click rate beside it. **No audience figure anywhere on the card** (list overlap is unmeasured) |
| Saadetud uudiskirju | Completed sends whose `completed_at` falls in the last 30 days — a count of **letters**, not of recipients. One issue posted to two lists is two sends, which is how the Chamber posts it | Smaily campaigns (`count_sends_between`) | Send; two equal 30-day spans | Absent, never `0` — a month nobody collected is not a month nobody sent | Preceding equal span, in letters | Card fact: how much went out, which the rates cannot say |
| Uudiskirjade nimekirjade suurus | Each list's current size, reported **on its own and never summed** — the overlap between the three is unmeasured | Smaily segments (`sync_smaily`) | Day; latest reading | `Sisestamata`, never zero | Previous reading | `Auditooriumid` on `/`, one row per list, sorted among the social counts by size and added to nothing |

## E-pood

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Soetatud ühikud | Completed-order units (Commerce order `state`, never `field_order_completed`) | Commerce package (`ShopDailyFact`) | Day × product; period anchored to **coverage end**, never today | Card unavailable | `derive_period_pair`: preceding equal window, refused when it reaches before coverage start — the page and the executive share this one rule | E-pood card headline over `NON_EVENT_TYPES` |
| Tellitud väärtus (KM-ta) | Order-time value net of VAT — **not revenue, not cash** | Commerce package | Day × product | Absent | As above | Supporting fact |
| Tellimused (distinct) | Distinct-order counts, shown **only** where the summary grain supports the active filters; otherwise `Tellimusridu` | `ShopDailySummary` (schema 2.0) | Day × product-type | Falls back to order lines, labelled | As above | Not on `/` |
| Tasuta osakaal | Free units ÷ classified units (unclassified excluded from the denominator, disclosed) | Commerce package | Period | `None` when the package carries no classification (1.0) | Previous period note | Supporting fact |
| Soetusi / 100 vaatamist | Units ÷ acquisition-page views × 100, GA4 window clamped by **Commerce coverage end** as well as GA4's own span | Commerce × GA4 | Product; overlap window | No page → no rate | n/a | Not on `/` |

## Executive overview (`/`)

**Six domain cards, one per dashboard** — Liikmeskond, Õigusloome, Sündmused,
Koduleht ja uudised, Otsepostitused, E-pood — in the sidebar's own order. That
is the whole set rather than a hand-picked subset: the strip has been four, then
five, then two, and each of those quietly decided that some of the Chamber's
activities did not need reporting. Nothing sums across the six, no composite
score exists, and there is no red/amber/green verdict on a domain: membership,
opinions, events, sessions, newsletter rates and Commerce units are not
commensurable.

The cards are disjoint by construction. The Sündmused card reads the programme
workbook and no Commerce; the E-pood card reads Commerce minus
`EVENT_REGISTRATION`; `Koduleht ja uudised` states news reading as a **share**
of site reading rather than adding a subset to its superset; the Otsepostitused
card carries rates and no audience. The Digiteenused pillar card was removed at
the board's request on 2026-08-15 and `Huvikaitse` and `Kaasamine` on 2026-08-16;
all three domains are back under their own dashboard names since 2026-08-17, and
the no-double-counting rule survived every one of those changes because it
governs what a card may count rather than how many cards there are.

Each card is a label, one figure with its unit, a comparison where the domain
supports one, two to four supporting facts, one period or as-of line, and a
drill-through. It carries no meaning sentence, no sparkline and no per-figure
source caption — `ExecutiveMetric` still holds `period`, `source` and `as_of` in
data, which is what `Andmete seis` and the domain pages are built from.

`Tähelepanu` is the page's one genuinely cross-domain section. Signals arrive
decided — wording, priority, threshold — from the domains; the page collects,
dedupes, sorts and limits. An evidence sentence is optional per signal since the
board struck the ones that restated their headlines. **With nothing flagged the
section is not rendered at all**, because a header and a line of reassurance
teaches a reader to skim the one section that must never be skimmed.

`Järgmised 30 päeva` holds the only two dated lanes (legal deadlines, scheduled
events) and is named for the horizon it actually has. An event that started
before today carries **no date** in it and says `kestev`: a year-long programme
that opened on 1 January is not a thing happening in the next thirty days.

`Auditooriumid` sits beside the timeline and is the quietest thing on the page:
one row per audience, largest first, newsletter list sizes and hand-entered
social counts in one list and added to nothing. It omits the website, because
sessions are a card headline above — and a session is a visit rather than an
audience.

`Praegu enim huvi` was a fourth section until 2026-08-18. Which single page,
article and product happened to lead is a browsing question, and the three
domain cards already carry the volumes those leaders are a slice of. The three
domain fields behind it went with it, and with them three bounded queries per
render.

The footer prints `Uuendatud <date> kell <time>` — the last moment any source
finished publishing successfully, and **not** a claim that every figure above is
current as of then. It is absent before anything has ever been imported.

Data quality lives at `/haldus/`. The overview carries one quiet `Andmete kohta`
link and no source grid, no schema version, no import diagnostic and no coverage
table. `Andmete seis` speaks per business source in that source's own
vocabulary; a stale-after-failure feed keeps its last-good figures and says so —
and it is the only place the overview's data names a source at all.
