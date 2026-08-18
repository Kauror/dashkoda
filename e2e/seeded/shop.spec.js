import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn } from "../helpers.js";

/*
 * E-pood against seeded content.
 *
 * The seed publishes thirty document products, one carrying the very long
 * synthetic title, twenty measured and ten never measured, and an export that
 * stops ten days before the analytics do so the stale-Commerce case is on
 * screen rather than only in a unit test.
 *
 * The seed still builds a schema 1.0 package, so this suite exercises the
 * fallback path: order **lines** rather than distinct orders, and no free/paid
 * card. That is deliberate until the seed is raised to 2.0 — the fallback is the
 * state a real 1.0 dataset is in, and it needs covering too.
 *
 * The tab strip — `Ülevaade`, `Ostud`, `Tooted`, `Nähtavus` — retired on
 * 2026-08-18, the same round Koduleht's did. Tooted's table is on the one
 * remaining page now, not behind a focus; every `fookus` value still resolves
 * rather than erroring, because a bookmark made before the retirement must
 * still open something.
 */

test("the ranking never scrolls sideways with real titles", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  await expect(
    page.getByRole("heading", { level: 1, name: "E-pood" }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the product detail never scrolls sideways", async ({ page }) => {
  // Reached through Tooted's own table. The top of that table is a top ten;
  // this product is not in it.
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await page
    .getByRole("link", { name: /Sünteetiline lepingu näidis 1$/ })
    .first()
    .click();

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the export states its own date, in Admin", async ({ page }) => {
  /*
   * The coverage line that used to sit above the heading left first — it put
   * three date ranges between the reader and the first figure. `Andmete kohta`
   * carried the disclosure after that, at the foot; that whole block moved
   * again on 2026-08-17, to `/haldus/`, along with every other domain's. An
   * extract months out of date, shown with nothing saying so, would be
   * claiming to be live, so both halves are checked here: gone from the
   * overview, and actually rendered where it went.
   */
  await signIn(page);
  await page.goto("/epood/");

  await expect(page.getByText(/Andmed \d{2}\.\d{2}\.\d{4}/)).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("Andmete kohta");

  await page.goto("/haldus/");
  // Scoped to E-pood's own block by id: `Andmeallikad ja import` holds more
  // than one domain's `<details>`, so counting every `<details>` in the
  // shared section counts the wrong thing.
  const method = page.locator("#epood-andmeallikad");
  await expect(method).toHaveCount(1);

  await method.locator("summary").click();

  await expect(
    method.getByText(/väljavõte seisuga \d{2}\.\d{2}\.\d{4}/),
  ).toBeVisible();
  await expect(
    method.getByText(/Tellimuste ajalugu algab \d{2}\.\d{2}\.\d{4}/),
  ).toBeVisible();
  await expect(page.getByText("sünkroonitud")).toHaveCount(0);
  await expect(page.getByText("automaatselt uuendatud")).toHaveCount(0);
});

test("value is labelled as ordered value, never as revenue", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  // The label and its unit are separate elements now: `Tellitud väärtus` with
  // `KM-ta` beneath it, rather than one parenthesised string.
  await expect(page.getByText("Tellitud väärtus").first()).toBeVisible();
  await expect(page.getByText("KM-ta").first()).toBeVisible();
  await expect(page.getByText("Laekunud tulu")).toHaveCount(0);
  await expect(page.getByText("Müük", { exact: true })).toHaveCount(0);
});

test("the headline carries three commerce measures under this schema", async ({
  page,
}) => {
  /*
   * Three cards, not four: the fourth slot is the free/paid card, gated on
   * `mix.is_known`, and this seed's schema 1.0 package carries no free/paid
   * classification. A schema 2.0 dataset draws a fourth card in the same
   * strip — see the host-only render tests for that shape.
   */
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  const strip = page.locator('section[aria-labelledby="section-kpis"]');
  await expect(strip.locator("article")).toHaveCount(3);
});

test("search finds a product that is not on the first page", async ({
  page,
}) => {
  /*
   * The point of searching the whole population rather than the visible rows:
   * this product sits past the table's first page of twenty-five.
   */
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await page.getByLabel("Otsi toodet").fill("Sünteetiline lepingu näidis 28");
  await page.getByRole("button", { name: "Otsi" }).click();

  await expect(
    page.locator("#tooted").getByRole("link", { name: /näidis 28/ }),
  ).toBeVisible();
});

test("the methodology is on /haldus/, collapsed, not on the overview", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await expect(page.locator("main")).not.toContainText("ei liideta");

  await page.goto("/haldus/");
  const details = page.locator("#epood-andmeallikad");
  await expect(details).toBeVisible();
  // Closed by default: Admin is where the diagnostics live, not where they
  // shout.
  await expect(details).not.toHaveAttribute("open", /.*/);

  await details.locator("summary").click();
  await expect(details.getByText(/ei liideta/)).toBeVisible();
});

test("the member split withholding reason lives on /haldus/, not the overview", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await expect(page.getByText("Liikmete ostud")).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("ei ole kinnitanud");

  await page.goto("/haldus/");
  await page.locator("#epood-andmeallikad summary").click();

  await expect(page.getByText(/ei ole kinnitanud/)).toBeVisible();
});

test("Tooted carries rank, category, share and change — no page-view column", async ({
  page,
}) => {
  /*
   * Both page-view columns left with Nähtavus on 2026-08-18, whole. `Osa` and
   * `Muutus` are new: a product's share of the current result set and its
   * movement since the equal-length previous window, next to the rank and the
   * category — which is its own column now rather than a subtitle under the
   * product's title.
   */
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  const headers = page.locator("#tooted thead th");
  await expect(headers).toHaveCount(6);
  await expect(page.locator("#tooted").getByText("Tutvustus")).toHaveCount(0);
  await expect(page.locator("#tooted").getByText("Vaatamised")).toHaveCount(0);
  await expect(
    page.locator("#tooted").getByText("Kategooria", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator("#tooted").getByText("Osa", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator("#tooted").getByText("Muutus", { exact: true }),
  ).toBeVisible();
});

test("a stray focus parameter from an old bookmark still opens the page", async ({
  page,
}) => {
  /*
   * The tab strip retired on 2026-08-18. Nothing reads `fookus` any more, so
   * every value — current, retired, or invented — is simply an unread query
   * parameter now, and every one of them opens the same page.
   */
  await signIn(page);

  for (const focus of [
    "ulevaade",
    "ostud",
    "tooted",
    "nahtavus",
    "vaartus",
    "ei-ole-olemas",
  ]) {
    await page.goto(`/epood/?fookus=${focus}&periood=koik`);
    await expect(
      page.getByRole("heading", { level: 1, name: "E-pood" }),
    ).toBeVisible();
    await expect(page.locator('nav[aria-label="Vaade"]')).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  }
});

test("a period past the export still renders", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=30");

  await expect(
    page.getByRole("heading", { level: 1, name: "E-pood" }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
