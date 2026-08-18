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
 * The page is three focused views behind one URL, so a test has to say which
 * one it is asserting about. `fookus` is an ordinary GET parameter and every
 * control is a link, which is what lets these navigate by URL rather than by
 * clicking through a client-side router.
 *
 * `liikmemaks` retired on 2026-08-16, its one chart onto the overview.
 * `liikumine` and `koosseis` retired on 2026-08-17: `liikumine` merged into
 * `kasv`, which took its content and the new name `Sisse-välja`; `koosseis`
 * mostly landed on the overview, but two of its charts — the joining-year
 * chart and the growth-index chart — followed `liikumine` into `kasv`
 * instead. See `RETIRED_FOCUSES` in `apps/membership/focus.py`.
 */
const GROWTH = "?fookus=kasv";
const RETIRED_FEES = "?fookus=liikmemaks";
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
  // Scrolled into view first, same reason `the size-movement tooltip` test
  // below does it explicitly: `boundingBox` reports viewport coordinates and
  // `mouse.move` does not scroll, so a canvas below the fold sends the mouse
  // nowhere. `Sel aastal` moving up the page on 2026-08-17 pushed the trend
  // chart — this helper's usual index-0 target — past the fold on `desktop`.
  await canvas.scrollIntoViewIfNeeded();
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

  // `Sisse-välja` carries both sections since the 2026-08-17 merge — the
  // recruitment dynamics and the movement they used to sit under two
  // separate tabs for — and neither is offered on the overview.
  await page.goto(`${PAGE}${GROWTH}`);
  for (const title of ["Uute liikmete dünaamika", "Liikmete liikumine"]) {
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }

  // The fee history draws on the overview since `liikmemaks` retired, and the
  // retired key still lands on it. Located by its payload id: the chart title
  // is a span rather than a heading, and the KPI strip carries the same words.
  for (const query of ["", RETIRED_FEES]) {
    await page.goto(`${PAGE}${query}`);
    await expect(page.locator('[data-chart-payload="internal-membership-fees"]')).toBeVisible();
  }

  // The two time series are sections of their own since 2026-08-18, each
  // named by what it draws and carrying its own controls. `Liikmeskonna areng`
  // was the sr-only name of the one section that held both.
  await page.goto(PAGE);
  for (const title of ["Liikmete arv ja tasunud liikmed", "Liikmemaksu laekumine"]) {
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }
  for (const gone of ["Liikmeskonna areng", "Ajaloolised trendid", "Mis muutus?"]) {
    await expect(page.getByRole("heading", { name: gone })).toHaveCount(0);
  }
});

test("the strip carries four cells and the year's movement is one of them", async ({
  page,
}) => {
  oncePerRun();
  /*
   * `Sel aastal` was a section below the strip until 2026-08-18, which put the
   * year's arrivals a scroll from the total they move and left the strip a
   * column short. The three counts are inside the strip now, each with its own
   * comparison where the report supports one.
   */
  await open_(page);

  const strip = page.locator('section[aria-labelledby="section-headlines"]');
  await expect(strip).toBeVisible();
  for (const label of [
    "Liikmeid kokku",
    "Tasunud liikmeid",
    "Liikmemaksu laekumine",
    "Sel aastal",
  ]) {
    await expect(strip.getByText(label, { exact: false }).first()).toBeVisible();
  }
  for (const word of ["liitunud", "väljaarvatud", "peatatud"]) {
    await expect(strip.getByText(word, { exact: true }).first()).toBeVisible();
  }
});

test("the range control sits on the heading row of the chart it governs", async ({
  page,
}) => {
  oncePerRun();
  /*
   * It sat above two charts and governed both, which was true and read as a
   * page-wide control. The trend is its own section now and the chips are on
   * that section's heading row; the fee section beside it has its own four
   * figures there instead.
   */
  await open_(page);

  const trend = page.locator('section[aria-labelledby="section-trend"]');
  await expect(trend.getByRole("group", { name: "Periood" })).toBeVisible();
  await expect(
    page.locator('section[aria-labelledby="section-fees"]').getByRole("group", { name: "Periood" }),
  ).toHaveCount(0);
  // The strip states both figures already, so the trend chart drops the
  // readouts that repeated them.
  await expect(trend.getByText("Liikmeid kokku")).toHaveCount(0);
});

