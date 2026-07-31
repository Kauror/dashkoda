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
| `--color-elevated` | `#1e242b` | inputs, hover fills, skeletons |
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
| `error_state` | announced failure, no technical detail |
| `list_row` | compact row, link only when a destination exists |
| `table_wrapper` | scroll container, column spec, empty fallback |
| `skeleton` | genuine loading only, never missing data |
| `callout` | one short note with a thin accent edge |
| `chart_figure` | one ECharts chart, plus the text summary and data table that always accompany it |
| `sparkline_figure` | one server-drawn miniature trend, with the same alternatives |
| `trend_chart` | two or more dated series on one pair of axes, solid and dashed, with the same alternatives |
| `channel_card` | one communication channel: a value, its provenance, or a truthful reason it has neither |

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
together it is still spelled out. `sparkline_figure` names its source and its
cadence, so the Liikmeskond card still says which number came from the daily
public directory and which from the monthly board report — a figure recounted
every day and one reported once a month are different kinds of claim, and the
overview shows both at once. The connection strip at the foot of the overview
still counts the connected and the stale sources, so a failed check is disclosed
even though no card carries a badge for it. Every figure is still *built* with
the `Connection` it came from; what changed is how much of it the footer prints.

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

`sparkline_figure` exists so the overview does not have to load the ECharts
bundle to draw a card-sized line. It keeps `chart_figure`'s contract: the text
summary and the data table stay in the document for every reader and are not a
fallback. A series with fewer than two points is not a trend and is not drawn.

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
