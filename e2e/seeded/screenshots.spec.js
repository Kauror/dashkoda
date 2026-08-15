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

test("capture the Koduleht overview with analytics", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/koduleht/");
  await page.screenshot({ path: shot(testInfo, "visibility"), fullPage: true });
});

test("capture the content view with its rankings", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/koduleht/?fookus=sisu&periood=koik");
  await page.screenshot({ path: shot(testInfo, "visibility-content"), fullPage: true });
});

test("capture the page explorer mid-search", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/koduleht/?fookus=lehed&periood=koik&otsing=sunteetiline");
  await page.screenshot({ path: shot(testInfo, "visibility-search"), fullPage: true });
});

test("capture the news archive", async ({ page }, testInfo) => {
  /*
   * Captured at every width, because the archive's density is the point of it
   * and density is exactly what a desktop-only screenshot cannot review.
   */
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=koik");
  await page.screenshot({ path: shot(testInfo, "news"), fullPage: true });
});

test("capture each news focus view", async ({ page }, testInfo) => {
  /*
   * All four, at every width. The dashboard's whole claim is that a reader gets
   * an answer in seconds and can then investigate, and whether that holds is a
   * question about hierarchy and density on a real screen — which is what a
   * screenshot review is for and what no assertion covers.
   */
  await signIn(page);
  for (const [name, url] of [
    ["news-overview", "/uudised/"],
    ["news-impact", "/uudised/?fookus=moju"],
    ["news-publishing", "/uudised/?fookus=avaldamine"],
    ["mailings", "/otsepostitused/"],
  ]) {
    await page.goto(url);
    await page.screenshot({ path: shot(testInfo, name), fullPage: true });
  }
});
