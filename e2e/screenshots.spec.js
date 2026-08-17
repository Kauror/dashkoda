import { test } from "@playwright/test";

import { signIn } from "./helpers.js";

/*
 * Produces the review screenshots that CI uploads as an artifact. They come
 * from the synthetic CI environment and contain no real or fabricated business
 * values, because the shell renders none.
 */
const shot = (testInfo, name) => `screenshots/${testInfo.project.name}-${name}.png`;

test("capture the login page", async ({ page }, testInfo) => {
  await page.goto("/sisene/");
  await page.screenshot({ path: shot(testInfo, "login"), fullPage: true });
});

test("capture the dashboard overview", async ({ page }, testInfo) => {
  await signIn(page);
  await page.screenshot({ path: shot(testInfo, "overview"), fullPage: true });
});

test("capture the visibility page", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/koduleht/");
  await page.screenshot({ path: shot(testInfo, "visibility"), fullPage: true });
});

test("capture the news page", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/uudised/");
  await page.screenshot({ path: shot(testInfo, "news"), fullPage: true });
});

test("capture the mailings section", async ({ page }, testInfo) => {
  // Reviewed as a pair with Uudised and Koduleht: what the newsletter move
  // changed is which page carries the section, not anything the section says.
  // The separate archive capture went on 2026-08-16 — the archive is the sends
  // table on this page now, so `fullPage` already covers it.
  await signIn(page);
  await page.goto("/otsepostitused/");
  await page.screenshot({ path: shot(testInfo, "mailings"), fullPage: true });
});

test("capture the admin area", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/haldus/");
  await page.screenshot({ path: shot(testInfo, "admin"), fullPage: true });
});

test("capture the open mobile drawer", async ({ page }, testInfo) => {
  test.skip(page.viewportSize().width >= 1024, "narrow layouts only");

  await signIn(page);
  await page.getByRole("button", { name: "Ava menüü" }).click();
  await page.screenshot({ path: shot(testInfo, "drawer"), fullPage: false });
});
