import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn } from "../helpers.js";

/*
 * E-pood against seeded content.
 *
 * The seed publishes thirty document products, one of them carrying the very
 * long synthetic title, twenty of them measured and ten never measured. The
 * export deliberately stops ten days before the analytics do, so the stale-
 * Commerce case is on screen rather than only in a unit test.
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

test("the export states its own date and is never called live", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/");

  await expect(page.getByText(/E-poe andmed seisuga \d{2}\.\d{2}\.\d{4}/)).toBeVisible();
  await expect(page.getByText("sünkroonitud")).toHaveCount(0);
  await expect(page.getByText("automaatselt uuendatud")).toHaveCount(0);
});

test("value is labelled as ordered value, never as revenue", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  await expect(page.getByText("Tellitud väärtus (KM-ta)").first()).toBeVisible();
  await expect(page.getByText("Laekunud tulu")).toHaveCount(0);
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

  await expect(page.getByRole("link", { name: /näidis 28/ })).toBeVisible();
});

test("the member split is withheld while its semantics are unverified", async ({ page }) => {
  await signIn(page);
  await page.goto("/epood/?periood=koik");

  await expect(page.getByText(/ei ole kinnitanud/)).toBeVisible();
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
