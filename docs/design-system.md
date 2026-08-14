# DashKoda design system

## Purpose

One dark, Chamber-aligned interface language for an internal board and
management dashboard. It is built to be read quickly on a meeting-room display
and to stay honest: a value is never shown without the date it describes, a
figure drawn beside another of a different definition says which is which, and
an empty module says so plainly rather than filling itself with something
plausible.

Version 1 is dark only. There is no light theme and no user theme selection.

## Brand inputs

From the Chamber CVI (`Logoraamat`, pages 15 and 17):

| Element | CVI value | Token |
| --- | --- | --- |
| Chamber blue | PMS Process Cyan, `R0 / G159 / B218` = `#009FDA` | `--color-brand` |
| Chamber dark grey | PMS 432C, `R59 / G59 / B56` = `#3B3B38` | basis of the neutral ramp |
| Primary typeface | FF DIN Pro | not licensed for this project |
| Fallback typeface | Arial | included in the web stack |

The supplied logo PNGs render their blue as `#009FE3` rather than the CVI's
`#009FDA`. The raster files are used exactly as supplied and are never
recoloured; the interface tokens follow the CVI value. This is a known and
accepted one-pixel-value difference between the logo artwork and the UI accent.

FF DIN Pro is not licensed for redistribution here and no font file is committed.
The web stack is:

```css
system-ui, -apple-system, "Segoe UI", Arial, sans-serif
```

This keeps Arial, the CVI's own fallback, in the chain while avoiding any
external font request.

## Tokens

All tokens are declared in `frontend/src/styles.css` inside Tailwind's `@theme`
block, which emits them as CSS custom properties on `:root` and simultaneously
makes them available as utilities (`bg-surface`, `text-brand`, `border-border`).

### Colour

| Token | Value | Use |
| --- | --- | --- |
| `--color-bg` | `#101418` | page background |
| `--color-surface` | `#171c22` | cards, sidebar, sections |
| `--color-elevated` | `#1e242b` | inputs, hover fills |
| `--color-sunken` | `#0b0e12` | drawer scrim |
| `--color-border` | `#2a323b` | default separators |
| `--color-border-strong` | `#3d4954` | emphasised separators, control edges |
| `--color-text` | `#e8edf2` | primary text |
| `--color-text-secondary` | `#9aa7b4` | supporting text |
| `--color-text-muted` | `#7d8b99` | metadata |
| `--color-text-inverse` | `#101418` | text on brand-blue fills |
| `--color-brand` | `#009fda` | accent, active state, primary action |
| `--color-brand-hover` | `#35b8e8` | hover |
| `--color-brand-focus` | `#5cc7ef` | focus ring |
| `--color-brand-soft` | `#0e2a37` | active navigation background |
| `--color-brand-border` | `#17506a` | brand badge edge |
| `--color-success` | `#4fbf95` | verified, fresh |
| `--color-warning` | `#e3ac4e` | stale, needs attention |
| `--color-danger` | `#ef7d6e` | failed, missing, error |
| `--color-info` | `#5fb3e8` | neutral note |

Each status colour also has a `-soft` companion used only as a low-contrast fill
behind its own text.

Contrast on `#101418`: primary text ≈ 15:1, secondary ≈ 7:1, metadata ≈ 5:1,
brand blue ≈ 6.2:1, and every status colour ≥ 6:1. All exceed WCAG 2.2 AA for
normal text.

Brand blue is an accent: the active navigation item, the primary button, focus
rings and links. It is never the page background and never a large fill.

### Spacing, radii, shadows

Spacing uses the Tailwind scale unchanged (`0.25rem` step). Radii are
`--radius-xs` `0.125rem` through `--radius-xl` `0.75rem`; cards and sections use
`lg`, controls and badges use `md`/`sm`. Shadows are deliberately flat:
`--shadow-card` for resting surfaces and `--shadow-raised` for the mobile
drawer. There are no glows and no decorative gradients.

### Typography

| Token | Size | Use |
| --- | --- | --- |
| `--text-micro` | `0.6875rem` | metadata, badges, table headers |
| Tailwind `text-sm` | `0.875rem` | body and controls |
| Tailwind `text-base` | `1rem` | inputs |
| Tailwind `text-2xl`/`3xl` | — | page title |
| `--text-metric` | `2rem` | KPI values |

## Layout and breakpoints

