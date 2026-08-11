import { test } from "@playwright/test";

import { signIn } from "../helpers.js";

/*
 * Review screenshots of pages that actually have content.
 *
 * `e2e/screenshots.spec.js` runs in the empty stage, so every image CI has ever
 * uploaded shows an empty state — useful for reviewing the honest-nothing case
 * and useless for reviewing a table, a ranking or a search. These run after
 * `manage.py seed_e2e_data`, at all six project widths, and are the only way to
 * see the narrow layouts of the traffic section without a phone in hand.
 *
 * Every value in them is synthetic. The seed publishes no real Chamber figure,
 * member total, article, event or URL, so an image from here can never be
 * mistaken for production.
 */
const shot = (testInfo, name) => `screenshots/${testInfo.project.name}-seeded-${name}.png`;

test("capture the visibility page with analytics", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/nahtavus/");
  await page.screenshot({ path: shot(testInfo, "visibility"), fullPage: true });
});

test("capture the content ranking and the page search", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/nahtavus/?periood=koik&otsing=sunteetiline");
  await page.screenshot({ path: shot(testInfo, "visibility-search"), fullPage: true });
});

test("capture the news archive", async ({ page }, testInfo) => {
  /*
   * Captured at every width, because the archive's density is the point of it
   * and density is exactly what a desktop-only screenshot cannot review.
   */
  await signIn(page);
  await page.goto("/uudised/?periood=koik");
  await page.screenshot({ path: shot(testInfo, "news"), fullPage: true });
});
