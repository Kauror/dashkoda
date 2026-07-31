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
  await page.goto("/nahtavus/");
  await page.screenshot({ path: shot(testInfo, "visibility"), fullPage: true });
});

test("capture the open mobile drawer", async ({ page }, testInfo) => {
  test.skip(page.viewportSize().width >= 1024, "narrow layouts only");

  await signIn(page);
  await page.getByRole("button", { name: "Ava menüü" }).click();
  await page.screenshot({ path: shot(testInfo, "drawer"), fullPage: false });
});
