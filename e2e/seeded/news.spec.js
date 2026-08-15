import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "../helpers.js";

/*
 * The news archive, against a seeded catalogue.
 *
 * `/uudised/` used to render the current feed snapshot: ten rolling items under
 * a source-status panel and two KPI cards that both said "10". These assert the
 * page it became — a compact archive over the durable catalogue, filtered by
 * publication period rather than by whatever the feed happens to be showing.
 *
 * The compactness is the product requirement, so it is asserted as one: rows
 * have a measured height, and the first article sits within a screen of the
 * page title.
 */

const ARCHIVE = 'section[aria-labelledby="section-archive"]';

const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

test("the removed status panel and KPI cards are gone", async ({ page }) => {
  oncePerRun();
  const errors = watchConsole(page);

  await signIn(page);
  await page.goto("/uudised/");

  const main = page.locator("main");
  await expect(main).not.toContainText("Viimane edukas sünkroonimine");
  await expect(main).not.toContainText("Viimati kontrollitud");
  await expect(main).not.toContainText("Avaldatud viimase kuu jooksul");
  await expect(main).not.toContainText("Uudiseid voos");
  // The stale planned block: newsletter analytics are `Otsepostitused` now, so
  // a module here saying the newsletter source is not connected is simply
  // false — and nothing on this page is about newsletters at all.
  await expect(main).not.toContainText("Muud kanalid");
  await expect(main).not.toContainText("Meediakajastused");

  expect(errors).toEqual([]);
});

test("the controls and the first article are near the top of the page", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik");

  await expect(page.getByRole("heading", { level: 1, name: "Uudised" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Avaldamisperiood" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Enim vaadatud" })).toBeVisible();

  /*
   * The product requirement, measured rather than described. The old page put a
   * status panel and a KPI strip between the title and the first article; the
   * archive has to reach the news inside roughly a screen of the heading.
   *
   * The allowance rose from 320 when the five-item focus navigation and the
   * source-freshness row moved above the archive's own controls. Those are the
   * page's navigation rather than filler about the feed, and the requirement is
   * still what it was: the news within about a screen of the title.
   */
  const gap = await page.evaluate(() => {
    const heading = document.querySelector("main h1");
    const firstRow = document.querySelector("main table tbody tr");
    return firstRow.getBoundingClientRect().top - heading.getBoundingClientRect().top;
  });
  expect(gap).toBeLessThan(420);
});

test("rows are compact and carry no article summary", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik");

  const heights = await page.evaluate(() =>
    Array.from(document.querySelectorAll("main table tbody tr")).map(
      (row) => row.getBoundingClientRect().height,
    ),
  );
  expect(heights.length).toBeGreaterThan(10);
  /*
   * The old rows ran 100–150px because each carried a multi-line RSS summary.
   * The ceiling allows a deliberately absurd seeded headline to wrap onto a
   * second line — that is the title column doing its job — while still failing
   * if a summary or a second metadata line ever comes back.
   */
  expect(Math.max(...heights)).toBeLessThan(72);
  // And an ordinary row is one line.
  const typical = heights.slice().sort((a, b) => a - b)[Math.floor(heights.length / 2)];
  expect(typical).toBeLessThan(46);

  // And the summary text itself is absent. The seed writes a fixed sentence
  // into every article's summary, so if any of it rendered this would find it.
  await expect(page.locator("main")).not.toContainText("Sünteetiline kokkuvõte");
});

test("a period selects articles by publication date", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  await page.goto("/uudised/?fookus=arhiiv&periood=koik");
  const all = await page.locator("main table tbody tr").count();

  await page.goto("/uudised/?fookus=arhiiv&periood=30");
  const recent = await page.locator("main table tbody tr").count();

  expect(all).toBeGreaterThan(recent);
  await expect(page.getByRole("link", { name: "30 päeva" })).toHaveAttribute(
    "aria-current",
    "true",
  );
});

test("a custom range exposes its two date fields and applies them", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv");

  await page.getByRole("link", { name: "Kohandatud" }).click();
  const from = page.getByLabel("Alates");
  const to = page.getByLabel("Kuni");
  await expect(from).toBeVisible();
  await expect(to).toBeVisible();

  // A reversed pair is normalised by the server rather than refused, and the
  // fields then show the window that was actually applied.
  await page.goto("/uudised/?fookus=arhiiv&periood=kohandatud&alates=2099-01-01&kuni=2020-01-01");
  await expect(page.getByLabel("Alates")).toHaveValue("2020-01-01");
  await expect(page.getByLabel("Kuni")).toHaveValue("2099-01-01");
});

test("the view column shows measured figures and a dash where nothing was measured", async ({
  page,
}) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik");

  const views = await page.evaluate(() =>
    Array.from(document.querySelectorAll("main table tbody tr")).map((row) =>
      row.lastElementChild.textContent.trim(),
    ),
  );

  expect(views.some((value) => /^[\d\s ]+$/.test(value))).toBe(true);
  // The seed leaves later articles unmeasured on purpose. A zero there would be
  // a fabricated reading.
  expect(views.some((value) => value === "—")).toBe(true);
  expect(views).not.toContain("0");
  // The unit is named once, in the column heading — not on every row. Scoped
  // to the archive: `Andmete kohta` spells the word once, deliberately, to
  // say that a page view is not a reader.
  await expect(page.locator(ARCHIVE)).not.toContainText("lehevaatamist");
});

test("pagination walks the archive and keeps the query", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik&sort=vaadatud");

  const section = page.locator(ARCHIVE);
  await expect(section).toContainText(/Lehekülg 1 \/ [2-9]/);

  const firstPage = await page.locator("main table tbody tr").first().innerText();
  await section.getByRole("link", { name: "Järgmine" }).click();

  await expect(section).toContainText(/Lehekülg 2 \//);
  await expect(page.getByRole("link", { name: "Enim vaadatud" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(await page.locator("main table tbody tr").first().innerText()).not.toBe(firstPage);
});

test("an article title opens the original on Koda.ee", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik");

  const link = page.locator("main table tbody a").first();
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", /noopener/);
  await expect(link).toHaveAttribute("href", /koda\.ee/);
});

test("the archive is readable without dragging it sideways", async ({ page }) => {
  /*
   * Runs at every width. Every other table on this dashboard is `min-w-max` and
   * scrolls inside its wrapper, which suits a row of figures with a natural
   * width. A headline has none, so the same treatment sized this table to the
   * longest title on the page and put the whole archive behind a horizontal
   * scrollbar — on a phone, dragging sideways to read a headline. The title
   * column wraps instead, and this is what says so.
   */
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik");

  const scrolls = await page.evaluate(() => {
    const table = document.querySelector("main table");
    return table.scrollWidth - table.parentElement.clientWidth;
  });

  expect(scrolls).toBeLessThanOrEqual(1);
});

test("the archive never widens the page", async ({ page }) => {
  await signIn(page);

  for (const url of [
    "/uudised/?fookus=arhiiv",
    "/uudised/?fookus=arhiiv&periood=koik&sort=vaadatud",
    "/uudised/?fookus=arhiiv&periood=kohandatud&alates=2020-01-01&kuni=2030-01-01",
  ]) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }
});
