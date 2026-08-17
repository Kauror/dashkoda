import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn } from "../helpers.js";

/*
 * Koduleht, against seeded analytics.
 *
 * This suite exists because the website analysis used to be invisible here. The
 * seed published no GA4 history and the page gated its whole traffic section on
 * the connection status, so every browser run — and every screenshot CI
 * uploaded — showed the `Lisamisel` empty state instead. Two defects shipped
 * through a fully green suite on 2026-08-11 in that blind spot: the view never
 * read the `otsing` query parameter, and the template hid the entire content
 * block, search box included, behind the ranking that searching empties on
 * purpose. Neither was reachable by any test.
 *
 * The assertions are therefore about reachability and about honesty — that the
 * controls appear, that using them changes what the page answers, that a
 * comparison the coverage cannot support is absent rather than drawn, and that
 * no view widens the document at 320 pixels.
 */

const KPI = 'section[aria-labelledby="section-kpi"]';
const SEARCH = 'section[aria-labelledby="section-otsing"]';
const MOVEMENT = 'section[aria-labelledby="section-liikumine"]';
const CHANGES = 'section[aria-labelledby="section-muutus"]';
// Koduleht's coverage disclosure lives on `/haldus/` since 2026-08-16, inside
// the sources section it shares with Sündmused' provenance block.
const METHOD = 'section[aria-labelledby="section-andmeallikad"]';

/*
 * Wording and behaviour that do not depend on the viewport run once rather than
 * six times, the same rule `content.spec.js` follows. The overflow test is the
 * deliberate exception: it is the one that has to see every width.
 */
const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

/**
 * The category label of every row a chart draws, read from its own
 * non-executable JSON payload rather than the accessible data table that
 * left every chart on 2026-08-17 — see `chart_figure.html`. `dashkoda.tooltip`
 * is keyed by row and every value carries `title`, the row's own label —
 * built the same way whether the chart draws one category-axis bar per row
 * or, like the composition charts, one stacked series per row on a single
 * shared category. Reading tooltip titles works either shape; `yAxis.data`
 * does not.
 */
const chartCategoryLabels = (page, payloadId) =>
  page.evaluate((id) => {
    const script = document.getElementById(id);
    if (!script) {
      return [];
    }
    const option = JSON.parse(script.textContent);
    const tooltip = (option.dashkoda && option.dashkoda.tooltip) || {};
    return Object.values(tooltip).map((row) => row.title);
  }, payloadId);

// ---------------------------------------------------------------------------
// Ülevaade
// ---------------------------------------------------------------------------

test("the overview answers before the reader touches anything", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  // If this fails, every other test in this file is passing vacuously.
  await expect(page.locator(KPI)).toBeVisible();
  for (const label of ["Kasutajad", "Külastused", "Lehevaatamised"]) {
    await expect(page.locator(KPI)).toContainText(label);
  }
  await expect(page.getByRole("heading", { name: "Perioodi muutus", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Kust tullakse", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Enim külastatud lehed", exact: true }),
  ).toBeVisible();
});

test("the seeded history gives every headline a comparison", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?periood=30");

  // Seventy seeded days is a complete thirty-day window and a complete one
  // before it, which is what the comparison requires. A rate moves in
  // percentage points and a count in percent, and the two must not be confused.
  // The strip is all counts since the engagement rate left it on 2026-08-16, so
  // the `pp` assertion follows the rate to `Perioodi muutus`.
  await expect(page.locator(KPI)).toContainText("%");
  await expect(page.locator(KPI)).not.toContainText("pp");
  await expect(page.locator(CHANGES)).toContainText("pp");
});

test("a period the history cannot fill is offered and inert", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  const periods = page.getByRole("navigation", { name: "Periood" });
  await expect(periods.getByRole("link", { name: "30 päeva" })).toBeVisible();
  // A reader who looks for `3 aastat` and finds no such control learns nothing;
  // one who finds it inert learns how long the Chamber has been measuring.
  await expect(periods.locator('[aria-disabled="true"]').first()).toBeVisible();
});

