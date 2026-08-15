import { expect, test } from "@playwright/test";

import {
  expectNoHorizontalOverflow,
  signIn,
  watchConsole,
} from "../helpers.js";

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
 * The page is four focused views behind one URL, so a test has to say which
 * one it is asserting about. `fookus` is an ordinary GET parameter and every
 * control is a link, which is what lets these navigate by URL rather than by
 * clicking through a client-side router. `liikmemaks` retired on 2026-08-16:
 * its one chart draws on the overview, and the key resolves there.
 */
const GROWTH = "?fookus=kasv";
const RETIRED_FEES = "?fookus=liikmemaks";
const MOVEMENT = "?fookus=liikumine";
const COMPOSITION = "?fookus=koosseis";
const REGISTER = "?fookus=nimekiri";

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

test("every chart mounts with real dimensions and no console error", async ({
  page,
}) => {
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

test("each focus draws its own analysis and names itself", async ({ page }) => {
  oncePerRun();
  await open_(page);

  // The overview leads with the four headline answers and the trend, and does
  // not carry the deeper sections at all.
  await expect(
    page.getByRole("heading", { name: "Peamised näitajad" }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "Uute liikmete dünaamika" }),
  ).toHaveCount(0);

  // Each named focus carries its own section, and only when navigated to.
  for (const [query, title] of [
    [GROWTH, "Uute liikmete dünaamika"],
    [MOVEMENT, "Liikmete liikumine"],
  ]) {
    await page.goto(`${PAGE}${query}`);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }

  // The fee history draws on the overview since `liikmemaks` retired, and the
  // retired key still lands on it. Located by its payload id: the chart title
  // is a span rather than a heading, and the KPI strip carries the same words.
  for (const query of ["", RETIRED_FEES]) {
    await page.goto(`${PAGE}${query}`);
    await expect(page.locator('[data-chart-payload="internal-membership-fees"]')).toBeVisible();
  }

  // Still a heading, still the section's accessible name, just not painted.
  await page.goto(PAGE);
  await expect(
    page.getByRole("heading", { name: "Liikmeskonna areng" }),
  ).toHaveCount(1);
  // The single range control that governed only some of the charts is gone.
  await expect(
    page.getByRole("heading", { name: "Ajaloolised trendid" }),
  ).toHaveCount(0);
});

test("an unknown focus renders the overview rather than an error", async ({
  page,
}) => {
  oncePerRun();
  /*
   * A stale bookmark or a typed URL must render the page. A 404 for a mistyped
   * query value would punish a reader for a link somebody else wrote.
   *
   * Signed in first: without a session the path redirects to the login form,
   * and a 200 from *that* would prove nothing about the focus at all.
   */
  await signIn(page);
  const response = await page.goto(`${PAGE}?fookus=koosseiss`);

  expect(response.status()).toBe(200);
  // The overview's own landmark, which the other focuses do not draw. Located
  // by its section rather than by its heading: the heading is `sr-only`, and
  // asserting visibility on a one-pixel clipped element tests the clipping
  // technique rather than the page.
  await expect(
    page.locator('section[aria-labelledby="section-headlines"]'),
  ).toHaveCount(1);
});

test("the composition view states the date it describes", async ({ page }) => {
  oncePerRun();
  /*
   * A roster export is a reading taken on one day. Saying so before any chart
   * is reached is what stops the whole view being read as "the membership right
   * now", and stops its row count being mistaken for a membership total.
   */
  await open_(page, COMPOSITION);

  await expect(page.getByText(/Koosseis seisuga/i)).toBeVisible();
  await expect(
    page.getByText(/ei ole sama mis juhatuse aruande liikmete arv/i),
  ).toBeVisible();
  await expect(canvases(page).first()).toBeVisible();
});

test("the joining-year chart refuses to be read as retention", async ({
  page,
}) => {
  oncePerRun();
  /*
   * The roster holds only members who are still here, so every cohort is seen
   * through its survivors. Nothing records who left, and the footnote has to
   * say so on the page rather than only in the code.
   */
  await open_(page, COMPOSITION);

  await expect(page.getByText(/ei ole püsimamäär/i)).toBeVisible();
});

test("a tooltip appears and states formatted Estonian figures", async ({
  page,
}) => {
  oncePerRun();
  await open_(page);

  const text = await tooltipText(page);

  // A grouped thousand, and no ISO date anywhere in the readout.
  expect(text).toMatch(/\d \d{3}/);
  expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}/);
});

