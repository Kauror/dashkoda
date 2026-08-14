# Õigusloome intelligence dashboard

The analytical contract for `/oigusloome/`. Every figure the page draws is
defined here once: what it measures, which field dates it, which rows are
eligible, and what it must not be read as.

`apps/legal_work/analytics.py` implements these definitions and
`tests/legal_work/test_analytics.py` pins them. Where the two disagree, the
tests are right and this document is stale.

## The four rules everything obeys

1. **The cutoff is the snapshot's `reporting_date`, never today.** The workbook
   is regenerated each morning and read all day. Counting "this year" against
   the wall clock would move a published figure between two page loads and
   would claim a period the data does not cover.
2. **A year-on-year comparison clamps both years to the same calendar day.**
   The current year is incomplete; comparing it with a finished one measures the
   calendar rather than the work.
3. **Missing is not zero.** An absent date lands in no bucket and an absent
   count stays `None`. A row is dropped from a statistic it cannot support and
   reported as missing coverage instead of being repaired.
4. **Snapshots are revisions, not annual populations.** Historical totals come
   from the current snapshot grouped by the rows' own event dates. Adding
   snapshots together would count the same legislative matter once per revision.

## Two different year questions

The register answers "which year is this matter's work" and "when did this
happen" with different fields, and they genuinely disagree for a minority of
rows — a matter received in December sits on the following year's sheet.

| Question | Field | Used by |
| --- | --- | --- |
| Which year's file is this? | `source_year` | `{aasta}. aasta teemad`, annual topic series, feedback coverage |
| When did it arrive? | `received_date` | monthly new topics, arrivals year-on-year, response-window cohort, active age |
| When did the opinion go out? | `sent_date` | monthly sent, annual sent, sent year-to-date, year-on-year |

`source_year` is the register's own annual grouping: the operational workbook is
one sheet per year and `source_row` is the row number inside that sheet, so the
field states which year's work a matter belongs to. A month, by contrast, is a
question about an event, so it is always answered by a date.

**These do not reconcile, and are not forced to.** The monthly chart reports how
many of the year's rows carry no arrival date rather than spreading them across
months or assigning them to January.

## Metric definitions

| Metric | Question | Population | Eligibility | Comparison |
| --- | --- | --- | --- | --- |
| `{aasta}. aasta teemad` | How big is this year's file? | `source_year = year` | all rows | **none** — a stock cannot be clamped to a comparable date |
| `{aasta}. aastal arvamusi välja läinud` | How many opinions went out? | `sent_status = SENT` | `sent_date` year = year **and** `sent_date <= reporting_date` | same-date previous year |
| `Arvamuste muutus` | Is output up or down? | as above | as above | 1 Jan → reporting date, both years |
| `Hetkel töös` | How much is in progress? | `is_open = True` | all open rows | none |
| `Aktiivsed teemad hetkeseisu kaupa` | Where is open work in the process? | `is_open = True`, grouped by `stage_key` | all open rows, blanks as `Määramata` | none |
| `Uued teemad kuude lõikes` | How much arrives? | `received_date` month | dated rows only | previous year, same months |
| `Välja saadetud arvamused kuude lõikes` | How much goes out? | `sent_status = SENT`, `sent_date` month | current year clamped to reporting date | previous year, same months |
| `Välja saadetud arvamused aastate lõikes` | Long-term output | `sent_status = SENT`, `sent_date` year | `sent_date <= reporting_date` | current year marked `YTD` |
| `Arvamuse esitamiseks antud aeg` | How long is a consultation window? | `deadline_date - received_date`, in days | both dates present **and** `deadline >= received` | median and mean per cohort year |
| `Aktiivsete teemade vanus` | How long has work been open? | `reporting_date - received_date` | open, dated, `received_date <= reporting_date` | none |
| Deadline bands | Where is pressure building? | open rows with a future deadline | mutually exclusive bands | none |
| `Arvamus saadetud hiljemalt tähtajaks` | Did sends carry a date on or before the deadline? | sent rows **with** a deadline | nothing else can be judged | none |

### Year-on-year, precisely

Current: 1 January of the reporting year → `reporting_date`.
Previous: 1 January of the prior year → the same month and day.

29 February has no counterpart in an ordinary year, so the previous-year cutoff
is pinned to **28 February** — the last day the previous year actually shares
with this one. Sliding to 1 March would count a day the current year has not
reached, and `date.replace()` would raise.

A zero baseline yields **no percentage**. Change from nothing is not infinity
and not a hundred per cent; the absolute delta is shown and the ratio is a dash.

### What is deliberately not a metric

- **No `sent / received` ratio as a success rate.** Some matters intentionally
  require no written opinion, so the ratio would penalise correct judgement.
