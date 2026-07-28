# Frontend build and assets

## Dependencies

Everything is installed from `package-lock.json` and bundled locally. There is
no CDN, no external stylesheet and no external font request at runtime.

| Package | Version | Role |
| --- | --- | --- |
| `tailwindcss` / `@tailwindcss/cli` | 4.3.3 | design tokens and utilities |
| `htmx.org` | 2.0.10 | server-rendered partial updates |
| `@alpinejs/csp` | 3.15.12 | small local UI state, CSP build |
| `echarts` | 6.1.0 | charts, bundled but not yet rendered |
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

`app.js` is the only bundle any page loads. It contains htmx and the Alpine CSP
build and nothing else.

Alpine runs as the **CSP build**: a directive value may only name a property or
a method of a registered `Alpine.data()` component, never an inline expression.
It holds interface state only — currently just the mobile drawer — and never
business data. There is no inline `<script>` anywhere.

htmx is configured through a `htmx-config` meta tag in `templates/base.html`:

```json
{"includeIndicatorStyles": false, "allowEval": false, "allowScriptTags": false}
```

`includeIndicatorStyles: false` stops htmx injecting an inline `<style>` element,
which would otherwise require `style-src 'unsafe-inline'`; the equivalent rules
ship in the compiled stylesheet as `.dk-indicator`. `allowEval: false` disables
the only htmx code paths that would need `unsafe-eval`; `hx-on` and `js:` values
are not used anywhere.

The one htmx pattern in the shell is the freshness fragment:

- `GET /dashboard/varskus/`, protected by the ordinary viewer middleware;
- triggered by a button click only — there is no polling;
- swaps `innerHTML` of a persistent `aria-live` region so the update is
  announced;
- without JavaScript the same button submits its enclosing `GET` form and simply
  reloads the overview, which renders the identical fragment server-side;
- when the viewer session has expired the middleware answers an HTMX request with
  `204` and an `HX-Redirect` header, so the browser navigates to `/sisene/`
  instead of the login page being swapped into the fragment.

`charts.js` is a separate bundle that no template loads. It exists so the first
real data module can mount a chart without changing the build or the CSP. It
reads its data from a non-executable `<script type="application/json">` block,
initialises responsively with a `ResizeObserver`, disables animation under
`prefers-reduced-motion`, keeps a text summary and a table as the accessible
alternative, and falls back to the chart empty state whenever the payload has no
data points. Its own contract is documented at the top of the file. PR-04 renders
no chart, because there is no verified data to draw.

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

CI runs it in four projects — 1440, 768, 390 and 320 px — against the Compose
development runtime. That run uses `config.settings.local`, because the browser
drives the application over plain HTTP on loopback and the production settings
force an HTTPS redirect and secure cookies. The separate production-settings
Compose smoke test is unchanged and still covers that configuration.

## Dependency audit

CI runs `npm audit --audit-level=high` and reports its result without failing the
build and without applying automatic major-version upgrades. Findings are
reviewed and upgraded deliberately.