| Width | Behaviour |
| --- | --- |
| 320–767 px | top bar with hamburger; navigation in an overlay drawer; single column |
| 768–1023 px | same drawer navigation; two-column card grids |
| ≥ 1024 px | persistent 17rem sidebar; drawer removed from layout |
| ≥ 1280 px | four-column KPI strip; module cards in two columns |
| ≥ 1536 px | content column capped at `--container-content` (96rem) |

The sidebar is a fixed 17rem so it stays stable while content reflows. The main
column is capped so a 1920 px meeting-room display does not produce unreadably
long lines.

## Components

Reusable Django partials live in
`apps/dashboard/templates/dashboard/components/`. Each file documents its own
context contract in a leading `{% comment %}` block.

| Component | Purpose |
| --- | --- |
| `nav_item` | routed link, active state, or inert `Lisamisel` entry, with optional nested children |
| `section_header` | section title, description and optional badge |
| `kpi_card` | one indicator, with the date it describes in the footer |
| `freshness_row` | the as-of date of the value above it |
| `status_badge` | status expressed as text inside a coloured chip |
| `empty_state` | truthful "there is nothing here yet" |
| `planned_module` | truthful "nothing collects this at all" |
| `legal_topic` | one legal-work topic, a link only once a source gives the record an address |
| `callout` | one short note with a thin accent edge |
| `chart_figure` | one ECharts chart, plus the text summary and data table that always accompany it |
| `trend_chart` | two or more dated series on one pair of axes, solid and dashed, with the same alternatives |
| `channel_card` | one communication channel: a value, its provenance, or a truthful reason it has neither |

`kpi_card`'s detail rows link their label when the count has a section listing
exactly its rows, and they wear `dk-link-quiet` rather than the ordinary
`dk-link`. These are labels under a figure: three of the ordinary blue rule in
one small cell competes with the counts the cell exists to show, and with the
card's own "Vaata …" link. The quiet variant carries a faint dotted underline
that promotes to the ordinary link colour and a solid rule on hover, so the
affordance is **confirmed** on hover rather than introduced by it. That matters
twice over — a link distinguished only by a hover state does not exist on a touch
screen, and one distinguished only by hue does not exist for a reader who cannot
separate the hues.

`legal_topic` exists because the Õigusloome card lists the same kind of record
under two tabs — Töös and Välja läinud — and a rule about when a topic is
clickable must not be able to hold in one of them and not the other. Each tab
previews at most seven records; the full Õigusloome page is where the whole
population is read. Arrivals are not a list of their own on either surface — a
record that has just come in is active work, and Hetkel töös already lists it.
Both tab panels wear `dk-preview-reserve`, which holds the height of a
seven-row list so the shorter tab does not pull the card's freshness row up the
page. It is a minimum rather than a fixed height: a short list simply ends,
leaving empty surface, and a row that grows still expands the card.
Nothing supplies an address today: `Tööd eelnõudega.xlsx` has no such column and
is read-only to this application, and a column on `LegalWorkItem` would not help
on its own, because snapshot rows are rebuilt on every import and a manually
entered address would be erased by the next sync. The address has to arrive with
the record. Whatever eventually writes it validates it — `apps.core.public_http`
holds the predicate the other feeds use — because a template that decides
whether an href is safe is a template that will one day decide wrong.

`kpi_card` accepts `label`, `value`, `unit`, `change`, `change_direction`,
`comparison_period`, `secondary`, `meter_pct`, `status`, `status_label`,
`as_of`, `empty_message` and `flush`. Its presence test is `is not None` rather than truthiness, so a reported
zero renders as the measurement it is instead of falling through to the empty
state.

### Empty is not the same as unconnected

Two components look similar and mean different things, and keeping them apart is
what makes the numbers on the page trustworthy:

- `empty_state` — this module has a source, and it currently has nothing to show;
- `planned_module` — nothing collects this figure at all. It shows the intended
  slot, an `Ühendamata` or `Lisamisel` badge and one line naming what the source
  would be. It never shows a placeholder number and never a date by which the
  source will exist.

`apps/dashboard/connections.py` holds the vocabulary — `Ühendatud`,
`Vananenud`, `Ühendamata`, `Lisamisel` — and derives a wired feed's state from
that module's own summary rather than restating the rule.

### Manually entered is a third thing again

A figure somebody typed is neither a connected feed nor an empty module, and it
must not borrow the words for either. `Ühendatud` beside a hand-entered follower
count would tell a board member an integration exists.

