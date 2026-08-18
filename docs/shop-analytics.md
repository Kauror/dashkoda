# E-pood analytics

What DashKoda knows about the Koda.ee web shop, where it comes from, and the
several things it deliberately refuses to claim.

## What this is

Koda.ee runs Drupal 11 with Commerce 3. Its shop sells contract templates,
event registrations and a few physical products, and it has kept order history
since October 2020. `apps/shop` holds an aggregated, personal-data-free view of
that history and joins it to the GA4 page traffic `apps/visibility` already
collects.

**The current source is a manual export and there is no collector.** That is a
statement of fact rather than a limitation being apologised for: the models,
the selectors and the pages are shaped so an automated read-only collector can
replace the manual package later without any of them changing.

## The data model

| Model | One row is | Key |
| --- | --- | --- |
| `ShopProduct` | one Commerce product's identity | `source_product_id` |
| `ShopProductSnapshot` | catalogue metadata observed on one day | one current per (product, day) |
| `ShopProductPage` | one public path in one role | one current per (product, role) |
| `ShopDailyFact` | one day × product × member status × payment class | one current per cell |
| `ShopDailySummary` | how many **distinct** orders one day carried, per product type | one current per (day, type) |
| `ShopSourceState` | what the dataset covers and which semantics are trusted | one current |

Published rows are immutable. A correction inserts a **new current row** naming
the one it supersedes, and the replaced row keeps its figures — the same shape
`Ga4DailySnapshot` uses, including the partial unique index that guarantees one
current row per natural key.

Revisioning per row rather than per import is what lets today's full manual
export and a later incremental collector publish into the same tables.

### Product identity

**The Commerce product ID, and nothing else.** Titles are display metadata: a
renamed product is the same product, and the live catalogue contains both
`Tähtajaline tööleping` and `Tähtajaline tööleping renditöötajaga`, which title
matching would confuse. `ShopProduct` therefore carries no title, no price and
no category; all three belong to a dated snapshot.

The product route is `/epood/toode/<commerce id>/` for the same reason.

### Why catalogue metadata is dated

Drupal retained no price history. Today's list price is a *reading taken today*,
not a fact about 2021, and modelling it as a timeless attribute would let a 2021
purchase be valued at a 2026 price. Snapshots carry `observed_on` so that is
structurally impossible rather than merely discouraged.

## Source semantics

### Completed means the Commerce state

The authoritative sales state is the **Drupal Commerce order `state`**.

Koda.ee also carries a custom `field_order_completed` boolean. It is not a sales
status: a read-only audit on 2026-08-11 found 12 618 orders flagged
"Lõpetamata" whose Commerce state was `Completed`. Using that flag would
understate sales roughly fourfold.

The importer accepts `commerce_state = completed` and refuses every other value,
and the manifest must declare `order_state_filter = completed`. Both rules have
tests.

### Ordered value, not revenue

`ordered_value_net` is **`sum(unit_price_net × quantity)` over completed orders,
excluding VAT**. The interface calls it `Tellitud väärtus (KM-ta)`.

It is not revenue and must never be labelled `Tulu` or `Laekunud tulu`. Koda.ee
records no payment receipt and has no refund or cancellation concept at all;
roughly 77% of orders are invoices the website marks as sent and never as paid.
Whether an invoice was paid is the accounting system's fact, and this
application does not have it.

| Fact | Owner |
| --- | --- |
| page views | GA4 |
| orders, units, unit price paid, ordered value | Koda.ee Commerce |
| recognised revenue | the Chamber's accounting system — **not connected** |
| membership counts | the membership sources, never shop purchases |

### Order lines, not orders

`ShopDailyFact.order_count` counts orders **in its own cell** — orders
containing that product, on that day, under that member status and payment
class. That makes it additive across days for one product and **not** additive
across products: an order carrying three templates contributes one to each of
three cells.

So the two surfaces name it differently, and the difference is not cosmetic:

| Surface | Label | Why |
| --- | --- | --- |
| `/epood/` overview | **Tellimused** | the headline is unfiltered since 2026-08-18, which always stays inside the summary's day × type grain |
| `/epood/toode/<id>/` | **Tellimused** | the sum spans one product's cells, so each order appears once |

