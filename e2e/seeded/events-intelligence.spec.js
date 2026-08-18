import { expect, test } from "@playwright/test";

import {
  expectNoHorizontalOverflow,
  signIn,
  watchConsole,
} from "../helpers.js";

/*
 * Sündmused, in one scroll since 2026-08-18.
 *
 * `Ülevaade`, `Maht ja kalender` and `Formaadid ja teemad` were three focus
 * views chosen by `?fookus=` before that; all three folded onto one page, and
 * `fookus` is not read at all any more. `e2e/seeded/events.spec.js` drives the
 * programme register, which folded onto the page a round earlier, on
 * 2026-08-17, and renders on every test in this file too. This file exists
 * because two classes of defect on this dashboard are invisible to every
 * value-inspecting test in the Python suite:
 *
 *   - a page that renders every section and loads no chart JavaScript, because a
 *     context key was renamed in one place and not the other. Django resolves the
 *     missing variable to falsy, the rest of the page renders perfectly, and no
 *     assertion about values notices;
 *   - horizontal overflow, which has shipped four times in this repository — each
 *     time from an `sr-only` note or a wide table escaping its container — and is
 *     only ever visible at a width somebody actually looked at.
 *
 * Every figure and name on this page is synthetic.
 */

const PAGE = "/sundmused/";

/* Assertions that do not depend on the viewport run once. */
const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

async function open_(page) {
  await signIn(page);
  await page.goto(`${PAGE}?year=all`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sündmused");
}

test("the page renders every section that folded into it", async ({ page }) => {
  oncePerRun();
  const errors = watchConsole(page);

  await open_(page);

  /* `exact` matters: Playwright matches an accessible name by substring by
     default. One heading per section that used to live behind its own
     focus — `Maht ja kalender`'s two charts, `Formaadid ja teemad`'s
     delivery-over-time chart, and the type/mode breakdown that always lived
     on `Ülevaade`. */
  for (const heading of [
    "Maht aastate lõikes",
    "Ürituse tüüp",
    "Hinnastruktuur",
    "Toimumisviis aastate lõikes",
    "Järgmised sündmused",
    "Praegu enim vaadatud tulevased sündmused",
  ]) {
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
  }

  /* There is no focus navigation left to mark a chip current on. */
  await expect(page.locator('nav[aria-label="Sündmuste vaated"]')).toHaveCount(
    0,
  );
  expect(errors).toEqual([]);
});

test("a stray focus parameter from an old bookmark still opens the page", async ({
  page,
}) => {
  oncePerRun();
  await signIn(page);
  const response = await page.goto(`${PAGE}?fookus=ei-ole-olemas`);

  expect(response.status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "Ürituse tüüp", exact: true }),
  ).toBeVisible();
});

test("the page actually draws its charts", async ({ page }) => {
  oncePerRun();
  const errors = watchConsole(page);

  await open_(page);

  /* The canvas is filled by the bundle. An empty one is exactly what a page
     that shipped no chart JavaScript looks like, and it is silent. */
  const canvas = page.locator("[data-chart-canvas]").first();
  await expect(canvas).toBeVisible();
  await expect(canvas.locator("canvas, svg").first()).toBeVisible({
    timeout: 15000,
  });
  expect(errors).toEqual([]);
});

test("every chart names itself for a reader who cannot see the canvas", async ({
  page,
}) => {
  oncePerRun();
  await open_(page);

  /* The accessible data table left every chart on 2026-08-17. `chart.summary`,
     the canvas's own `aria-label`, is what is left to reach these charts by
     keyboard or by screen reader — neither tooltip does either. */
  const figures = page.locator("figure[data-chart]");
  const count = await figures.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await expect(
      figures.nth(index).locator("[data-chart-canvas]"),
    ).toHaveAttribute("aria-label", /.+/);
  }
});

test("the provenance block is not on the page, and is on /haldus/", async ({
  page,
}) => {
  oncePerRun();
  await open_(page);

  /* It was folded away at the foot of every focus until 2026-08-15, when the
     board moved it to Admin. Both halves are checked here: gone from the
     page, and actually rendered where it went — deleting it from one page
     and never wiring it into the other would satisfy the first half alone. */
  await expect(
    page.locator('section[aria-labelledby="section-quality"]'),
  ).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("Andmete kohta");

  await page.goto("/haldus/");
  // Scoped to Sündmused' own block by id: `Andmeallikad ja import` holds more
  // than one domain's `<details>` since Õigusloome joined it on 2026-08-17,
  // so counting every `<details>` in the shared section counts the wrong thing.
  const details = page.locator("#sundmused-andmeallikad");
  await expect(details).toHaveCount(1);
  // Still folded: Admin is where the diagnostics live, not where they shout.
  await expect(details).not.toHaveAttribute("open", /.*/);

  await details.locator("summary").click();
  await expect(
    page.getByRole("heading", {
      name: "Mida need andmed ei tõesta",
      exact: true,
    }),
  ).toBeVisible();
});

test("the page claims no attendance, capacity or satisfaction", async ({
  page,
}) => {
  oncePerRun();
  await open_(page);

  const text = await page.locator("main").innerText();
  for (const forbidden of [
    "Osalejaid",
    "Kohal käinud",
    "Täitumus",
    "Vabu kohti",
    "Rahulolu",
  ]) {
    expect(text).not.toContain(forbidden);
  }
});

/* -- responsive ------------------------------------------------------------ */

test("the page does not scroll sideways", async ({ page }) => {
  await open_(page);
  await expectNoHorizontalOverflow(page);
});
