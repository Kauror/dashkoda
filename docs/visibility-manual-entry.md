# Manual visibility metrics

How many people the Chamber currently reaches, entered by hand.

**The four social audience figures are typed in.** There is no Meta, LinkedIn,
Instagram or YouTube client anywhere in this repository, no credential that
would let one exist, and no model field capable of holding a token. A staff user
reads a figure off a platform's own statistics screen and types it in.

Two channels are collected instead and have no box on this form: website traffic
through `sync_ga4`, described [below](#google-analytics-website-traffic), and
the three newsletter list sizes through `sync_smaily`, described in
[newsletter-audience.md](newsletter-audience.md).

That is a deliberate first step, not a shortcut. A typed value publishes through
the same path a collector would use — canonical JSON, SHA-256 content identity,
a metadata-only `SourceArtifact`, an ordinary `ImportRun` and an audit event —
so an automated route can replace the form later without rewriting a single
historical row.

## Scope

These four are what the form offers, and they are the whole of it. `manual_entry`
in `apps/visibility/registry.py` is the single fact the form, its preview and its
confirmation page all derive from, so this table cannot drift from the form
without that flag changing.

| Metric | Estonian label | Unit | Source | Stale after |
| --- | --- | --- | --- | --- |
| `facebook_followers` | Facebooki jälgijad | jälgijat | `manual-facebook-followers` | 45 days |
| `linkedin_followers` | LinkedIni jälgijad | jälgijat | `manual-linkedin-followers` | 45 days |
| `instagram_followers` | Instagrami jälgijad | jälgijat | `manual-instagram-followers` | 45 days |
| `youtube_subscribers` | YouTube’i tellijad | tellijat | `manual-youtube-subscribers` | 45 days |

Website traffic (`ga4-website-traffic`) is registered as a source and has its own
daily collector; its configuration is optional, and it counts as connected only
once an observation has been published. See
[Google Analytics](#google-analytics-website-traffic).

**The three newsletter lists were once typed here and are not any more.** They
are collected daily by `sync_smaily`, one metric per list — e-Teataja, eNews and
e-Vestnik, each reported on its own and never summed. An earlier model split a
single newsletter into member, non-member and overlap recipients; it never
matched what actually goes out, and those three keys are retired. Observations
recorded under them stay in the table because they were real readings, but no
registry entry describes them, so nothing reads them and the form does not offer
them. See [newsletter-audience.md](newsletter-audience.md).

Deliberately out of scope, and absent from the schema: post reach, impressions,
engagement, video views, newsletter opens, newsletter clicks, media coverage,
individual subscribers and any per-person data.

### The vocabulary is closed

`apps/visibility/models.py` holds the seven metrics as `VisibilityMetric`
choices, and `apps/visibility/registry.py` decorates each one with its label,
unit, source, public link, stale threshold and display order.
`registry._check_registry()` runs at import and refuses to load if the two
disagree, so a metric cannot exist in the database without being described, or
be described without existing.

This is not a product-wide key/value store. A free-text metric name would make
"was this ever reported" unanswerable in SQL and would let a typo create a
second series that looks like the first one.

## Metric definitions

**Newsletter figures count list membership.** They are the subscriber count
Smaily reports for a list, collected daily rather than typed. They are not
emails sent, not delivered emails, not opens and not clicks, and the labels
never say otherwise. The word "active" is deliberately avoided: `list.php`
returns a `subscribers_count` and does not document whether unsubscribed
addresses are excluded from it.

**Social figures are the follower or subscriber count the channel's own
statistics show.** Not reach, not impressions, not engagement.

### The newsletter union rule

The Chamber runs two lists and some people are on both:

```text
unikaalsed uudiskirja saajad
  = liikmete uudiskirja aktiivsed saajad
  + mitteliikmete uudiskirja aktiivsed saajad
  − mõlemas nimekirjas olevad saajad
```

Rules, all of them tested:

- the union is calculated **only when all three values exist**;
- an overlap of `0` is a real answer and the two lists then simply add;
- a **missing** overlap is not zero. The union is `None`, the two counts are
  shown separately, and the page states
  `Nimekirjade kattuvus ei ole sisestatud.`;
- adding the two lists without the overlap would double-count everybody on both,
  so it never happens;
- the overlap may not exceed either list — that is arithmetically impossible, so
  it is a hard error rather than a warning. It is checked against the value in
  the same submission, or against the current stored value when the list was not
  re-entered;
- the union is **derived by selectors and stored nowhere**. Persisting it would
  create a fourth number capable of disagreeing with the three it comes from;
- a union is dated by its **oldest** ingredient, because it is only as current as
  its stalest figure.

## Fixed public profile links

```text
Facebook   https://www.facebook.com/Kaubanduskoda
LinkedIn   https://www.linkedin.com/company/ecci/
Instagram  https://www.instagram.com/kaubanduskoda
YouTube    https://www.youtube.com/user/Kaubanduskoda
```

They are **fixed application configuration**, not editable form values. There is
no field, form or setting through which anyone can introduce a URL — AGENTS.md
forbids that, and a display link is no exception. `_check_registry()` asserts
each one is HTTPS on an exact expected host with no query string, so a typo is an
import error rather than a link pointing somewhere unintended.

Nothing fetches them. No page render, no command and no scraper touches a social
platform.

Viewer links follow the established DashKoda pattern for a link that leaves the
application: `target="_blank" rel="noopener noreferrer"` and a visually-hidden
note naming both the outside destination and the new tab. `docs/design-system.md`
holds the rule.

The newsletter metrics deliberately have **no** link. The only URL would be a
Smaily account login, and sending a board member to a login screen is not
provenance.

### On the LinkedIn address

The brief supplied `https://www.linkedin.com/company/2877448` and asked for a
read-only probe. Result, recorded because it is a limitation rather than a
finding:

- the numeric URL answers **HTTP 999** — LinkedIn's anti-automation status — to
  an unauthenticated request, with **no `Location` header**. No redirect to a
  canonical address can be observed from outside;
- `https://www.linkedin.com/company/ecci/` **was** verified directly and resolves
  to "Estonian Chamber of Commerce and Industry", Tallinn, koda.ee.

The verified vanity address is therefore what the application stores. No follower
count was read or recorded during that probe.

## Update cadence and freshness

There is no schedule. Somebody reads the figures and enters them, roughly
monthly for the social channels and quarterly for the newsletter lists.

Freshness is a property of the newest **observation**, not of a feed:

- social channels: `Vajab uuendamist` after **45 days** — one clearly missed
  cycle;
- newsletter lists: after **90 days**, because they move more slowly and are read
  from a different system.

Both thresholds live in `apps/visibility/registry.py` as documented constants,
never as a condition in a template. Staleness only ever **labels** a figure. An
old reading is still the last thing anybody counted and is never hidden.

`DataSource.stale_after_days` is deliberately left unset for these sources: that
field drives feed-level staleness, and these have no feed to fall behind.

## Connection vocabulary

Manual data must never masquerade as an automatic feed.
`apps/visibility/selectors.ReadingState` carries its own wording rather than
reusing `apps.dashboard.connections.ConnectionState`, whose `Ühendatud` label
would tell a board member an integration exists:

| State | Label | Meaning |
| --- | --- | --- |
| `OBSERVED` | Käsitsi sisestatud | somebody read and typed this |
| `STALE` | Vajab uuendamist | past the registry threshold for that metric |
| `MISSING` | Andmed puuduvad | nobody has entered it — **not** zero |

Plus `Seisuga 31.07.2026` beside every figure, and
`Google Analytics ei ole ühendatud.` on the website slot.

The words **sünkroonitud**, **API-ga ühendatud** and **automaatselt uuendatud**
appear nowhere for a manually entered value, and a test asserts it.

### The global freshness count is unchanged

`apps/dashboard/freshness.py` counts *connected automatic feeds* — legal work,
membership, news and events — and its `n/4` denominator means exactly that.

Manual visibility observations are **deliberately left outside it**. Adding them
would silently change what the denominator means: a figure somebody typed is not
a source that is being checked, and `Ühendatud andmeallikaid: 5/5` would be a
claim nothing supports. `current_freshness()` is untouched by this pull request.

## The admin hub

`/admin/data-entry/` — **Andmete sisestamine**.

One index of every workflow that writes domain data from a browser. It is not a
second admin site: it lives inside `/admin/`, every view is wrapped in
`admin.site.admin_view`, and it introduces no password, no permission model and
no session of its own. It lists no model and offers no form.

It currently carries two modules:

1. **Liikmeskonna aruanne** — the existing form at
   `/admin/membership/internal-report/new/` and its observation history;
2. **Kanalite statistika** — the visibility form and its entry history.

Adding a third is one entry in `apps/core/data_entry.py`. Entries carry **URL
names, not imports**, so `apps.core` depends on neither `membership` nor
`visibility` and a hub entry cannot break the admin at import time.

The Django admin index gets one panel linking to the hub, added *above*
`{{ block.super }}` so the ordinary model app list is untouched.

## Routes

| Route | Method | Purpose |
| --- | --- | --- |
| `/admin/data-entry/` | GET | the hub |
| `/admin/data-entry/visibility/` | GET | read-only submission history, newest first |
| `/admin/data-entry/visibility/new/` | GET, POST | enter a new submission |
| `/admin/data-entry/visibility/<id>/` | GET | read-only confirmation of what was published |
| `/admin/data-entry/visibility/<id>/correct/` | GET, POST | prefill and publish a revision |
| `/nahtavus/` | GET | the viewer page |

Every `/admin/` route requires an active staff account **and** sits behind the
viewer PIN middleware. The shared PIN alone is never sufficient. All of them are
CSRF-protected, reads are GET, preview and confirm are POST, and publication is
Post/Redirect/Get.

## Preview and confirm

One form, submitted twice.

**Kontrolli** validates, shows the entered values, the derived unique newsletter
audience where possible, the change against each metric's baseline, and any
same-date values that would be superseded. It **saves nothing** — no draft
record, no session copy, nothing to clean up if the tab is closed.

**Kinnita ja salvesta** publishes every supplied metric in one transaction.

A submission with no explicit `action=confirm` defaults to preview, so a stray
submit can never publish.

Validation lives in `manual.build_preview`, not in the form, so posting straight
to the confirmation step applies exactly the same rules.

### The data-entry check

A movement is flagged when **both** of these hold:

- the proportional change exceeds **25 %**; and
- the absolute change exceeds **100**.

Both, because either alone misfires: the proportional rule would flag 4 → 6
subscribers, and the absolute rule would flag an ordinary 200-follower month on a
12 000-follower page.

It is a **data-entry check, not an assertion that the number is wrong**. A
follower count can genuinely jump. The warning never blocks an authorised staff
user; what it catches is a transposed digit or a figure read off the wrong
channel.

Separate warnings cover a decrease, a same-date correction, and a missing
newsletter overlap.

The thresholds are `SUBSTANTIAL_CHANGE_RATIO` and `SUBSTANTIAL_CHANGE_ABSOLUTE`
in `apps/visibility/manual.py`, and both boundaries are tested.

### Number entry

Ordinary whitespace thousands separators are stripped, including the no-break,
narrow no-break, thin and figure spaces that platforms emit. `12,230` and
`12.230` stay **invalid**: a comma or a period could be a decimal mark, and
guessing would be exactly the silent coercion this form must not do.

Blank and `0` are distinct everywhere. A blank field is not entered; a `0` is a
reading that says nobody is there.

## Idempotency

A submission is canonicalised as deterministic JSON — sorted keys, fixed
separators, ISO date, plain integers, normalised note — and hashed with SHA-256.
That hash is `VisibilityEntryBatch.content_hash` and is **unique in the
database**, so a double submit is refused by PostgreSQL rather than only by a
view.

| Situation | Result |
| --- | --- |
| identical preview | nothing written |
| first confirmation | published |
| repeated identical confirmation | the existing batch is returned |
| same metric, same date, same value | unchanged |
| same metric, same date, different value | a correction that supersedes the previous current row |
| same metric, a later date | a new historical observation; both remain |
| one invalid metric | the whole batch is refused |
| a database failure | the whole batch rolls back |

There is no state in which the newsletter figures are published and the social
ones are not.

### Why the artifact payload names its batch

An artifact's identity is `(source, sha256)` and an import key is derived from
that digest. Two submissions can legitimately carry an identical Smaily reading
while differing elsewhere — correcting only the Facebook figure is the obvious
case — and hashing the source's values alone would collide. The per-source
payload therefore names the batch it belongs to, so its identity reads "this
source's contribution to *this* submission".

Each contributing source gets its own metadata-only artifact and its own
`ImportRun`, under a fixed non-secret reference:

```text
manual:smaily-audience:<correlation id>
manual:facebook-followers:<correlation id>
manual:linkedin-followers:<correlation id>
manual:instagram-followers:<correlation id>
manual:youtube-subscribers:<correlation id>
```

**No profile URL is ever stored as an artifact reference.** `SourceArtifact`
independently refuses any reference containing `@` or `?`.

## Correction and supersession

A correction is never an edit.

Re-entering a metric for a date that already has a value creates a **new**
observation that names the old one through `supersedes`. The old row keeps its
number, its batch, its artifact and its place in the audit trail; only its
`is_current_for_date` flag moves.

A value entered for a **later** date is not a correction at all. It is the next
point on the trend, and both readings remain current for their own dates.

`is_current_for_date` is the only field a published observation may ever change —
`MUTABLE_FIELDS` says so and `save()` enforces it. Deletion is refused on the
instance, on the queryset and through `bulk_update`.

Constraints backing this in PostgreSQL:

- `visibilityobservation_one_current_per_metric_date` — one readable row per
  metric per date;
- `visibilityobservation_unique_metric_per_batch` — one value per metric per
  submission;
- `visibilityobservation_value_non_negative`;
- `visibilityentrybatch.content_hash` unique.

Metric-to-source agreement is enforced in `clean()` and by the publication
service rather than by a constraint: the mapping lives in the registry, so
PostgreSQL has nothing to join against.

There is deliberately **no** `supersedes != id` check constraint.
`Model.full_clean()` validates check constraints against the in-memory instance,
whose `id` is still `NULL` before the first save, so such a constraint would
reject every correction at validation time. Nothing is lost: a row cannot name
itself at creation because it has no identifier yet, and cannot acquire one later
because `supersedes` is immutable.

## Audit

Three actions, all through `apps.audit.services.record_event`:

```text
visibility.manual_batch_published
visibility.observation_published
visibility.observation_superseded
```

Summaries carry the metric key, the value, the observation date, the batch id,
the source slug, the collection method, the content checksum and whether
something was superseded. One correlation ID threads a whole submission through
its artifacts, its import runs and every one of its events.

They do **not** carry the note the user typed, any form payload, session data, a
platform token, HTML or a traceback. These counts are aggregate business metrics
rather than personal data, and the existing redaction rules still apply on top.

## Viewer presentation

### Overview — Kanalite statistika

Six slots, in order: Kodulehe külastused, Uudiskirja saajad, Facebooki jälgijad,
LinkedIni jälgijad, Instagrami jälgijad, YouTube’i tellijad.

- website visits show no value, link nowhere, and say Google Analytics is not
  connected;
- the newsletter slot shows the unique audience when the overlap is known, and
  otherwise the two list counts plus the missing-overlap statement;
- social cards show the latest value, the change from the previous observation,
  the observation date, that collection is manual, staleness when it applies, and
  a link to the fixed public profile;
- a channel with no reading shows `Andmed puuduvad`, **never a zero**. A zero
  would claim the Chamber has no followers.

The band is a grid: one column below `sm`, two to `lg`, three to `2xl` and six
above it. Six across at 1280 px would wrap the labels mid-word.

### `/nahtavus/` — Mõju ja nähtavus

Heading and explanation, the current band, the newsletter audience with its
definition, the four social channels with trends, the whole observation history,
the source definitions and the Google Analytics state.

Trends reuse the server-rendered sparkline from PR #14: geometry travels in the
`points` attribute, never in a `style`, which keeps `style-src 'self'` intact.
Four small follower histories do not justify loading ECharts. A series of fewer
than two points is not a trend and nothing is drawn; every trend keeps its values
as an accessible table, which stays in the document rather than being a fallback.
Missing observations are gaps, never zeros.

The history table shows date, metric, value, collection method, correction state
and source. It **does not name who entered a figure** — that is a staff detail
and lives in the admin history instead.

A staff-only `Lisa andmed` action appears when `request.user.is_staff`. An
ordinary shared-PIN viewer never sees an editing control they cannot use.

## Google Analytics website traffic

A daily collector runs in production as of 2026-08-09. The full account is in
[website-analytics.md](website-analytics.md); what matters here is that it is
the one figure in this module nobody types.

- `Ga4DailySnapshot` stores one immutable revision of one reporting day, with
  page and channel rows beside it. Every site figure is nullable, because an API
  that omits a metric has not reported zero;
- `apps/visibility/ga4.py` holds the configuration status, a
  `Ga4NotConfigured` exception, the `WebsiteTrafficReading` normalisation
  contract and `Ga4ApiCollector`, which reads one completed day from the GA4
  Data API through a **read-only** service account
  (`analytics.readonly` and nothing wider);
- the scheduled `sync_ga4` management command is the only caller. It collects
  the previous completed day, and a re-run of an already-collected day finishes
  cleanly without publishing a duplicate. `ops/unraid/sync_ga4.sh.example` is
  the schedule template; the schedule itself is not installed by this
  repository;
- `GA4_PROPERTY_ID` and `GA4_CREDENTIALS_FILE` are optional and default to
  empty. Startup, local development and the whole test suite need neither, and
  only `sync_ga4` reads them. The JSON key belongs in the deployment
  environment only — never in Git, PostgreSQL, a log, an audit summary or the
  interface;
- live acceptance against the real property has not been performed.

The website card stays `Lisamisel` until a real observation exists.
Configuration alone does not make it connected.

Publication follows the path every other source uses: canonical JSON →
SHA-256 → metadata-only artifact → `ImportRun` → an immutable observation →
audit event. No GA4 response body is retained.

## The automation seam, now used twice

`CollectionMethod.AUTOMATIC` was added so a collector could write into the
**same table** through the same publication service, making the form one of two
writers rather than something a migration replaces. Both collectors now use it:

- `sync_ga4` publishes website traffic;
- `sync_smaily` publishes the three newsletter totals, and the entry form no
  longer offers a box for them.

Historical manual rows keep their values and their `manual` method, so a chart
never silently changes meaning — the newsletter figures a staff user typed
before the collector existed are still there, still marked as typed.

The four social platforms remain unautomated: no API, no OAuth, no key, no
service account, no scraping and no schedule.

## Deployment and migration

One migration, `visibility.0001_initial`, creating three tables. It adds no
column to an existing table and touches no other module's data.

```bash
docker compose exec web python manage.py migrate visibility
```

The data sources are registered on first use by
`apps/visibility/bootstrap.py`, so nothing has to be seeded by hand. The GA4
source is registered too, without an artifact or an import run — there is no
content to give one a checksum. The newsletter source was renamed from
`manual-smaily-audience` to `smaily-newsletter-audience` by
`visibility.0006_rename_smaily_source` when its figures stopped being typed;
`ensure_data_source` registers a source once and never updates it, so the rename
had to be a migration.

No new environment variable is **required**. `GA4_PROPERTY_ID` and
`GA4_CREDENTIALS_FILE` may stay unset indefinitely.

No schedule, no new volume, no new container and no server change. The first real
figures are entered by an authorised staff user after deployment, at
`/admin/data-entry/visibility/new/`.