Before 2026-08-18 the overview's own type, category, member and search filters
narrowed the headline too, and a narrowing past the summary's grain fell back
to `Tellimusridu`. The headline no longer takes any of those filters — only
Tooted's table does, and it names no order figure at all — so that fallback is
now unreachable from the overview and is exercised only through
`distinct_orders_supported()` directly, in `tests/shop/test_shop_intelligence.py`,
and through `Lepingupõhjad`'s own document-only order count, which stays inside
the grain for the same reason a bare product-type filter always did.

On the published dataset that is **5 551 order lines against 4 009 distinct
orders** — calling the overview figure "orders" overstates it by 38.5%. Both
figures were measured against the live database on 2026-08-14; the earlier
estimates in this file and in `models.py` predated `ShopDailySummary`, so the
order side of them was never counted.

### Where the distinct count comes from

`ShopDailyFact` cannot answer it, so schema 2.0 packages carry
`ShopDailySummary`: the count is computed at import, where the order
identifiers still exist, and only the total survives. No order number, order ID
or customer field is stored anywhere.

Its grain is **day × product type**, plus a blank-type row carrying the true
total across every type. Both are needed because an order containing a document
and a physical product belongs to two type rows but is one order — so the type
rows are never added together.

There is deliberately **no category, member or product dimension**. One order
routinely spans categories, so a per-category distinct count could not be summed
into anything.

This gives the interface a rule it must follow rather than a number it may
always use:

| Selection | Order figure |
| --- | --- |
| all products | `Tellimused` — the blank-type summary row |
| one product type | `Tellimused` — that type's summary row |
| a category, member status or search | **`Tellimusridu`** — order lines, relabelled |
| one product, on its own page | `Tellimused` — the sum runs over one product's cells |

`selectors.distinct_orders_supported()` decides this before the query runs, and
the label follows the metric. Showing the type-wide count beside one category's
units and value would put two populations in one row with nothing on screen
saying so, and `tests/shop/test_shop_order_semantics.py` holds the rule.

### Acquisitions, not downloads

A completed document order is a reasonable proxy for a template being acquired,
and the interface says `Ostetud`. It is **not** a verified file download: no
download tracking exists anywhere in Koda.ee, and `file_download` is not among
the events the GA4 property records.

### Member status is gated

Commerce records a member indicator through the customer profile. Whether it
describes membership **at the moment of the transaction** — rather than the
customer's standing today — has not been established, and the difference decides
whether a historical split is meaningful.

So the dimension is stored (`member`, `non_member`, `unknown`), the selectors
work, and the interface withholds the whole thing until
`ShopSourceState.member_semantics_verified` is true. `unknown` is a real value
and never collapses into either of the others.

Membership is never inferred from a company name or from the current member
directory, and this dataset is never joined to that directory to reconstruct
history.

### Public listing is gated too

The audit found 273 published document products and only 144 of them listed in
the public shop, and nobody has established why. `published` and
`publicly_listed` are therefore two fields, `publicly_listed` may be null, and
no "currently in the shop" population is presented until
`public_listing_semantics_verified` is true.

## Product types

Covered: `document`, `event_registration`, `physical_product`.

Deliberately absent: `membership` and `default`. Commerce processes membership
purchases as orders, but membership belongs to the membership domain. The
importer refuses membership rows rather than dropping them, so a package built
to a different scope fails loudly.

## The contract-template special case

Every contract template has **two** public pages:

```text
/et/tooriistad/<slug>            informational page, with a "TELLI LEPING" button
        │
        ▼
/et/pood/lepingute-naidised/<category>/<slug>     the product page
        │
        ▼
cart → checkout → completed order
```

Both accumulate traffic. `ShopProductPage.page_role` keeps them apart
(`information`, `product`, `event`), and:

- the two view counts are **never added**. A visitor commonly sees both, so
  their sum is not reach;
- the acquisition rate uses the **product page**, because that is the page
  carrying the buy action;
- the information → product step is shown as counts of page views and
  acquisitions, never as a user-level funnel. Nothing deduplicates a person
  across two pages and the interface does not pretend otherwise.

The relationship comes only from explicit `product_paths.csv` rows. It is never
inferred from a title or from slug similarity.

## The two windows

A shop question has two date ranges and conflating them produces the most
confidently wrong number available here.

```text
commerce_start = max(selected_start, shop coverage_start)
commerce_end   = min(selected_end,   shop coverage_end)

web_start = max(commerce_start, ga4.earliest)
web_end   = min(commerce_end,   ga4.latest)      ← clamped by BOTH
```

