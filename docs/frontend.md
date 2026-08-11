# Frontend build and assets

## Dependencies

Everything is installed from `package-lock.json` and bundled locally. There is
no CDN, no external stylesheet and no external font request at runtime.

| Package | Version | Role |
| --- | --- | --- |
| `tailwindcss` / `@tailwindcss/cli` | 4.3.3 | design tokens and utilities |
| `htmx.org` | 2.0.10 | server-rendered partial updates |
| `@alpinejs/csp` | 3.15.12 | small local UI state, CSP build |
| `echarts` | 6.1.0 | charts |
| `esbuild` | 0.28.1 | JavaScript bundler |
| `@playwright/test` | 1.62.0 | browser smoke tests |

Versions are exact, not ranges. `npm ci` is the only install path used by CI and
by the container build.

## Source and output

```text
frontend/src/app.js       -> static/build/app.js
frontend/src/charts.js    -> static/build/charts.js
frontend/src/styles.css   -> static/build/styles.css
```

Build it with:

```powershell
npm ci
npm run build
```

`static/build/` is generated and git-ignored apart from a `.gitkeep`. Never edit
a file in it by hand. `static/brand/` is checked in and holds the logo and the
placeholder favicon.

Django serves the output through WhiteNoise:

- `STATICFILES_DIRS` includes `static/`;
- `collectstatic` copies and hashes it into `staticfiles/`;
- production uses `CompressedManifestStaticFilesStorage`.

`npm run build` must therefore run before `collectstatic`, both in CI and in the
image build. Source maps are not generated, so none are served.

## Container build

The Dockerfile has a pinned Node 22 stage that runs `npm ci` and `npm run build`.
Only the resulting `static/build` directory is copied into the Python builder
stage. Neither the development nor the production runtime image contains Node,
npm or `node_modules`; CI asserts this explicitly.

## JavaScript boundaries

`app.js` is the bundle every page loads. It contains htmx and the Alpine CSP
build and nothing else.

`charts.js` is a second bundle, loaded only by pages that actually draw a chart
through the `extra_head` block in `templates/base.html`. It is over a megabyte,
so putting it in `app.js` would make every page pay for it. Anything added
through that block must still be a local `{% static %}` module: no CDN, and no
inline script.

Alpine runs as the **CSP build**: a directive value may only name a property or
a method of a registered `Alpine.data()` component, never an inline expression.
Anything a directive needs is therefore exposed as a getter — `tabPair` returns
its own class strings that way rather than composing them in the markup. Alpine
holds interface state only — the mobile drawer and the overview's paired tabs —
and never business data. There is no inline `<script>` anywhere.

Both components degrade rather than disappear. The drawer toggle and the tablist
carry `x-cloak`, so before Alpine boots there is no dead control: the drawer's
`<noscript>` navigation stands in for one, and for the other both tab panels are
simply visible, each under its own heading. Nothing is hidden that cannot be
revealed again.

The overview draws its miniature trends and its proportion bars as server-rendered
SVG rather than through `charts.js`. It is the page every viewer loads first, and
a card-sized line of a dozen points does not justify a megabyte of charting
library. The Nähtavus page follows the same rule for the four social follower
histories: server-drawn `<polyline>` plus the values as a table, and no chart
bundle. See [design-system.md](design-system.md).

htmx is configured through a `htmx-config` meta tag in `templates/base.html`:

```json
{"includeIndicatorStyles": false, "allowEval": false, "allowScriptTags": false}
```

`includeIndicatorStyles: false` stops htmx injecting an inline `<style>` element,
which would otherwise require `style-src 'unsafe-inline'`; the equivalent rules
ship in the compiled stylesheet as `.dk-indicator`. `allowEval: false` disables
the only htmx code paths that would need `unsafe-eval`; `hx-on` and `js:` values
are not used anywhere.

htmx carries one pattern, used five times: **live search**. Every search box on
the dashboard filters its results as somebody types.

`apps/dashboard/live_search.py` holds the shared half and documents the
reasoning; the five wirings are the newsletter sends and the website-page search
on Nähtavus, the campaign archive, the news archive, and the whole filter form
on the event programme.

The shape is the same everywhere:

- the results are a partial. The full page and the fragment endpoint render the
  same template, so what a reader sees while typing and what they see after a
  reload cannot drift apart;
- **the input is never inside the swapped region.** htmx replaces the region's
  contents, and an input inside it loses the caret, the selection and the focus
  ring on every keystroke. The region is drawn around the answer, not the
  section;
- `hx-trigger="input changed delay:250ms, search"` debounces. `changed` stops an
  arrow key spending a query; `search` is the second trigger, for the native
  clear button an `input type=search` draws;
- `hx-include="closest form"` sends the hidden period, section or newsletter
  along with the term, so typing narrows exactly what submitting would;
- `hx-sync="this:replace"` aborts the in-flight request when the next keystroke
  arrives. Without it two answers race and the slower can land last, leaving the
  reader results for a prefix of what the box says;
- pagination resets. A new term is a new question, and page 40 of a four-row
  result answers "nothing found";