`apps/visibility/selectors.ReadingState` therefore carries its own vocabulary —
`Käsitsi sisestatud`, `Vajab uuendamist`, `Andmed puuduvad` — and `channel_card`
renders three distinct states:

- **planned** — nothing collects this at all. A dash, no link, and why. Google
  Analytics is the only one;
- **no data** — a store exists and nobody has entered a reading. `Andmed
  puuduvad`, never a zero: a zero would claim the Chamber has no followers;
- **observed** — a value, always with its observation date and `Käsitsi
  sisestatud`.

The words *sünkroonitud*, *API-ga ühendatud* and *automaatselt uuendatud* never
appear for a manually entered value, and a test asserts it.

The six-slot channel band uses `.dk-kpi-strip-wide`: one column below `sm`, two
to `lg`, three to `2xl` and six above it. Six across at 1280 px would wrap the
labels mid-word.

### Provenance travels with the figure

`freshness_row` carries the **as-of date** of the value above it, and that is
all: the board asked for the source name, the update cadence and the connection
badge to come out of the card footers.

Provenance itself is not gone, and where two figures of different currency sit
together it is still spelled out. The Liikmeskond card still says which number
came from the daily public directory and which from the monthly board report — a
figure recounted every day and one reported once a month are different kinds of
claim, and the overview shows both at once. The connection strip at the foot of the overview
still counts the connected and the stale sources, so a failed check is disclosed
even though no card carries a badge for it. Every figure is still *built* with
the `Connection` it came from; what changed is how much of it the footer prints.

### Analytical charts

The Liikmeskond charts are ECharts, drawn from a payload the server prepares in
full. Five rules make them read as one system rather than as five drawings.

**One subject, everything else context.** A chart has a current year, or a
current series, and it is the strong one: full-weight line, drawn last, on top.
Comparison series are muted, dashed and thinner. Historical years are context,
not competitors — three at most behind the subject, because a fourth stops being
background and becomes a thicket. A chart where every series has equal weight
asks the reader to decide what matters, which is the chart's job.

**Legends are a last resort.** A series is labelled at its own last point where
that stays legible, so the reader never looks away from a line to find out which
one it is. A single-series chart gets no legend at all: the heading already
names it.

**Direct values where they replace a hover.** Bar charts state their value at
the bar end; distribution bars add their share beside it. `labelLayout:
{hideOverlap: true}` drops the ones that would collide at narrow widths rather
than printing them over each other. Time-series points are not labelled
individually — that is label soup — beyond the last one.

**Tooltips are built on the server.** Every figure is formatted in Python by the
helpers in `apps.core.formatting`, keyed to its datum, and rendered by
`frontend/src/charts.js` as DOM nodes with `textContent`. Three things follow:
no number is spelled two ways on one page, the browser never has to know what a
percentage point is, and a label that arrived from a source cannot be read as
markup. A tooltip states the question's answer — the values, their difference,
and the comparison — rather than a series name and a raw number.

**Numbers and dates are Estonian everywhere they are read.** `3 412` with a
non-breaking group separator, `742 400 €`, `72,8%` with a decimal comma, `+27`
and `−17` with a real U+2212 minus, `+3,4 pp` for a movement in percentage
points — which is not percent, and is spelled out because the distinction is the
reason it exists. Dates are `31.07.2026`, `31. juuli` or `juuli 2026` by
context; an ISO date never reaches a reader. Month axes use `jaan`–`dets`, not
Roman numerals, even where the source numbers its months that way.

#### Quality states are part of the visual language

Four states must never collapse into each other, and three of them are drawn
differently rather than only footnoted:

| State | Drawn as |
| --- | --- |
| verified | an ordinary filled point |
| provisional | a **hollow** point, plus `Olek: esialgne` in its own tooltip |
| conflicted | nothing — no point, and no line across the gap |
| missing | nothing, and never a zero |
| an explicit zero | a real point at zero |

Provisional is hollow rather than coloured, because an estimate that firms up
next month is not an error and the warning hue already means something else.

#### An axis can lie

A y domain is where a truthful series most easily becomes a misleading picture,
so the rule is written down in `apps.membership.analytics.value_domain` and
tested from both directions. A **level** — a membership sitting in a narrow band
far from the origin — is never anchored at zero, which would draw every real
change as flat; and it is never fitted so tightly that a one-percent drift reads
as a cliff, so the domain covers at least a twentieth of the largest value. A
**proportion** that genuinely starts the year at nothing — budget completion —
does start at zero, and its ceiling clears 100% so exceeding a budget is visible
rather than clipped.

