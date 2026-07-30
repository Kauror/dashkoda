# DashKoda design system

## Purpose

One dark, Chamber-aligned interface language for an internal board and
management dashboard. It is built to be read quickly on a meeting-room display
and to stay honest: a value is never shown without its source, as-of date and
freshness state, and an empty module says so plainly rather than filling itself
with something plausible.

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
| ≥ 1280 px | four-column KPI grid |
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
| `nav_item` | routed link, active state, or inert `Lisamisel` entry |
| `section_header` | section title, description and optional badge |
| `kpi_card` | one indicator, with a full provenance footer |
| `freshness_row` | source, as-of date and freshness badge |
| `status_badge` | status expressed as text inside a coloured chip |
| `empty_state` | truthful "there is nothing here yet" |
| `error_state` | announced failure, no technical detail |
| `list_row` | compact row, link only when a destination exists |
| `table_wrapper` | scroll container, column spec, empty fallback |
| `skeleton` | genuine loading only, never missing data |
| `callout` | one short note with a thin accent edge |
| `chart_figure` | one chart, plus the text summary and data table that always accompany it |

`kpi_card` already accepts the full future API — `label`, `value`, `unit`,
`change`, `change_direction`, `comparison_period`, `status`, `status_label`,
`source`, `as_of`, `freshness`, `freshness_label` — so the first real data module
does not have to change the component. In PR-04 it is rendered only in its empty
state.

Components are covered by `tests/dashboard/test_components.py` using clearly
synthetic values. The dashboard page itself renders none of them, which
`tests/dashboard/test_overview.py` asserts by scanning the page for digits.

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
  an arrow glyph, the active navigation item carries `aria-current`.
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

A full focus trap inside the drawer is not implemented. Focus is moved into and
out of the drawer and Escape closes it; trapping is deferred to a later pull
request.

## Logo

See [frontend.md](frontend.md) for logo provenance, the verification that was
performed on the negative variant, and its usage rules.