test("the chart bundle loads only where there is something to draw", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  const scripts = await page.evaluate(() =>
    Array.from(document.querySelectorAll("script[src]")).map((node) => node.getAttribute("src")),
  );
  expect(scripts.some((src) => src.includes("charts.js"))).toBe(true);
});

test("every chart names itself for a reader who cannot see the canvas", async ({
  page,
}) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu");

  // The accessible data table left every chart on 2026-08-17. What is left is
  // `chart.summary`, rendered as the canvas's own `aria-label` — a `role="img"`
  // with no label is an image nobody using a screen reader can read at all.
  const figures = page.locator("figure[data-chart]");
  const count = await figures.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const label = await figures
      .nth(index)
      .locator("[data-chart-canvas]")
      .getAttribute("aria-label");
    expect(label?.trim()).toBeTruthy();
  }
});

// ---------------------------------------------------------------------------
// Sisu
// ---------------------------------------------------------------------------

test("no language root or utility path occupies a ranking position", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=koik");

  const labels = await chartCategoryLabels(page, "koduleht-enim-vaadatud");
  expect(labels.length).toBeGreaterThan(0);

  /*
   * The seed gives `/et` more traffic than any article on purpose, which is how
   * the real property behaves — so without the exclusion registry these would
   * take the top of the list and this assertion is what notices.
   */
  for (const excluded of ["/et", "/en", "/ru", "/et/search/node", "/et/cart", "/403.html"]) {
    expect(labels, `${excluded} must not be ranked as content`).not.toContain(excluded);
  }
  // Nor a section's own listing page, which would otherwise top its own section.
  expect(labels).not.toContain("/et/uudised");
});

test("the excluded traffic still counts towards the site's own figures", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  // Excluded from a list, never from a total. A figure gone to zero would mean
  // the registry had been applied to the wrong query.
  const figures = await page.locator(KPI).locator("dd").allInnerTexts();
  expect(figures.some((value) => /[1-9]/.test(value))).toBe(true);
});

test("the content mix no longer states its denominator inline", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu");

  // The footnote and the chart's own question left the page on 2026-08-17.
  await expect(page.getByRole("heading", { name: "Vaadatud sisu jaotus" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("järjestatavaks sisuks");
});

test("the movement list finds the pages the seed made move", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=30");

  const movement = page.locator(MOVEMENT);
  await expect(movement).toContainText("Kasvavad lehed");
  await expect(movement).not.toContainText("Halvimad");
  await expect(movement).toContainText("sunteetiline-kasvav");

  // `Vähenenud tähelepanu`, the declining half, left the page on 2026-08-17.
  // `sunteetiline-vaibuv` was the seed's declining fixture; it rendered only
  // there and is gone with it.
  await expect(movement).not.toContainText("Vähenenud tähelepanu");
  await expect(movement).not.toContainText("sunteetiline-vaibuv");
});

test("a page with no previous measurement states that rather than a percentage", async ({
  page,
}) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=30");

  // It did not grow by 100% and it did not grow infinitely: there was no base.
  await expect(page.locator(MOVEMENT)).toContainText("uus mõõdetud liiklus");
});

test("the language split disclaims what it does not measure", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu");

  const labels = await chartCategoryLabels(page, "koduleht-keeled");
  expect(labels).toContain("Eesti");
  expect(labels).toContain("Inglise");
  await expect(page.locator("main")).toContainText("mitte külastaja rahvust");
});

test("the opportunity matrix names measurements rather than verdicts", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=30");

  await expect(page.getByRole("heading", { name: "Tähelepanu ja kaasatus" })).toBeVisible();

  // The quadrant name used to reach `main` through the accessible table's
  // `Rühm` column, which left every chart on 2026-08-17. Read from the same
  // payload the removed table's rows were built from instead.
  const rowValues = await page.evaluate(() => {
    const script = document.getElementById("koduleht-kaasatuse-maatriks");
    if (!script) {
      return [];
    }
    const option = JSON.parse(script.textContent);
    const tooltip = (option.dashkoda && option.dashkoda.tooltip) || {};
    return Object.values(tooltip).flatMap((entry) => entry.rows.map((row) => row.value));
  });
  expect(rowValues).toContain("Palju vaatamisi, lühem kaasatus");

  const main = page.locator("main");
  for (const verdict of ["hea sisu", "halb sisu", "ebaõnnestunud"]) {
    await expect(main).not.toContainText(verdict);
  }
});

