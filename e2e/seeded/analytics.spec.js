import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn } from "../helpers.js";

/*
 * The website-traffic section of Nähtavus, against seeded analytics.
 *
 * This suite exists because that section used to be invisible here. The seed
 * published no GA4 history, `overview.html` gates the whole section on
 * `page.ga4.is_connected`, and so every browser run — and every screenshot CI
 * uploaded — showed the `Lisamisel` empty state instead. Two defects shipped
 * through a fully green suite on 2026-08-11 in that blind spot: the view never
 * read the `otsing` query parameter, and the template hid the entire content
 * block, search box included, behind `{% if traffic.ranking %}` while searching
 * empties the ranking on purpose. Neither was reachable by any test.
 *
 * The assertions are therefore about reachability, not about arithmetic: that
 * the controls appear, that using them changes what the page answers, and that
 * neither mode widens the document at 320 pixels.
 */

const TRAFFIC = 'section[aria-labelledby="section-traffic"]';

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

/** The visible label of every row in the content ranking, suffix stripped. */
const rankingLabels = (page) =>
  page.evaluate(() => {
    const caption = Array.from(document.querySelectorAll("main table caption")).find((node) =>
      node.textContent.trim().startsWith("Enim vaadatud sisu"),
    );
    if (!caption) {
      return [];
    }
    return Array.from(caption.closest("table").querySelectorAll("tbody tr th")).map((cell) => {
      // The anchor carries a visually hidden "(väline leht…)" note after the
      // label, so the first child node is the label and the rest is the note.
      const link = cell.querySelector("a");
      return (link ? link.firstChild.textContent : cell.textContent).trim();
    });
  });

test("the traffic section renders rather than the not-connected state", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/nahtavus/");

  // If this fails, every other test in this file is passing vacuously.
  await expect(page.locator(TRAFFIC)).toBeVisible();
  await expect(page.locator(TRAFFIC)).not.toContainText("ei ole ühendatud");
  await expect(page.getByLabel("Otsi lehekülge")).toBeVisible();
});

test("no language root or utility path occupies a ranking position", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/nahtavus/");

  const labels = await rankingLabels(page);
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
  await page.goto("/nahtavus/");

  // Excluded from a list, never from a total. A section whose figures had gone
  // to zero would mean the registry had been applied to the wrong query.
  await expect(page.locator(TRAFFIC)).toContainText("Lehevaatamisi");
  const figures = await page.locator(TRAFFIC).locator("dd").allInnerTexts();
  expect(figures.some((value) => /[1-9]/.test(value))).toBe(true);
});

test("searching reaches a page the ranking does not show", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/nahtavus/");

  /*
   * Seeded almost silent, so it sits far below the Top 20 whatever else the
   * seed does. That is the whole point of search: the page somebody looks up is
   * the one the ranking cannot reach. The term appears in no path, so only the
   * news catalogue can produce this row — events and services are deliberately
   * uncatalogued and show their paths instead.
   */
  const wanted = "Sünteetiline uudise pealkiri 12";
  expect(await rankingLabels(page)).not.toContain(wanted);

  // Through the control itself, not through a hand-built URL — the parameter
  // never reaching the view is exactly the defect this file was written for.
  const section = page.locator(TRAFFIC);
  await page.getByLabel("Otsi lehekülge").fill("pealkiri 12");
  await section.getByRole("button", { name: "Otsi", exact: true }).click();

  await expect(section).toContainText("Otsingu tulemused");
  await expect(section.getByRole("link", { name: new RegExp(wanted) })).toBeVisible();
  // And the box still holds what was typed, so the term stays visible.
  await expect(page.getByLabel("Otsi lehekülge")).toHaveValue("pealkiri 12");
});

test("a search that finds nothing keeps its box and offers the way back", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/nahtavus/?otsing=puudubtaielikult");

  const section = page.locator(TRAFFIC);
  await expect(section).toContainText("Ühtegi lehte ei leitud");
  // The half of the defect that only appeared once a search actually ran: the
  // block hid itself, taking the search box and this link with it.
  await expect(page.getByLabel("Otsi lehekülge")).toBeVisible();
  await expect(section.getByRole("link", { name: "Tühjenda otsing" })).toBeVisible();
});

test("the period and the section travel with a search and survive clearing it", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/nahtavus/?periood=90&sisu=sundmused&otsing=sunteetiline");

  const section = page.locator(TRAFFIC);
  const active = () =>
    section.locator(
      'nav[aria-label="Periood"] [aria-current], nav[aria-label="Sisu"] [aria-current]',
    );
  await expect(active()).toHaveText(["90 päeva", "Sündmused"]);

  // Clearing a search is not starting again: the reader still wants Sündmused
  // over 90 days, they have simply finished looking for one page.
  await section.getByRole("link", { name: "Tühjenda otsing" }).click();
  await expect(active()).toHaveText(["90 päeva", "Sündmused"]);
  await expect(section).not.toContainText("Otsingu tulemused");
  expect((await rankingLabels(page)).length).toBeGreaterThan(0);
});

test("results paginate and each page keeps the whole query", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  // Matches every seeded content path, so there are more results than one page.
  await page.goto("/nahtavus/?periood=koik&otsing=sunteetiline");

  const section = page.locator(TRAFFIC);
  await expect(section).toContainText(/Lehekülg 1 \/ [2-9]/);

  await section.getByRole("link", { name: "Järgmine" }).click();
  await expect(section).toContainText(/Lehekülg 2 \//);
  await expect(page.getByLabel("Otsi lehekülge")).toHaveValue("sunteetiline");
  await expect(section.getByRole("link", { name: "Eelmine" })).toBeVisible();
});

test("neither the ranking nor a search widens the page", async ({ page }) => {
  /*
   * Runs at every width, unlike the rest of this file. The seeded ranking puts
   * a very long linked title at rank one and the results table carries three
   * columns, so both modes are real candidates for the `min-w-0` failure the
   * template comments describe: a flex item whose `min-width` is `auto` refuses
   * to shrink below a `min-w-max` table and pushes the document sideways
   * instead of scrolling inside itself.
   */
  await signIn(page);

  for (const url of ["/nahtavus/", "/nahtavus/?periood=koik&otsing=sunteetiline"]) {
    await page.goto(url);
    await expect(page.locator(TRAFFIC)).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }
});
