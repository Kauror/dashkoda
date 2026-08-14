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
| Liikmeid kokku (headline on `/`) | Public directory count, newest observation | Koda.ee liikmekataloog (`apps/membership/selectors.py`) | Observation; written only when the count changes | Pillar unavailable | Newest reading older than 365 days; relative % only with a non-zero baseline | Pillar headline. **Never** mixed with the internal report |
| Internal member total (headline on `/liikmeskond/`) | Board-report total | Internal report (`internal_selectors.py`) | Monthly report, `observation_date` | Absent with reason | Report-internal only | Not shown on `/` as a total — ratios only |
| Tasunute osakaal | Paid members ÷ report's own total | Internal report | Report date | Absent (0% is a value and renders) | Signal when moved ≥ 2 pp against predecessor ≤ 400 days back | Supporting fact, source-labelled |
| Liikmemaksu laekumine | Reported collection %, else computed from the report's own amounts | Internal report | Report date | Absent | None | Supporting fact |
| Liitunud / välja arvatud YTD | Report's own YTD movement counts | Internal report | Report date | Absent | None | Supporting fact |
| Koosseis (composition) | Aggregate buckets from a hand-imported roster | `MembershipCompositionSnapshot` | Snapshot date (stated, never inferred from a filename) | Page degrades before first import; no fake zeros | Between snapshots only | Not on `/` |

The public directory and the internal report count different things and are
never merged, subtracted, averaged or continued into each other (AGENTS.md).

## Õigusloome

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Arvamusi saadetud tänavu | Sent opinions, 1 Jan → reporting date | Workbook snapshot (`analytics.sent_year_on_year`) | Row; `sent_date`; cutoff = workbook reporting date | Pillar unavailable | Same calendar day a year earlier; 29 Feb → 28 Feb; zero baseline → count, no % | Pillar headline, same function |
| Teemasid töös | Open matters now — a stock | Workbook snapshot | Reporting date | Absent | None (a stock has no YTD pair) | Supporting fact |
| Tänavusi teemasid | Register's annual membership = `source_year` sheet | Workbook snapshot (`topics_year_on_year`) | Sheet-year; months use `received_date` and the two deliberately disagree for a minority of rows | Absent | Year on year, same-day | Supporting fact |
| Tähtaegu 7 päeva jooksul / möödas | `deadline_pressure`: open + due within 7, and open + deadline passed (an answered matter still open is *not* late) | Workbook snapshot | Reporting date | Absent | None | Facts + the critical/attention signals |
| Response window | Days received → sent, median and mean shown separately | Workbook snapshot | Row pair of dates | Rows without both dates excluded, count disclosed | Per year | Not on `/` |
| Member feedback | `feedback_member_count` and `feedback_requested_member_count` are **independent populations**; no response rate exists or may be created | Workbook snapshot | Row | NULL ≠ 0 throughout; measured zeros are real | None | Not on `/` |

## Sündmused

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Sündmusi tänavu | Canonical programme events started 1 Jan → today. **One row = one event, never an occurrence or a session** | Programme workbook (`analytics.count_year_to_date`) | Event; application day (`timezone.localdate`) | Pillar unavailable | Same span a year earlier; zero baseline → count | Pillar headline, same function |
| Algab 30 päeva jooksul | Events starting inside the near-term horizon | Programme workbook | Event start date | Absent | None | Fact + timeline lane (one bounded read serves both) |
| Planeerimisvaru | Stored source figure `planning_lead_days`, never recomputed | Programme workbook | Event | Old rows carry NULL until reimport; counted as unknown, not zero | Per year, negative leads excluded and disclosed | Not on `/` |
| Hinnad / price_status | Stored source values; unknown status renders as unknown, `0 €` is a real price | Programme workbook | Event | NULL / `""` = unknown | None | Not on `/` |
| Registreerimised | Commerce `event_registration` units — **gated off**: production Commerce has no such products and `member_semantics_verified` is false | Commerce bridge (`commerce.py`) | Order-line day | Whole surface withheld | n/a | **Absent by design** — Kaasamine reads no Commerce, so no row is counted in two pillars |

## Koduleht

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Seansid | Sum of daily sessions over the measured window | GA4 daily rows | Day; window anchored to newest **measured** day, never today | Pillar unavailable | Preceding equal window, only when `build_comparison` accepts the coverage pair; refusal is named in `Andmete seis` | Pillar headline, same summary function |
| Lehevaatamised | Sum of daily page views | GA4 daily | Day | Absent | As above | Denominator for the news share |
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
| Uudiskirja avamis-/klikimäär | Weighted totals over the last 12 sends: opens ÷ delivered, clicks ÷ delivered; click-to-open separately | Smaily aggregates | Send; slice over sends, not dates (cadence is irregular) | Absent | Previous 12-send block | e-Teataja open rate as a fact; **no audience totals anywhere** (list overlap is unmeasured) |

## E-pood

| Metric | Definition | Source | Grain / time basis | Missing | Comparison | Executive use |
| --- | --- | --- | --- | --- | --- | --- |
| Soetatud ühikud | Completed-order units (Commerce order `state`, never `field_order_completed`) | Commerce package (`ShopDailyFact`) | Day × product; period anchored to **coverage end**, never today | Pillar unavailable | `derive_period_pair`: preceding equal window, refused when it reaches before coverage start — the page and the executive share this one rule | Pillar headline over `NON_EVENT_TYPES` |
| Tellitud väärtus (KM-ta) | Order-time value net of VAT — **not revenue, not cash** | Commerce package | Day × product | Absent | As above | Supporting fact |
| Tellimused (distinct) | Distinct-order counts, shown **only** where the summary grain supports the active filters; otherwise `Tellimusridu` | `ShopDailySummary` (schema 2.0) | Day × product-type | Falls back to order lines, labelled | As above | Not on `/` |
| Tasuta osakaal | Free units ÷ classified units (unclassified excluded from the denominator, disclosed) | Commerce package | Period | `None` when the package carries no classification (1.0) | Previous period note | Supporting fact |
| Soetusi / 100 vaatamist | Units ÷ acquisition-page views × 100, GA4 window clamped by **Commerce coverage end** as well as GA4's own span | Commerce × GA4 | Product; overlap window | No page → no rate | n/a | Not on `/` |

## Executive overview (`/`)

Five pillars, disjoint by construction: Kaasamine reads the programme workbook
and no Commerce; Digiteenused reads Commerce minus `EVENT_REGISTRATION`; news
reading is stated as a share of site reading, never added. Nothing sums across
pillars and no composite score exists. Signals arrive decided (wording,
priority, threshold) from the domains; the page collects, dedupes, sorts and
limits. The timeline holds the only two dated lanes (legal deadlines, scheduled
events). `Andmete seis` speaks per business source in that source's own
vocabulary; a stale-after-failure feed keeps its last-good figures and says so.
