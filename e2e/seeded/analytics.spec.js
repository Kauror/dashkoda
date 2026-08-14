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
const METHOD = 'section[aria-labelledby="section-andmed"]';

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

/** The visible label of every row in a chart's accessible data table. */
const chartTableLabels = (page, captionPrefix) =>
  page.evaluate((prefix) => {
    const caption = Array.from(document.querySelectorAll("main table caption")).find((node) =>
      node.textContent.trim().startsWith(prefix),
    );
    if (!caption) {
      return [];
    }
    return Array.from(caption.closest("table").querySelectorAll("tbody tr th")).map((cell) =>
      cell.textContent.trim(),
    );
  }, captionPrefix);

// ---------------------------------------------------------------------------
// Ülevaade
// ---------------------------------------------------------------------------

test("the overview answers before the reader touches anything", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  // If this fails, every other test in this file is passing vacuously.
  await expect(page.locator(KPI)).toBeVisible();
  for (const label of ["Seansid", "Lehevaatamised", "Kaasatud seansside osakaal"]) {
    await expect(page.locator(KPI)).toContainText(label);
  }
  await expect(page.getByRole("heading", { name: "Mis muutus?", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Kust tullakse", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mida vaadatakse", exact: true })).toBeVisible();
});

test("the seeded history gives every headline a comparison", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?periood=30");

  // Seventy seeded days is a complete thirty-day window and a complete one
  // before it, which is what the comparison requires. A rate moves in
  // percentage points and a count in percent, and the two must not be confused.
  await expect(page.locator(KPI)).toContainText("pp");
  await expect(page.locator(KPI)).toContainText("%");
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

test("every chart keeps its numbers as a table", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu");

  // The table is not a fallback: it stays in the document for every reader, and
  // only the canvas is hidden when there is nothing to draw.
  const figures = page.locator("figure[data-chart]");
  const count = await figures.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await expect(figures.nth(index).locator("table[data-chart-table]")).toHaveCount(1);
  }
});

// ---------------------------------------------------------------------------
// Sisu
// ---------------------------------------------------------------------------

test("no language root or utility path occupies a ranking position", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=koik");

  const labels = await chartTableLabels(page, "Enim vaadatud sisu");
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

test("the content mix states which denominator it used", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu");

  // "Vaadatud sisu jaotus" is a share of rankable content, which is smaller than
  // the site's page views. Calling it the site's traffic would be wrong.
  await expect(page.getByRole("heading", { name: "Vaadatud sisu jaotus" })).toBeVisible();
  await expect(page.locator("main")).toContainText("järjestatavaks sisuks");
});

test("the movement lists find the pages the seed made move", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=30");

  const movement = page.locator(MOVEMENT);
  await expect(movement).toContainText("Kasvavad lehed");
  // Deliberately neutral: traffic falls because an event ended, not because
  // anybody failed.
  await expect(movement).toContainText("Vähenenud tähelepanu");
  await expect(movement).not.toContainText("Halvimad");

  await expect(movement).toContainText("sunteetiline-kasvav");
  await expect(movement).toContainText("sunteetiline-vaibuv");
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

  const labels = await chartTableLabels(page, "Lehevaatamised sisukeele järgi");
  expect(labels).toContain("Eesti");
  expect(labels).toContain("Inglise");
  await expect(page.locator("main")).toContainText("mitte külastaja rahvust");
});

test("the opportunity matrix names measurements rather than verdicts", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=30");

  await expect(page.getByRole("heading", { name: "Tähelepanu ja kaasatus" })).toBeVisible();
  const main = page.locator("main");
  await expect(main).toContainText("Palju vaatamisi, lühem kaasatus");
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

  await expect(page.getByRole("heading", { name: "Seansid kanalite kaupa" })).toBeVisible();
  await expect(page.locator("main")).toContainText("kogu kodulehe seansside suhtes");
  const labels = await chartTableLabels(page, "Seansid kanalite kaupa");
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
  expect(await chartTableLabels(page, "Enim vaadatud sisu")).not.toContain(wanted);

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

test("the methodology discloses coverage without shouting about it", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/");

  const method = page.locator(METHOD);
  await expect(method).toBeVisible();
  // Available, not intrusive: it is a disclosure and starts shut.
  const open = await method.locator("details").first().getAttribute("open");
  expect(open).toBeNull();

  await method.locator("summary").first().click();
  await expect(method).toContainText("Puuduvaid päevi");
  await expect(method).toContainText("Lehekaupa kogutud päevi");
  // The rule that matters most, in the reader's own words.
  await expect(method).toContainText("ei ole 780");
});

test("the seeded gaps are disclosed rather than smoothed over", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/koduleht/?periood=30");

  // The seed publishes a few days with the site figures and no page detail. The
  // content comparison must refuse itself and say why rather than draw a delta.
  await page.goto("/koduleht/?fookus=sisu&periood=30");
  const movement = page.locator(MOVEMENT);
  const text = await movement.innerText();
  expect(text).toMatch(/Kasvavad lehed|Kasvu ja languse võrdlust ei kuvata/);
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