test("no composite health score is invented", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  for (const focus of ["ulevaade", "liiklus", "sisu", "kanalid", "lehed"]) {
    await page.goto(`/koduleht/?fookus=${focus}`);
    const text = await page.evaluate(() => document.querySelector("main").innerText);
    expect(text).not.toMatch(/Health Score|Digital Score|Engagement Score|\d+\s*\/\s*100/);
  }
});

// ---------------------------------------------------------------------------
// Kanalid
// ---------------------------------------------------------------------------

test("channels are shown with the denominator their shares used", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=kanalid");

  await expect(
    page.getByRole("heading", { name: "Külastused kanalite kaupa" }),
  ).toBeVisible();
  const labels = await chartCategoryLabels(page, "koduleht-kanalid");
  expect(labels).toContain("Organic Search");
});

test("no source, medium or campaign detail is invented", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=kanalid");

  // Acquisition is stored at channel-group level only.
  const text = await page.evaluate(() => document.querySelector("main").innerText);
  expect(text).not.toMatch(/google \/ organic|utm_|Facebook referral/);
});

// ---------------------------------------------------------------------------
// Lehed
// ---------------------------------------------------------------------------

test("searching reaches a page the ranking does not show", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=koik");

  /*
   * Seeded almost silent, so it sits far below the ranking whatever else the
   * seed does. That is the whole point of search: the page somebody looks up is
   * the one a ranking cannot reach. The term appears in no path, so only the
   * news catalogue can produce this row — events and services are deliberately
   * uncatalogued and show their paths instead.
   */
  const wanted = "Sünteetiline uudise pealkiri 12";
  expect(await chartCategoryLabels(page, "koduleht-enim-vaadatud")).not.toContain(wanted);

  // Through the control itself, not through a hand-built URL — the parameter
  // never reaching the view is exactly the defect this file was written for.
  await page.goto("/koduleht/?fookus=lehed&periood=koik");
  await page.getByLabel("Lehe nimi või aadress").fill("pealkiri 12");
  await page.locator(SEARCH).getByRole("button", { name: "Otsi", exact: true }).click();

  await expect(page.locator(SEARCH)).toContainText(wanted);
  // And the box still holds what was typed, so the term stays visible.
  await expect(page.getByLabel("Lehe nimi või aadress")).toHaveValue("pealkiri 12");
});

test("a search that finds nothing keeps its box and offers the way back", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=lehed&otsing=puudubtaielikult");

  const section = page.locator(SEARCH);
  await expect(section).toContainText("Ühtegi lehte ei leitud");
  // The half of the defect that only appeared once a search actually ran: the
  // block hid itself, taking the search box and this link with it.
  await expect(page.getByLabel("Lehe nimi või aadress")).toBeVisible();
  await expect(section.getByRole("link", { name: "Tühjenda" })).toBeVisible();
});

test("the period travels with a search and survives clearing it", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=lehed&periood=90&otsing=sunteetiline");

  const active = () =>
    page.getByRole("navigation", { name: "Periood" }).locator("[aria-current]");
  await expect(active()).toHaveText(["90 päeva"]);

  // Clearing a search is not starting again: the reader still wants ninety days,
  // they have simply finished looking for one page.
  await page.locator(SEARCH).getByRole("link", { name: "Tühjenda" }).click();
  await expect(active()).toHaveText(["90 päeva"]);
  await expect(page.locator(SEARCH)).not.toContainText("Ühtegi lehte ei leitud");
});

