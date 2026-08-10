import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "../helpers.js";

/*
 * The Liikmeskond analytics, against seeded content.
 *
 * These are the assertions the unit tests cannot make: that a chart actually
 * mounts, that a tooltip appears and says something a person can read, and that
 * a control changes what is drawn. The server-side contracts — which points
 * exist, what a comparison refuses — are pinned in `tests/membership/`; this
 * file is about whether any of it reaches the screen.
 */

const PAGE = "/liikmeskond/";

/*
 * Chart behaviour does not depend on the viewport, so the interaction tests run
 * once rather than six times. The overflow and mobile tests are the ones that
 * need every width.
 */
const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

async function open_(page, query = "") {
  await signIn(page);
  await page.goto(`${PAGE}${query}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

/** Every mounted chart canvas, which ECharts fills with a <canvas> element. */
const canvases = (page) => page.locator("[data-chart-canvas] canvas");

/** Hover the middle of a chart and return whatever tooltip text appears. */
async function tooltipText(page, index = 0) {
  const canvas = canvases(page).nth(index);
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const tooltip = page.locator(".dk-chart-tooltip").first();
  await expect(tooltip).toBeVisible({ timeout: 5000 });
  return (await tooltip.innerText()).trim();
}

test("every chart mounts with real dimensions and no console error", async ({ page }) => {
  oncePerRun();
  const errors = watchConsole(page);

  await open_(page);

  await expect(canvases(page).first()).toBeVisible();
  const count = await canvases(page).count();
  expect(count).toBeGreaterThan(0);

  for (let index = 0; index < count; index += 1) {
    const box = await canvases(page).nth(index).boundingBox();
    expect(box.width).toBeGreaterThan(0);
    expect(box.height).toBeGreaterThan(0);
  }
  expect(errors).toEqual([]);
});

test("the four analytical sections are separate and named", async ({ page }) => {
  oncePerRun();
  await open_(page);

  // `Liikmeskonna areng` is deliberately absent from this list: its heading was
  // struck out on the board's print-out and is now `sr-only`, so it names the
  // landmark without being drawn. The other three are still visible headings.
  for (const title of [
    "Liikmemaksu laekumine",
    "Uute liikmete dünaamika",
    "Liikmete liikumine",
  ]) {
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }
  // Still a heading, still the section's accessible name, just not painted.
  await expect(page.getByRole("heading", { name: "Liikmeskonna areng" })).toHaveCount(1);
  // The single range control that governed only some of the charts is gone.
  await expect(page.getByRole("heading", { name: "Ajaloolised trendid" })).toHaveCount(0);
});

test("a tooltip appears and states formatted Estonian figures", async ({ page }) => {
  oncePerRun();
  await open_(page);

  const text = await tooltipText(page);

  // A grouped thousand, and no ISO date anywhere in the readout.
  expect(text).toMatch(/\d \d{3}/);
  expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}/);
});

test("the size-movement tooltip never states a departure as a negative", async ({ page }) => {
  oncePerRun();
  /*
   * The defect this chart shipped with: the removed count is negated so the bar
   * extends leftwards, and that geometry used to reach the reader as
   * `Lahkunud: -11`. Asserted in the browser as well as on the server, because
   * the server value being right is only half of it — the browser must show
   * that value and not the one it drew.
   */
  await open_(page);

  const heading = page.getByRole("heading", { name: "Liikmete liikumine" });
  await expect(heading).toBeVisible();

  const section = page.locator('section[aria-labelledby="section-movement"]');
  const canvas = section.locator("[data-chart-canvas] canvas").first();
  const box = await canvas.boundingBox();
  // The left half is where departures are drawn.
  await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.3);

  const tooltip = page.locator(".dk-chart-tooltip").first();
  await expect(tooltip).toBeVisible({ timeout: 5000 });
  const text = await tooltip.innerText();

  expect(text).not.toMatch(/Lahkunud[\s\S]{0,12}[-−]\d/);
});

test("a range preset redraws the growth chart and marks itself active", async ({ page }) => {
  oncePerRun();
  await open_(page);

  const section = page.locator('section[aria-labelledby="section-growth"]');
  const presets = section.getByRole("link").filter({ hasText: /aasta|Kõik/ });
  expect(await presets.count()).toBeGreaterThan(0);

  const whole = presets.filter({ hasText: "Kõik" }).first();
  await whole.click();

  await expect(page).toHaveURL(/alates=\d{4}-\d{2}-\d{2}&kuni=\d{4}-\d{2}-\d{2}/);
  await expect(
    page.locator('section[aria-labelledby="section-growth"] [aria-current="true"]'),
  ).toHaveText("Kõik");
});

test("the monthly and cumulative views draw different data", async ({ page }) => {
  oncePerRun();
  await open_(page);

  const section = page.locator('section[aria-labelledby="section-recruitment"]');
  await expect(section).toBeVisible();

  const payload = async () =>
    page.locator("#internal-membership-monthly").evaluate((node) => node.textContent);

  const monthly = await payload();
  await section.getByRole("link", { name: "Kumulatiivselt" }).click();
  await expect(page).toHaveURL(/vaade=kumulatiivne/);
  const cumulative = await payload();

  expect(cumulative).not.toEqual(monthly);
  await expect(
    page.locator('section[aria-labelledby="section-recruitment"] [aria-current="true"]').first(),
  ).toHaveText("Kumulatiivselt");
});

test("a control link carries only resolved parameters", async ({ page }) => {
  oncePerRun();
  /*
   * The links are built from values the view has already validated, so a stale
   * key handed to the page is not carried forward into them.
   */
  await open_(page, "?vahemik=99&vaade=onbekend");

  const href = await page
    .locator('section[aria-labelledby="section-recruitment"] a[href*="vaade="]')
    .first()
    .getAttribute("href");

  expect(href).not.toContain("vahemik");
  expect(href).not.toContain("onbekend");
});

test("every chart keeps its data table alongside the drawing", async ({ page }) => {
  oncePerRun();
  await open_(page);

  const figures = await page.locator("[data-chart-payload]").count();
  const tables = await page.locator("[data-chart-table]").count();

  expect(figures).toBeGreaterThan(0);
  expect(tables).toEqual(figures);
});

test("charts still render with reduced motion requested", async ({ page }) => {
  oncePerRun();
  await page.emulateMedia({ reducedMotion: "reduce" });
  const errors = watchConsole(page);

  await open_(page);

  await expect(canvases(page).first()).toBeVisible();
  expect(errors).toEqual([]);
});

/* -- every width ----------------------------------------------------- */

test("the analytics page never scrolls sideways", async ({ page }) => {
  await open_(page);

  await expect(canvases(page).first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("charts keep a readable height at every width", async ({ page }) => {
  await open_(page);

  const box = await canvases(page).first().boundingBox();

  expect(box.height).toBeGreaterThanOrEqual(200);
  expect(box.width).toBeLessThanOrEqual(await page.evaluate(() => document.documentElement.clientWidth));
});

test("section controls wrap rather than pushing the page sideways", async ({ page }) => {
  await open_(page);

  const escaping = await page
    .locator("section a.dk-chip")
    .evaluateAll(
      (nodes) =>
        nodes.filter(
          (node) => node.getBoundingClientRect().right > document.documentElement.clientWidth + 1,
        ).length,
    );

  expect(escaping).toBe(0);
  await expectNoHorizontalOverflow(page);
});