#### Controls belong to their chart

A control sits inside the section it governs, and a section that governs nothing
has none. The Liikmeskond page carried one date range above five charts of which
two obeyed it; a control that appears to govern the page and does not is how a
reader learns to mistrust the numbers. A snapshot section states `Seisuga
<date>` instead of inheriting a range that never applied to it.

A control is only rendered when it can change the picture: a benchmark the
history cannot support and a preset window the history cannot fill are both left
out, because a choice that does nothing leaves the reader wondering what they
broke. Controls are ordinary links and GET forms carrying server-resolved values
— never the incoming query string copied forward — so a view is bookmarkable and
no unvalidated input is reflected into an href.

#### Height is a named shape

`dk-chart-large`, `dk-chart-medium` and `dk-chart-categorical`. A five-year time
series and a four-category distribution do not want the same frame, and no
JavaScript measures anything. All three keep a floor on a phone: a chart squeezed
below it is not a smaller chart, and the data table underneath is the better
answer at that size.

### Proportions and trends are SVG geometry

The Content Security Policy is `style-src 'self'` and
`tests/dashboard/test_overview.py` asserts that no `style="` reaches the page, so
a bar length or a line position may never be an inline width. Both are drawn as
SVG **attributes** — `<rect width="78">`, `<polyline points="…">` — with colour
supplied by Tailwind `fill-*` and `stroke-*` classes. Coordinates are computed
server-side in `apps/dashboard/sparkline.py`.

`trend_chart` puts several dated series on **one** pair of axes, which is what
the Liikmeskond card needs: the board reads it for the gap between the member
total and the paid members, and two drawings with two independent scales made
that gap a matter of guesswork. The x axis is time, not position in the series,
so a daily source and a monthly one land on their own dates instead of being
lined up by index. Lines differ in **pattern as well as colour** — one solid,
one dashed — so they survive greyscale and a reader who cannot separate the two
hues. Sharing axes is a drawing decision only: each line keeps its own label and
source, nothing is summed, and neither is extended with the other's
observations.

Its month labels are HTML below the drawing rather than SVG text, because the
drawing is stretched to the card width with `preserveAspectRatio="none"` and
that would stretch any glyph inside it. They are hidden below `sm`, where twelve
month names cannot fit a phone-width card; the stated range and the tables
carry the same window in words.

Every observation is **hoverable, with no JavaScript required**. Three
attribute-only decisions carry that, and each of them is what it is because of
the stretched viewBox or the Content Security Policy:

- the hit target is a full-height strip per observation date, drawn **first** so
  its hover fill sits behind the lines rather than over them. Everything drawn
  after it carries `pointer-events="none"`, so the strip is what a pointer meets
  anywhere in its column. A reader aims at a month, not at a dot;
- the reading is an SVG `<title>`, shown by the browser as its own tooltip. One
  strip names **every** line that reported that day, because the card is read as
  one reading — the total, the paid count, and the gap between them. A tooltip
  per line would make that two hovers and a memory. A line with nothing on that
  date contributes no phrase and never a zero;
- a dot is a **zero-length path with a round linecap**, which SVG defines as
  drawing a circle of the stroke width. A `<circle>` would be squashed into an
  ellipse by `preserveAspectRatio="none"`; a stroke marked
  `vector-effect="non-scaling-stroke"` is measured in screen pixels and stays
  round at any card width.

The native `<title>` tooltip is the floor: browsers show it after their own
delay, only while the pointer holds still, and never on touch.
`frontend/src/trend-tooltip.js` — part of the app bundle, mounted on
`[data-trend-chart]` — lifts each `<title>` out of the live document and shows
the same reading in one shared `.dk-chart-tip` element that follows the pointer
instantly and also answers a tap. Its coordinates are CSSOM assignments, which
`style-src 'self'` permits; nothing writes a style attribute into markup. With
the bundle blocked the `<title>`s simply stay.

Neither tooltip reaches a keyboard, which is exactly why the data table below
is not optional.

Geometry inside an attribute is written with `stringformat`, never
`floatformat`. The dashboard renders in Estonian, `floatformat` is localised,
and `12,34` in a `points` or `d` attribute is not one coordinate — it is two.