test("the size-movement tooltip never states a departure as a negative", async ({
  page,
}) => {
  oncePerRun();
  /*
   * The defect this chart shipped with: the removed count is negated so the bar
   * extends leftwards, and that geometry used to reach the reader as
   * `Lahkunud: -11`. Asserted in the browser as well as on the server, because
   * the server value being right is only half of it — the browser must show
   * that value and not the one it drew.
   */
  await open_(page, MOVEMENT);

  const heading = page.getByRole("heading", { name: "Liikmete liikumine" });
  await expect(heading).toBeVisible();

  const section = page.locator('section[aria-labelledby="section-movement"]');
  const canvas = section.locator("[data-chart-canvas] canvas").first();
  // Scrolled into view first. `toBeVisible` does not scroll — an element below
  // the fold satisfies it — and `boundingBox` reports viewport coordinates, so
  // without this the mouse was sent to a y outside the viewport and never
  // reached the chart at all. The page grew past the fold and the hover has
  // been landing nowhere since.
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  // The left half is where departures are drawn, and the middle of the plotting
  // area rather than its top edge, which on a four-class chart is axis margin.
  await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.5);

  const tooltip = page.locator(".dk-chart-tooltip").first();
  await expect(tooltip).toBeVisible({ timeout: 5000 });
  const text = await tooltip.innerText();

  expect(text).not.toMatch(/Lahkunud[\s\S]{0,12}[-−]\d/);
});

test("a range preset redraws the trend and keeps the reader on its focus", async ({
  page,
}) => {
  oncePerRun();
  /*
   * The preset has to carry `fookus` forward. Without it a preset clicked on
   * any focus but the first drops the reader back to the overview, so the
   * control appears to navigate away from the chart it governs.
   */
  await open_(page, GROWTH);

  const section = page.locator('section[aria-labelledby="section-stock"]');
  const presets = section.getByRole("link").filter({ hasText: /aasta|Kõik/ });
  expect(await presets.count()).toBeGreaterThan(0);

  const whole = presets.filter({ hasText: "Kõik" }).first();
  await whole.click();

  await expect(page).toHaveURL(
    /alates=\d{4}-\d{2}-\d{2}&kuni=\d{4}-\d{2}-\d{2}/,
  );
  await expect(page).toHaveURL(/fookus=kasv/);
  await expect(
    page.getByRole("heading", { name: "Uute liikmete dünaamika" }),
  ).toBeVisible();
  await expect(
    page.locator(
      'section[aria-labelledby="section-stock"] [aria-current="true"]',
    ),
  ).toHaveText("Kõik");
});

test("the monthly and cumulative views draw different data", async ({
  page,
}) => {
  oncePerRun();
  await open_(page, GROWTH);

  const section = page.locator(
    'section[aria-labelledby="section-recruitment"]',
  );
  await expect(section).toBeVisible();

  const payload = async () =>
    page
      .locator("#internal-membership-monthly")
      .evaluate((node) => node.textContent);

  const monthly = await payload();
  await section.getByRole("link", { name: "Kumulatiivselt" }).click();
  await expect(page).toHaveURL(/vaade=kumulatiivne/);
  const cumulative = await payload();

  expect(cumulative).not.toEqual(monthly);
  await expect(
    page
      .locator(
        'section[aria-labelledby="section-recruitment"] [aria-current="true"]',
      )
      .first(),
  ).toHaveText("Kumulatiivselt");
});

test("a control link carries only resolved parameters", async ({ page }) => {
  oncePerRun();
  /*
   * The links are built from values the view has already validated, so a stale
   * key handed to the page is not carried forward into them.
   */
  await open_(page, "?fookus=kasv&vahemik=99&vaade=onbekend");

  const href = await page
    .locator('section[aria-labelledby="section-recruitment"] a[href*="vaade="]')
    .first()
    .getAttribute("href");

  expect(href).not.toContain("vahemik");
  expect(href).not.toContain("onbekend");
});

test("every chart keeps its data table alongside the drawing", async ({
  page,
}) => {
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
  expect(box.width).toBeLessThanOrEqual(
    await page.evaluate(() => document.documentElement.clientWidth),
  );
});

test("section controls wrap rather than pushing the page sideways", async ({
  page,
}) => {
  await open_(page);

  const escaping = await page
    .locator("section a.dk-chip")
    .evaluateAll(
      (nodes) =>
        nodes.filter(
          (node) =>
            node.getBoundingClientRect().right >
            document.documentElement.clientWidth + 1,
        ).length,
    );

  expect(escaping).toBe(0);
  await expectNoHorizontalOverflow(page);
});

/*
 * Board-decision batches.
 *
 * The section exists only because the seed now creates batches; before that it
 * was invisible here, which is the same blind spot that hid the website-traffic
 * section until it was seeded. A green run proves the parts work, not that
 * anything reaches them.
 */
