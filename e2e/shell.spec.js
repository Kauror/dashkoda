import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

const SECTIONS = [
  "Põhinäitajad",
  "Juhatuse tähelepanu",
  "Pärast eelmist ülevaadet",
  "Liikmeskond",
  "Õigusloome ja arvamused",
  "Tulevased sündmused",
  "Viimased uudised",
];

test("the shell renders every section with a truthful empty state", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);

  for (const section of SECTIONS) {
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
  }
  await expect(page.getByText("Andmeallikas ei ole veel ühendatud.").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("the Chamber logo is visible and undistorted", async ({ page }) => {
  await signIn(page);

  const logo = page.getByRole("img", { name: /Kaubandus-Tööstuskoda/ }).first();
  await logo.scrollIntoViewIfNeeded();
  const ratio = await logo.evaluate((image) => {
    const box = image.getBoundingClientRect();
    return {
      rendered: box.width / box.height,
      natural: image.naturalWidth / image.naturalHeight,
      width: box.width,
    };
  });

  expect(ratio.width).toBeGreaterThan(0);
  expect(Math.abs(ratio.rendered - ratio.natural)).toBeLessThan(0.02);
});

test("no fabricated business number is shown anywhere on the shell", async ({ page }) => {
  await signIn(page);

  // The connection-check time is a fact about the application, not about
  // business data, so it is excluded before the page is scanned for digits.
  const text = await page.evaluate(() => {
    const clone = document.querySelector("main").cloneNode(true);
    clone.querySelector("#freshness-region")?.remove();
    return clone.innerText;
  });

  expect(text).not.toMatch(/\d/);
});

test("the page never scrolls sideways", async ({ page }) => {
  await signIn(page);

  await expectNoHorizontalOverflow(page);
});

test("the shell stays usable at 200% zoom", async ({ page }) => {
  await signIn(page);
  await page.evaluate(() => {
    document.documentElement.style.zoom = "200%";
  });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the skip link is the first focus stop and reaches the main region", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("Tab");

  const focused = await page.evaluate(() => document.activeElement?.getAttribute("href"));
  expect(focused).toBe("#main");
});