test("results paginate and each page keeps the whole query", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  // Matches every seeded content path, so there are more results than one page.
  await page.goto("/koduleht/?fookus=lehed&periood=koik&otsing=sunteetiline");

  const section = page.locator(SEARCH);
  await expect(section).toContainText(/Lehekülg 1 \/ [2-9]/);

  await section.getByRole("link", { name: "Järgmine" }).click();
  await expect(section).toContainText(/Lehekülg 2 \//);
  await expect(page.getByLabel("Lehe nimi või aadress")).toHaveValue("sunteetiline");
  await expect(section.getByRole("link", { name: "Eelmine" })).toBeVisible();
});

test("one page opens its own analysis with both of its figures", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=lehed&periood=koik&otsing=pealkiri%2012");

  // Scoped to the results region, not to the section: the section's first link
  // is `Tühjenda` inside the form above the results.
  await page.locator("#koduleht-otsingutulemused").getByRole("link").first().click();

  const detail = page.locator('section[aria-labelledby="section-leht"]');
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("Valitud perioodil");
  // Not a lifetime figure for a page older than the collection, and the first
  // measured day is printed beside it rather than left to be assumed.
  await expect(detail).toContainText("Kokku mõõdetud");
  await expect(detail).toContainText("Mõõdetud alates");
});

// ---------------------------------------------------------------------------
// Andmete kohta
// ---------------------------------------------------------------------------

test("the coverage disclosure moved to Admin and kept its numbers", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  /*
   * It is no longer a `<details>` that starts shut. On Koduleht it had to stay
   * out of the way of a board member; `/haldus/` exists to show exactly this, so
   * hiding it behind a summary there would be a disclosure nobody opens.
   */
  await page.goto("/haldus/");

  const method = page.locator(METHOD);
  await expect(method).toBeVisible();
  await expect(method).toContainText("Puuduvaid päevi");
  await expect(method).toContainText("Lehekaupa kogutud päevi");
  // The rule that matters most, in the reader's own words.
  await expect(method).toContainText("ei ole 780");
});

test("Koduleht no longer carries the disclosure it used to end with", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  await expect(page.locator("main")).not.toContainText("Andmete kohta");
});

test("the seeded gaps are disclosed rather than smoothed over", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  /*
   * The seed publishes a few days carrying the site figures and no page rows,
   * behind both 30-day windows. So the thirty-day comparison is trusted and the
   * movement lists are drawn, while the coverage disclosure over the whole
   * history reports fewer page-detail days than days — which is the honest
   * statement, and the one a reader needs before trusting a long-window ranking.
   */
  await page.goto("/koduleht/?fookus=sisu&periood=30");
  await expect(page.locator(MOVEMENT)).toContainText("Kasvavad lehed");

  /*
   * No `?periood=koik` needed any more. Admin reports coverage over the whole
   * collected history by definition — there is no period control there to make
   * the table mean something narrower.
   */
  await page.goto("/haldus/");
  const method = page.locator(METHOD);

  const counts = await method.evaluate((node) => {
    const read = (label) => {
      const term = Array.from(node.querySelectorAll("dt")).find(
        (dt) => dt.textContent.trim() === label,
      );
      return term ? Number(term.nextElementSibling.textContent.trim()) : null;
    };
    return { days: read("Kogutud päevi"), pages: read("Lehekaupa kogutud päevi") };
  });

  expect(counts.days).toBeGreaterThan(0);
  expect(counts.pages).toBeLessThan(counts.days);
});

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

test("no focus view widens the page", async ({ page }) => {
  /*
   * Runs at every width, unlike the rest of this file. The seeded ranking puts
   * a very long linked title at rank one and several views carry wide tables, so
   * every one of them is a real candidate for the `min-w-0` failure the template
   * comments describe: a flex item whose `min-width` is `auto` refuses to shrink
   * below a `min-w-max` table and pushes the document sideways instead of
   * scrolling inside itself. The `sr-only` escape is the other half of it.
   */
  await signIn(page);

  const urls = [
    "/koduleht/",
    "/koduleht/?fookus=liiklus",
    "/koduleht/?fookus=sisu&periood=koik",
    "/koduleht/?fookus=kanalid",
    "/koduleht/?fookus=lehed&periood=koik&otsing=sunteetiline",
  ];
  for (const url of urls) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }
});