test("the decision section is drawn and keeps itself apart from year-to-date", async ({
  page,
}) => {
  oncePerRun();

  await open_(page, MOVEMENT);

  const section = page
    .locator("#section-decisions")
    .locator("xpath=ancestor::section[1]");
  await expect(section).toBeVisible();
  await expect(section.getByText("Juhatuse otsused")).toBeVisible();

  // The caveat has to be on the page, not only in the code: a batch is one
  // decision's own list and is not addable to a year-to-date figure.
  await expect(
    section.getByText(/ei ole aasta algusest kogunenud arv/i),
  ).toBeVisible();

  // Both of its charts mount with real dimensions.
  const drawn = section.locator("[data-chart-canvas] canvas");
  const count = await drawn.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const box = await drawn.nth(index).boundingBox();
    expect(box.width).toBeGreaterThan(0);
    expect(box.height).toBeGreaterThan(0);
  }
});

test("a decision chart names both of its dates", async ({ page }) => {
  oncePerRun();

  await open_(page, MOVEMENT);

  const section = page
    .locator("#section-decisions")
    .locator("xpath=ancestor::section[1]");
  // The appendix is compiled on one day and signed on another; a label that
  // collapsed them would hide which day a figure describes.
  await expect(section.getByText(/seisuga/i).first()).toBeVisible();
  await expect(section.getByText(/otsus/i).first()).toBeVisible();
});

test("the decision section does not make the page scroll sideways", async ({
  page,
}) => {
  await open_(page, MOVEMENT);

  await expect(page.locator("#section-decisions")).toBeAttached();
  await expectNoHorizontalOverflow(page);
});

/*
 * The members list.
 *
 * The one focus that prints rows rather than drawing them, so what has to be
 * checked here is different: that the list says which day it describes, that
 * its controls are ordinary GETs, and that a wide table does not take the
 * document sideways with it.
 */
test("the members list states the date it describes and lists members", async ({
  page,
}) => {
  oncePerRun();

  await open_(page, REGISTER);

  await expect(
    page.getByRole("heading", { name: "Liikmete nimekiri" }),
  ).toBeVisible();
  // A members list rendered without its date reads as current, and this one is
  // a manual export that ages between imports.
  await expect(page.getByText(/Nimekiri seisuga/i)).toBeVisible();
  await expect(page.getByText(/ei ole liikmete arvu näitaja/i)).toBeVisible();
  await expect(page.getByRole("row").nth(1)).toBeVisible();
});

test("searching narrows the list through an ordinary GET", async ({ page }) => {
  oncePerRun();
  /*
   * No client state and no SPA: a search is a request the browser can bookmark,
   * share and go back through, and it must keep the reader on the focus that
   * carries the control.
   */
  await open_(page, REGISTER);

  const before = await page.getByRole("row").count();
  await page.getByRole("searchbox").fill("Näidisettevõte 02");
  await page.getByRole("button", { name: "Otsi" }).click();

  await expect(page).toHaveURL(/fookus=nimekiri/);
  await expect(page).toHaveURL(/otsing=/);
  expect(await page.getByRole("row").count()).toBeLessThan(before);
});

test("the pager moves through the list and keeps the focus", async ({
  page,
}) => {
  oncePerRun();
  await open_(page, REGISTER);

  const nav = page.getByRole("navigation", { name: "Lehed" });
  await expect(nav).toBeVisible();
  await nav.getByRole("link", { name: /Järgmine/ }).click();

  await expect(page).toHaveURL(/leht=2/);
  await expect(page).toHaveURL(/fookus=nimekiri/);
  await expect(page.getByText(/Lehekülg 2/)).toBeVisible();
});

test("the two sources are compared without producing one merged total", async ({
  page,
}) => {
  oncePerRun();
  /*
   * The rule the whole page is built on. The comparison may state what each
   * source knows and where they differ; it may not state a corrected
   * membership number, because no measurement here produces one.
   */
  await open_(page, REGISTER);

  const section = page
    .locator("#section-register-comparison")
    .locator("xpath=ancestor::section[1]");
  await expect(section).toBeVisible();
  await expect(section.getByText("Mõlemas allikas")).toBeVisible();
  await expect(section.getByText("Ainult nimekirjas")).toBeVisible();
  await expect(section.getByText("Ainult kataloogis")).toBeVisible();
  await expect(
    section.getByText(/ei ole kummagi allika viga ega anna parandatud/i),
  ).toBeVisible();
});

test("the members list never scrolls the page sideways", async ({ page }) => {
  /*
   * A seven-column table at 320px is exactly the shape that has taken this
   * document sideways before. The table scrolls inside its own wrapper; the
   * page does not.
   */
  await open_(page, REGISTER);

  await expect(page.getByRole("table")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