- **the address bar is rewritten server-side.** `hx-push-url="true"` would push
  the fragment endpoint, so reloading would land on a bare partial; instead each
  fragment answers with `HX-Push-Url` naming the real page. The rest of the
  page's state is recovered from the `HX-Current-URL` request header — query
  only, declared keys only, never the path;
- the news page swaps two regions from one response, because its result count
  sits above the card and its rows inside it. The count rides along as an
  `hx-swap-oob` element;
- **the form still works without JavaScript.** Every box keeps its submit
  button, every form still `GET`s to its own page, and the server renders the
  same partial. Live filtering is an enhancement, not the mechanism.

The remaining fragment, `GET /dashboard/varskus/`, is served but included by no
page — see `apps/dashboard/templates/dashboard/partials/freshness.html`.

Every fragment route is an ordinary protected route: the viewer middleware
guards it, and when the session has expired it answers an HTMX request with
`204` and an `HX-Redirect` header, so the browser navigates to `/sisene/`
instead of a login form being swapped into a results table.

**There is no polling anywhere**, and none should arrive with any control that
is added later.

`charts.js` reads its data from a non-executable `<script type="application/json">`
block, initialises responsively with a `ResizeObserver`, disables animation under
`prefers-reduced-motion`, keeps a text summary and a table as the accessible
alternative, and falls back to the chart empty state whenever the payload has no
data points. Its own contract is documented at the top of the file.

It mounts every `[data-chart]` figure on load rather than waiting to be called,
because the alternative is an inline `mountCharts()` call that the Content
Security Policy forbids and should keep forbidding. A page opts in by including
the module at all.

The shared figure markup lives in
`dashboard/components/chart_figure.html`, which renders the summary and the data
table server-side. Those are not a fallback that appears when something breaks:
they stay in the document, so a reader whose browser never runs the module gets
the same numbers as a table. Only the canvas is hidden when there is nothing to
draw. The first module to use it is the internal membership history; see
[internal-membership-history.md](internal-membership-history.md) for what each
chart means and why none of them substitutes zero for a missing value.

## Logo provenance and limitations

The Chamber supplied two full-colour PNGs, `KODA_EST_logo_horiz+tag.png` and
`KODA_EST_logo_vert+tag.png`. Earlier work prepared negative variants for dark
backgrounds. Before committing anything, the horizontal negative was compared
against the original pixel by pixel:

- identical canvas (1654 × 709) and identical artwork bounding box;
- opaque-pixel mask identical, 0 differing pixels of 1 172 686;
- the only change is the dark grey ink `#373736` / `#383937` becoming `#FFFFFF`;
- the brand blue `#009FE3` is untouched.

It is therefore a faithful negative, not a redrawing, and it is committed
unmodified as `static/brand/koda-logo-horizontal-negative.png`. The CVI permits
the logo on dark backgrounds provided every part stays clearly legible.

Usage rules in the interface:

- rendered at its natural aspect ratio, never stretched, cropped or filtered;
- the transparent margin the file carries to the right of and below the artwork
  is kept as clear space;
- capped at 15rem wide, which is comfortably above the CVI 20 mm minimum for the
  horizontal logo;
- alt text names the Chamber and, in the sidebar, the link destination;
- exactly one logo is shown at a time. The mobile top bar shows the product name
  `DashKoda` as text, not a second mark.

Known limitations:

- **No official SVG exists yet.** The implementation plan lists obtaining one as
  an open item. A raster logo is used until it is supplied.
- **No simplified minimum-size icon.** The CVI specifies a special simplified
  icon below the minimum size; it was not supplied, so no small-size variant is
  used anywhere.
- **The favicon is a placeholder.** `static/brand/favicon.svg` is a plain
  Chamber-blue rounded square. It is deliberately not a Chamber mark and should
  be replaced once an official icon asset is available.
- **The vertical logo is not committed**, because nothing in the shell uses it.
- The CVI PDF itself is not committed to this repository.

## Browser smoke tests

```powershell
npm ci
npx playwright install --with-deps chromium
npm run e2e
```

The suite in `e2e/` runs against an already-running application; it never starts
one. `DASHKODA_E2E_BASE_URL` selects the target and defaults to
`http://127.0.0.1:8000`. `DASHKODA_E2E_PIN` supplies the viewer PIN and defaults
to the synthetic CI value `4071`. **The real PIN must never be used here.**

CI runs it in six projects — 1920, 1440, 1024, 768, 375 and 320 px — against the
Compose development runtime. The widths sit on both sides of every layout
breakpoint: 320 and 375 below `sm`, 768 at the tablet grid, 1024 where the
persistent sidebar replaces the drawer, 1440 for an ordinary desktop, and 1920
for a meeting-room display, which is also where the six-slot channel band first
fits on one line.

That run uses `config.settings.local`, because the browser drives the
application over plain HTTP on loopback and the production settings force an
HTTPS redirect and secure cookies. The separate production-settings Compose
smoke test is unchanged and still covers that configuration.

## Dependency audit

CI runs `npm audit --audit-level=high` and reports its result without failing the
build and without applying automatic major-version upgrades. Findings are
reviewed and upgraded deliberately.