The website page carried its card-sized trends as inline SVG while it was
**Nähtavus**, rather than loading the ECharts bundle for a line a few pixels
tall. As **Koduleht** it draws real analytical surfaces — a traffic line,
horizontal rankings, a stacked composition, an opportunity scatter — so it loads
the bundle, and only on the views that have something to draw. The rule that did
not change is `chart_figure`'s contract: the text summary and the data table stay
in the document for every reader and are not a fallback, and a series with fewer
than two points is not a trend and is not drawn.

Koduleht builds its payloads in `apps/visibility/website_charts.py` with a local
dataclass of the same shape as `apps.membership.charts.ChartPayload`. The
component's contract is the shared thing; the dataclass is not, and importing one
feature module's into another would couple two apps through an object neither
owns.

There was once a `sparkline_figure` component for this. It was never included by
any template and was removed; the inline implementation is the only one, and a
second component recreated to hold the abstraction would be a component with one
caller.

Components are covered by `tests/dashboard/test_components.py` using clearly
synthetic values. `tests/dashboard/test_overview.py` asserts that with nothing
connected the page renders no business digits at all, and
`tests/dashboard/test_overview_data.py` asserts that once sources are connected
each figure is the one its source published.

`chart_figure` takes a payload built on the server and renders three things
together: the canvas, a text summary, and the same values as a table. The summary
and the table are not a fallback — they stay in the document for every reader,
and only the canvas is hidden when there is nothing to draw. The payload travels
in a non-executable `application/json` block, so a chart never needs an inline
script or a relaxed Content Security Policy.

## Design rules

- Empty is a state, not a gap: say why there is nothing, in Estonian.
- Never show a number, trend, date or owner that is not backed by a verified
  source.
- A missing value is drawn as nothing. Never a zero, and never a line
  interpolated across the gap — both would state something no source said.
- A chart with no data is not rendered at all; an empty axis is not an empty
  state.
- Colour is never the only signal. Status badges carry text, KPI changes carry
  an arrow glyph, deadline urgency carries the number of days remaining, and the
  active navigation item carries `aria-current`.
- A comparison states its own baseline. The overview's activity strip uses one
  fixed window so every count in it describes the same period; the member delta,
  whose baseline is the previous reading rather than a period, is shown with its
  own date beside the figure instead of being mixed into that strip.
- A link that takes the reader out of the application opens in a new tab; a link
  that stays inside it does not. "Out" means a koda.ee page, a public profile, or
  a file served for reading rather than a page — the opinion PDFs are DashKoda's
  own URLs and still count, because a PDF replacing the dashboard loses the
  reader's place. The "Vaata …" links, headline counts, navigation, filters and
  pagination all stay in the current tab. The pattern is
  `target="_blank" rel="noopener noreferrer"` plus a visually-hidden note that
  names the destination *and* the new tab — `(avaneb koda.ee lehel uuel
  vahelehel)`, `(PDF, avaneb uuel vahelehel)`. `rel` is not optional: `_blank`
  without `noopener` hands the opened page a reference back to this one. Neither
  is the note: a new tab only sighted readers are told about is one a
  screen-reader user discovers by finding the back button gone.
- A Tailwind class is never assembled at render time. Utilities are generated by
  scanning these templates, so a class name built from a variable simply would
  not exist in the stylesheet.
- Restraint over decoration: borders and spacing carry the hierarchy.
- Compact but not cramped: 11px metadata, 14px body, 44px minimum control height.

## Accessibility

Target is practical WCAG 2.2 AA:

- one `<h1>` per page and headings in order;
- landmarks: `banner`, `navigation`, `main`;
- a skip link as the first focus stop;
- a 2px `--color-brand-focus` focus ring with a 2px offset on every control;
- the drawer toggle is a real `<button>` with `aria-expanded` and
  `aria-controls`; opening moves focus to the close button and closing returns
  it to the toggle;
- Escape closes the drawer;
- decorative SVG icons are `aria-hidden="true"` and every icon-only button has an
  `sr-only` label;
- `prefers-reduced-motion: reduce` disables animation and transitions;
- no page-level horizontal scrolling from 320 px upward, including at 200% zoom.
  The browser suite measures this at 320, 375, 768, 1024, 1440 and 1920 px, and
  again at half viewport to emulate 200% zoom.

A full focus trap inside the drawer is not implemented. Focus is moved into and
out of the drawer and Escape closes it; trapping is deferred to a later pull
request.

## Logo

See [frontend.md](frontend.md) for logo provenance, the verification that was
performed on the negative variant, and its usage rules.
