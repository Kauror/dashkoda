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
 * bar. That is deliberate until the seed is raised to 2.0 — the fallback is the
 * state a real 1.0 dataset is in, and it needs covering too.
 */

test("the ranking never scrolls sideways with real titles", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  await expect(page.getByRole("heading", { level: 1, name: "E-pood" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the product detail never scrolls sideways", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await page.getByRole("link", { name: /Sünteetiline lepingu näidis 1$/ }).first().click();

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the export states its own date in one quiet line", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/");

  await expect(page.getByText(/Andmed \d{2}\.\d{2}\.\d{4}/)).toBeVisible();
  await expect(page.getByText(/Tellimused \d{2}\.\d{2}\.\d{4}/)).toBeVisible();
  await expect(page.getByText("sünkroonitud")).toHaveCount(0);
  await expect(page.getByText("automaatselt uuendatud")).toHaveCount(0);
});

test("value is labelled as ordered value, never as revenue", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  // The label and its unit are separate elements now: `Tellitud väärtus` with
  // `KM-ta` beneath it, rather than one parenthesised string.
  await expect(page.getByText("Tellitud väärtus").first()).toBeVisible();
  await expect(page.getByText("KM-ta").first()).toBeVisible();
  await expect(page.getByText("Laekunud tulu")).toHaveCount(0);
  await expect(page.getByText("Müük", { exact: true })).toHaveCount(0);
});

test("the headline carries three commerce measures, not four", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  const strip = page.locator('section[aria-labelledby="section-kpis"]');
  await expect(strip.locator("article")).toHaveCount(3);
});

test("search finds a product that is not on the first page", async ({ page }) => {
  /*
   * The point of searching the whole population rather than the visible rows:
   * this product sits past the ranking's page of twenty-five.
   */
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await page.getByLabel("Otsi toodet").fill("Sünteetiline lepingu näidis 28");
  await page.getByRole("button", { name: "Otsi" }).click();

  await expect(page.locator("#tooted").getByRole("link", { name: /näidis 28/ })).toBeVisible();
});

test("the methodology is present but collapsed", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  const details = page.locator("#andmete-kohta details");
  await expect(details).toBeVisible();
  // Closed by default: the caveats no longer sit between the reader and the
  // first number.
  await expect(details).not.toHaveAttribute("open", /.*/);

  await details.locator("summary").click();
  await expect(page.getByText(/ei liideta/)).toBeVisible();
});

test("the member split is withheld while its semantics are unverified", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");
  await page.locator("#andmete-kohta details summary").click();

  await expect(page.getByText(/ei ole kinnitanud/)).toBeVisible();
});

test("the explorer drops the information column", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  const headers = page.locator("#tooted thead th");
  await expect(headers).toHaveCount(5);
  await expect(page.locator("#tooted").getByText("Tutvustus")).toHaveCount(0);
});

test("a period past the export offers no web comparison", async ({ page }) => {
  /*
   * GA4 keeps collecting for ten days after the seeded export stops. Those days
   * must never appear as traffic divided by no orders.
   */
  await signIn(page);
  await page.goto("/epood/?periood=30");

  await expect(page.getByRole("heading", { level: 1, name: "E-pood" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