test("the composition charts carry their own facts as subtitles", async ({
  page,
}) => {
  oncePerRun();
  /*
   * `Kes on meie liikmed?` was four readouts above four charts drawing the same
   * four dimensions. One section since 2026-08-18: each chart states its own
   * largest group — or, for tenure, the median — on the drawing that proves it.
   */
  await open_(page);

  const structure = page.locator('section[aria-labelledby="section-structure"]');
  await expect(structure.getByRole("heading", { name: "Kes on meie liikmed?" })).toBeVisible();
  await expect(structure.getByText(/suurim:/).first()).toBeVisible();
  await expect(structure.getByText(/mediaan/).first()).toBeVisible();
  // The retired four-fact strip.
  await expect(page.getByText("Suurim piirkond")).toHaveCount(0);
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

test("the composition distributions no longer state the date they describe inline", async ({
  page,
}) => {
  oncePerRun();
  /*
   * `Koosseisu ulatus` — the as-of date and row-count sentence that used to
   * open the retired composition focus — left on 2026-08-17 along with the
   * focus itself; its distributions joined the overview the same day, right
   * below `Kes on meie liikmed?`. Every composition chart still carries the
   * same date in its own observation label, so the view lost a repeated
   * sentence, not the information.
   */
  await open_(page, "");

  await expect(page.getByText(/Koosseis seisuga/i)).toHaveCount(0);
  await expect(
    page.getByText(/ei ole sama mis juhatuse aruande liikmete arv/i),
  ).toHaveCount(0);
  await expect(
    page.locator('section[aria-labelledby="section-structure"]'),
  ).toBeVisible();
});

test("the joining-year chart no longer states its retention caveat inline", async ({
  page,
}) => {
  oncePerRun();
  /*
   * The chart itself, and the fact that it counts survivors rather than
   * joiners, is unchanged — only the footnote spelling that out left the
   * page on 2026-08-17, the same day the chart followed `liikumine` from the
   * retired composition focus into `Sisse-välja`.
   */
  await open_(page, GROWTH);

  await expect(page.getByText(/ei ole püsimamäär/i)).toHaveCount(0);
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
  await open_(page, GROWTH);

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

test("every chart names itself for a reader who cannot see the canvas", async ({
  page,
}) => {
  oncePerRun();
  await open_(page);

  // The accessible data table left every chart on 2026-08-17. What is left is
  // `chart.summary`, rendered as the canvas's own `aria-label`.
  const canvases_ = page.locator("[data-chart-canvas]");
  const count = await canvases_.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const label = await canvases_.nth(index).getAttribute("aria-label");
    expect(label?.trim()).toBeTruthy();
  }
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
 * `Juhatuse otsused` — the section, its picker and both decision-scoped
 * charts — left this focus on 2026-08-17. The selector and chart-builder
 * functions it drew from are still covered directly in
 * `tests/membership/test_decision_batch_presentation.py`; this is what's left
 * to check at the page level, that a batch existing no longer surfaces any of
 * it here.
 */
test("the decision section no longer renders even when a batch exists", async ({
  page,
}) => {
  oncePerRun();

  await open_(page, GROWTH);

  await expect(page.locator("#section-decisions")).toHaveCount(0);
  await expect(page.getByText("Juhatuse otsused")).toHaveCount(0);
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
  //
  // `\s+` rather than a literal space, everywhere a phrase can span a template
  // line break. `getByText` normalizes whitespace for a *string*, but a regex
  // is tested against the text as rendered, so a sentence the template happens
  // to wrap stops matching — and rewrapping a paragraph must not fail a test
  // about what the page says.
  await expect(page.getByText(/Nimekiri\s+seisuga/i)).toBeVisible();
  await expect(
    page.getByText(/ei\s+ole\s+liikmete\s+arvu\s+näitaja/i),
  ).toBeVisible();
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

  // Each difference appears twice on purpose — as a count in the summary list
  // and as a heading over the members themselves — so each is asserted at the
  // place it belongs rather than with a bare text match, which resolves to two
  // elements and fails strict mode.
  const counts = section.locator("dl");
  await expect(counts.getByText("Mõlemas allikas")).toBeVisible();
  await expect(counts.getByText("Ainult nimekirjas")).toBeVisible();
  await expect(counts.getByText("Ainult kataloogis")).toBeVisible();
  await expect(counts.getByText("Kataloogis kokku")).toBeVisible();
  await expect(
    section.getByRole("heading", { name: "Ainult nimekirjas" }),
  ).toBeVisible();
  // The heading is wordier than the count label above it on purpose, and the
  // two must not be assumed identical: "Ainult kataloogis" is the count, while
  // the list over the members themselves says which catalogue.
  await expect(
    section.getByRole("heading", { name: "Ainult avalikus kataloogis" }),
  ).toBeVisible();

  await expect(
    section.getByText(
      /ei\s+ole\s+kummagi\s+allika\s+viga\s+ega\s+anna\s+parandatud/i,
    ),
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