The upper clamp is the consequential one. GA4 keeps collecting after a manual
Commerce export stops. Without it, a September period against an August export
divides September traffic by "no orders" and reads as a product that suddenly
stopped selling. Commerce absence after `source_as_of` means **not imported**,
never zero.

**The conversion numerator reads the web window too.** Six years of acquisitions
over three years of page views is not a rate of anything.

Whenever the web window is narrower than the Commerce one — the ordinary case,
Commerce history beginning in 2020 and GA4's in 2023 — the page states the
interval the web figures actually cover.

### Which page the rate divides by

`apps/shop/web_effectiveness.py` owns this decision and nothing else may make
it. Before it existed every surface reached for `PageRole.PRODUCT`, which gave
event registrations no rate at all — an event product's only public address is
its event page.

| Product family | Acquisition page | Why |
| --- | --- | --- |
| `document` | `PRODUCT` | `/et/pood/…` carries the buy action; `/et/tooriistad/…` only explains |
| `event_registration` | `EVENT` | the public event page *is* the registration page |
| `physical_product` | `PRODUCT` | the shop product page carries the buy action |

**No substitution.** A product whose denominator role is absent has no rate.
Dividing by the information page instead would not blur the answer — that page
carries roughly a hundred times less traffic, so it would invert the ranking.

**A shared page counts once.** `ShopProductPage` does not guarantee one product
per canonical path: an early-bird and a full-price registration for one seminar
legitimately share an event page. Aggregates therefore resolve unique
denominator paths first and add each path's views once, while every product's
acquisitions still count — the page sold both. Per product, that shared page
remains that product's own denominator.

### The rate

```text
Soetamisi 100 ostulehe vaatamise kohta
  = 100 × units in the web window ÷ acquisition-page views in the same window
```

It is `—`, never infinity and never zero, when the product has no product-page
mapping, when GA4 coverage is inadequate, or when the denominator is zero. It is
stated per hundred views rather than as a percentage because page views are not
visitors.

## Missing is not zero

A path with no stored `Ga4PageDaily` rows yields `None`, not `0` — **except**
where page-detail coverage is complete across the whole web window, in which
case a page nobody visited genuinely measured zero. `PageViewFigure` carries
which of the two it is, so no template has to guess.

The same rule runs through the importer: an empty price cell stays null, an
explicit `0.0000` stays zero, and an empty `publicly_listed` stays unknown.

## Periods are anchored to the data

Presets count back from **Commerce coverage end**, not from today. Anchoring on
the wall clock would drift further past a frozen export every day and eventually
select nothing at all — a product page reading "0 ostetud" for a month nobody
has imported. When automation arrives and coverage end starts moving forward on
its own, this needs no change.

## The manual import

### The package

A ZIP carrying `manifest.json`, `products.csv`, `daily_facts.csv` and
`product_paths.csv`. The manifest names every file with its SHA-256 and size;
an undeclared member is refused.

The manifest must declare, and these are checked rather than trusted:

| Field | Required value | Why |
| --- | --- | --- |
| `money_basis` | `net_ex_vat` | a gross figure imported as net overstates everything by the VAT rate |
| `order_state_filter` | `completed` | the Commerce state, never `field_order_completed` |
| `timezone` | `Europe/Tallinn` | so a report date means the same day on both sides |

Plus `schema_version`, `source_name`, `source_as_of`, `coverage_start`,
`coverage_end`, `exported_at`, `member_semantics_verified` and
`public_listing_semantics_verified`.

### The header must match exactly

**This is the privacy boundary, not pedantry.** The upstream source is a Drupal
Commerce order table carrying names, e-mail addresses, telephone numbers, postal
addresses, participant lists, registry codes and gateway transaction IDs. An
importer that skipped columns it did not recognise would accept such an export
and merely not store it *this time* — leaving it on disk, in the artifact store
and in whatever log recorded the run.

An unknown column is a hard failure naming the offending column. No model field
in `apps/shop` is capable of holding personal data.

### Running it

```bash
docker compose exec -T web python manage.py import_shop_snapshot \
  --package /path/to/epood.zip --validate-only --json
```

```bash
docker compose exec -T web python manage.py import_shop_snapshot \
  --package /path/to/epood.zip --json
```

`--validate-only` checks the whole contract, records the attempt, writes no
domain row and never blocks a later live import of the same package.

There is deliberately no upload route, no webhook and no URL option. The path is
given to the command by a person and never enters the database, the interface or
an audit summary.

