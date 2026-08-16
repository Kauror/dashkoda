import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

// The executive overview's sections, in reading order: status, near-term work,
// audience behaviour. `Andmete seis` moved to `/haldus/` on 2026-08-15;
// `Mis vajab tähelepanu?` and `Praegu huvi pakkuv` left on 2026-08-16 and the
// remaining two were renamed. Three now, down from seven.
const SECTIONS = ["Koja seis", "Eesolevad tegevused", "Kanalid"];

test("the shell renders every section with a truthful empty state", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);

  for (const section of SECTIONS) {
    // Level 2 pins this to the section headings. A pillar names its strategic
    // area too ("Liikmeskond"), and that label is an h3 inside the card.
    await expect(page.getByRole("heading", { name: section, exact: true, level: 2 })).toBeVisible();
  }
  // With nothing imported, every pillar says so rather than showing a nought,
  // and the exception section is silent rather than full of reassurance.
  await expect(page.getByText("Andmeallikas ei ole ühendatud.").first()).toBeVisible();
  await expect(
    page.getByText("Olulisi muutusi või lähenevaid tähtaegu ei ole."),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("the Chamber logo is visible and undistorted", async ({ page }) => {
  await signIn(page);

  if (page.viewportSize().width < 1024) {
    // Narrow layouts show the product name as text in the top bar and keep the
    // Chamber logo in the drawer, so only one mark is ever on screen.
    await expect(page.getByText("DashKoda", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Ava menüü" }).click();
  }

  const logo = page.getByRole("img", { name: /Kaubandus-Tööstuskoda/ }).first();
  await expect(logo).toBeVisible();

  const measured = await logo.evaluate((image) => {
    const box = image.getBoundingClientRect();
    return {
      rendered: box.width / box.height,
      natural: image.naturalWidth / image.naturalHeight,
      width: box.width,
    };
  });

  expect(measured.width).toBeGreaterThan(0);
  expect(Math.abs(measured.rendered - measured.natural)).toBeLessThan(0.02);
});

test("no fabricated business number is shown anywhere on the shell", async ({ page }) => {
  await signIn(page);

  // The whole of `main`, with nothing cut out. This used to carve out
  // `Järgmised 30 päeva`, whose thirty was a constant in a section title rather
  // than a measurement; that section became `Eesolevad tegevused` on 2026-08-16
  // and carries no number, and the header chip's count went the same day. So
  // the rule is now simply: not one digit, anywhere.
  const text = await page.evaluate(() => document.querySelector("main").innerText);

  expect(text).not.toMatch(/\d/);
});

test("the page never scrolls sideways", async ({ page }) => {
  await signIn(page);

  await expectNoHorizontalOverflow(page);
});

test("the shell stays usable at 200% zoom", async ({ page }) => {
  test.skip(page.viewportSize().width < 1024, "measured from the desktop viewport");

  await signIn(page);
  // Browser zoom halves the CSS-pixel viewport, so emulate it by halving the
  // viewport rather than by setting CSS zoom, which does not scale the layout
  // viewport and makes overflow measurements meaningless.
  const { width, height } = page.viewportSize();
  await page.setViewportSize({ width: Math.round(width / 2), height: Math.round(height / 2) });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the skip link is the first focus stop and reaches the main region", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("Tab");

  const focused = await page.evaluate(() => document.activeElement?.getAttribute("href"));
  expect(focused).toBe("#main");
});