- **No forecast.** Year-end projections would depend on seasonality and policy
  cycles the register does not describe.
- **No composite score.** There is no business definition for one.
- **No staff or performance measure.** Deadlines are negotiated, source dates
  are revised afterwards, and some opinions are deliberately submitted late.

## Member feedback (schema 1.2)

The source stores two counts per matter and **no identities**:

| Field | Means |
| --- | --- |
| `feedback_member_count` | how many members gave feedback on this matter |
| `feedback_requested_member_count` | how many members were asked directly |

`NULL` means the question was not tracked for that row. `0` means it was
tracked and nobody responded. The two are kept apart everywhere, including in
the tables, where an untracked row is an em dash and a measured zero is `0`.

### No response rate is calculated

`feedback_member_count` is **not** a subset of
`feedback_requested_member_count`. Members also answer through newsletters and
general calls, and the register contains matters where more members answered
than were asked directly — a ratio would exceed 100% on real rows and would not
mean what its name claimed.

The two counts are shown separately. Sums are labelled as counts of feedback
*instances across topics*, never as unique members: one member answering on nine
matters is nine here and one in reality.

Coverage is reported per year, because tracking began partway through the
register's history and is still partial. A year with few recorded responses may
simply be a year that was barely measured, so years before tracking are never
drawn as zero.

## Thresholds, and why they are what they are

| Threshold | Value | Why |
| --- | --- | --- |
| Short consultation window | `<= 14 days` | The register's median sits at 15–16 days across every profiled year, so this separates the shorter half from the longer one. **Not** a legal standard, and the interface never calls it too short. |
| Minimum comparison sample | `10 eligible matters` | A median from three matters is not comparable with one from ninety. Below it the figure is withheld rather than drawn small. |
| Response-window bands | 0–7, 8–14, 15–21, 22–30, 31+ | The first three split the bulk of the observed distribution; the last two carry a tail that reaches beyond 90 days. |
| Age bands | <30, 30–90, 91–180, 181–365, 1–2y, 2y+ | Fine at the short end where consultations live, coarse at the long end where European files legitimately sit for years. |
| Deadline bands | 0–3, 4–7, 8–14, 15–21, later | Mutually exclusive. Cumulative bands would count the same matter in every wider band. |
| Top categories | 10 | Beyond that a ranking is a list. |

## Free-text categories are never merged

`stage`, `recipient` and `act_type` are free text in the source, and the
register spans enough years that all three have drifted:

- the recipient column's *meaning* changed — it named who sent the draft in the
  early years and who receives the opinion later;
- one ministry was renamed, and another was reorganised with a changed remit;
- act types carry case and plural variants.

Exact source values are kept. No automatic rule can tell a spelling variant from
a machinery-of-government change, so merging them would invent a continuity the
register does not record. Any future mapping must be explicit, versioned and
reviewed.

The stage vocabulary is likewise not hard-coded: grouping is on the source's own
`stage_key`, the label drawn is the commonest `stage` spelling inside that key,
and a stage nobody has seen before appears without a code change. A blank stage
is shown as `Määramata` and stays inside the total, so the bars reconcile
exactly with the active count.

## Data-quality behaviour

Anomalies are **reported, never repaired**. Every one stays imported exactly as
the workbook stated it; what changes is which statistics draw it.

| Situation | Behaviour |
| --- | --- |
| `received_date` missing | in no month; counted and reported beside the chart |
| `received_date` after the reporting date | excluded from age statistics; counted. Never a negative age |
| `deadline_date` before `received_date` | excluded from window statistics; counted. Never an absolute value, never zero |
| `deadline_date` missing | outside the deadline population entirely |
| `sent_date` after the reporting date | outside every year-to-date figure; still in the register |
| feedback count `NULL` | untracked; not a zero, and not in any denominator |
| Deadline passed, opinion pending | `Tähtaeg möödas, arvamus ootel` — outstanding work |
| Deadline passed, opinion already sent, matter open | reported separately and **not** called overdue |

## Rendering

The page reads PostgreSQL and nothing else. Charts are prepared server-side in
`apps/legal_work/charts.py` and emitted through the shared figure component, so
every chart keeps a data table in the document for readers who never run the
module. The payload rides in a non-executable `application/json` block, which is
what keeps `script-src` at `'self'` with no inline script and no `unsafe-eval`.
The chart bundle loads only on a focus that actually draws something.

## What this dashboard cannot tell you

These are evidence limitations, not missing interface:

- **who** did the work, or how much any person handled;
- **how many distinct members** participate — only per-topic counts exist;
- **a true response rate**, for the reason given above;
- **whether Koda's position was accepted**, or any policy outcome;
- **why** workload changed. The dashboard supplies the measurement; the causal
  and political reading stays human.