### What an import does

Publication is one transaction. Either every product, path, fact and the source
state land together, or nothing does and the previous dataset stays exactly as
it was — a half-imported catalogue against last month's facts would be worse
than a stale one, because nothing on screen would say so.

Three outcomes:

- **unchanged** — the digest over the *normalised facts* matches what is already
  published. Nothing is written. Hashing the archive instead would republish
  every row whenever the file was re-zipped;
- **imported** — each changed natural key gets a new current row superseding the
  old one;
- **failed** — the package was refused, the previous dataset is untouched, and a
  sanitized reason is recorded.

## The pages

`/epood/` — one page since 2026-08-18. It was four tabs — `Ülevaade`, `Ostud`,
`Tooted`, `Nähtavus` — chosen by `?fookus=` and defaulting to the overview; the
tab strip retired the same round Koduleht's did, and `Ostud` (payment and
member split), `Nähtavus` (web-effectiveness) and Tooted's own category,
concentration and catalogue sections retired with it. `fookus` is no longer
read at all — an old bookmark carrying it simply opens the one remaining page,
which is what an unrecognised query parameter always does.

The page, top to bottom:

- **the headline** — three Commerce KPIs (units, orders, ordered value) and a
  fourth free/paid card, all read from the **whole** catalogue regardless of
  what a reader has chosen in Tooted below. A reader browsing one product
  family should not silently see "how much did the shop sell" narrow to that
  family;
- **Ostmine ajas** — the trend, on whichever of units, orders or value the
  metric switch carries, unfiltered for the same reason;
- **Lepingupõhjad** — always the document family specifically, whatever a
  reader has chosen in Tooted. Contract templates carry this shop, so this
  section states that outright rather than following a filter — the risers and
  fallers, the order count and the ordered value are all read with
  `product_types=(ProductType.DOCUMENT,)`, never with the reader's own type
  choice;
- **Tooted** — every product, one table, filterable by family and searchable
  across the whole population. Rank, product, category, units, this row's
  share of the current result set, and its movement since the equal-length
  previous window — `Osa` and `Muutus`, both new. A concentration note and a
  physical-products note close it: how much of the table the top ten carry,
  and how many physical-product units moved this period, which the ranking
  itself would otherwise bury under the document family's volume.

Search runs over the whole product population, matching title, canonical path
or Commerce ID, and composes with the type filter and the period through
`periods.build_query` — so the URL stays bookmarkable, shareable and
reload-safe because the state lives in it.

The generic word for a unit is **`Ostetud`** — `Soetatud` until 2026-08-16,
when the owner asked for the plainer word after being shown what it costs:
roughly three quarters of acquisitions are free, and "Ostetud" reads as "we
sold this much" where the measurement is "this many were taken". Each family
may narrow it, so an event says `Registreerimised` — never `Osalejad`, which
is not a fact this dataset holds.

`/epood/toode/<commerce id>/` — one product: its Commerce figures, both
page-view counts, the information → product → acquisition steps for a template
that has both pages, its current price pair with the date they were observed,
and a monthly series.

Both are behind the viewer gate, read PostgreSQL only, and never contact
Koda.ee.

## What is deliberately not here

- no Koda.ee collector, scraper or authenticated fetch of any kind;
- no customer-, participant- or submission-level anything;
- no download analytics — no such record exists;
- no refunds or cancellations — no such concept exists in the source;
- no recognised revenue and no accounting integration;
- **no historical member benefit.** The order line preserves the price paid but
  not the list price that stood on the day of that purchase, so "members saved
  €X" cannot be computed truthfully. Snapshots begin preserving the price pair
  from the first import onward, which makes the calculation possible for future
  purchases and is a separate piece of work;
- no membership analytics — Commerce processes those orders, the membership
  domain owns them.

## Future automation

The contract a collector would have to meet already exists: it publishes the
same logical facts through `import_shop_package`, keyed the same way, and
`ShopSourceState.coverage_end` starts moving forward on its own. Nothing in the
models, the selectors, the metric definitions or the pages needs to change.

The 2026-08-11 audit found no working export endpoint on Koda.ee — Drupal's
JSON:API is disabled and both of the site's own Views export displays return
HTTP 500 — but `/api/v1/company-list` proves a Views REST export works there.
A PII-free order-line export following that pattern is the natural next source,
and is a Koda.ee change rather than a DashKoda one.
