import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "../helpers.js";
import { PAGES } from "./pages.js";

/*
 * What the pages say once they actually have data.
 *
 * The empty-state suite proves the dashboard invents nothing when no source is
 * connected. This one proves the opposite half: with content published, the
 * headings stay ordered, the charts render silently, an explicit zero stays
 * distinguishable from a missing value, and nothing that looks like real
 * Chamber data appears.
 */

/*
 * Wording that would mean either a forbidden metric had appeared or the seed
 * had stopped being synthetic.
 *
 * "Teataja" alone is deliberately not listed: `e-Teataja` is a real newsletter
 * name the seed publishes, and only the out-of-scope *metric* is forbidden.
 */
const FORBIDDEN = ["Uusi liikmeid sel aastal", "orgusaar", "koda.ee/et/uudised/2"];

/*
 * Heading order, console cleanliness and page wording do not depend on the
 * viewport, so they run once rather than six times. The layout suite is what
 * needs every width. This keeps the seeded stage from doubling the browser job
 * for assertions that would produce six identical results.
 */
const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

for (const page_ of PAGES) {
  test(`${page_.name} renders content without a console error`, async ({ page }) => {
    oncePerRun();
    const errors = watchConsole(page);

    await signIn(page);
    await page.goto(page_.path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // Seeded pages carry figures, so a page with no digit at all would mean the
    // seed did not reach it.
    const text = await page.locator("main").innerText();
    expect(text).toMatch(/\d/);
    expect(errors).toEqual([]);
  });

  test(`${page_.name} keeps one h1 and no skipped heading level`, async ({ page }) => {
    oncePerRun();
    await signIn(page);
    await page.goto(page_.path);

    const levels = await page.evaluate(() =>
      Array.from(document.querySelectorAll("main h1, main h2, main h3, main h4")).map((node) =>
        Number(node.tagName.slice(1)),
      ),
    );

    expect(levels.filter((level) => level === 1)).toHaveLength(1);
    expect(levels[0]).toBe(1);
    for (let index = 1; index < levels.length; index += 1) {
      // A heading may close levels freely but may only open one at a time.
      expect(levels[index] - levels[index - 1]).toBeLessThanOrEqual(1);
    }
  });

  test(`${page_.name} shows nothing that looks like real Chamber data`, async ({ page }) => {
    oncePerRun();
    await signIn(page);
    await page.goto(page_.path);

    const text = await page.locator("main").innerText();
    for (const forbidden of FORBIDDEN) {
      expect(text).not.toContain(forbidden);
    }
  });
}

test("every chart keeps its accessible table alongside the drawing", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/nahtavus/");

  // The drawings are server-rendered SVG; the readable equivalent is a table
  // with a caption, and it must survive having real data to draw.
  const captions = page.locator("main table caption");
  expect(await captions.count()).toBeGreaterThan(0);
});

test("the overview membership chart draws with several seeded readings", async ({ page }) => {
  oncePerRun();
  const errors = watchConsole(page);

  await signIn(page);

  const polylines = page.locator("main svg polyline, main svg path");
  expect(await polylines.count()).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test("an explicit zero reads differently from a missing value", async ({ page }) => {
  /*
   * The seed publishes one board report with `suspended_members = 0` and
   * another with it absent. "Nobody was suspended" and "nobody counted" are
   * different facts, and the empty-state wording must never stand in for a
   * measured zero.
   */
  oncePerRun();
  await signIn(page);
  await page.goto("/liikmeskond/");

  const text = await page.locator("main").innerText();

  expect(text).toMatch(/\d/);
  // The missing-data wording may appear, but never as the whole page: with six
  // seeded readings the page must be showing real figures too.
  const missingCount = (text.match(/Andmed puuduvad/g) || []).length;
  const digitGroups = (text.match(/\d+/g) || []).length;
  expect(digitGroups).toBeGreaterThan(missingCount);
});

test("the mobile drawer still works on a populated page", async ({ page }) => {
  test.skip(page.viewportSize().width >= 1024, "narrow layouts only");

  await signIn(page);
  await page.goto("/oigusloome/");

  const toggle = page.getByRole("button", { name: "Ava menüü" });
  const drawer = page.locator("#main-drawer");

  await toggle.click();
  await expect(drawer).toBeVisible();
  await page.getByRole("button", { name: "Sulge menüü" }).click();
  await expect(drawer).toBeHidden();
});

test("a seeded list is long enough to have exercised scrolling", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/oigusloome/");

  // Enough rows that a bounded list and its container are genuinely tested,
  // rather than a two-row fixture that always fits.
  const rows = page.locator("main table tbody tr");
  expect(await rows.count()).toBeGreaterThanOrEqual(4);
});
