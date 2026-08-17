import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The sections a reader always sees, in reading order: the state of each
 * domain, what is coming, what is being attended to, and the audiences.
 *
 * `Tähelepanu` is deliberately not here. It renders only when a domain flagged
 * something and an empty database cannot produce one, which is asserted
 * separately below; `e2e/seeded/executive-overview.spec.js` holds the other
 * half, where the seeded data does produce signals.
 *
 * `Andmete seis` moved to `/haldus/` on 2026-08-15 and does not come back.
 */
const SECTIONS = ["Põhinäitajad", "Järgmised 30 päeva", "Praegu enim huvi", "Auditooriumid"];

/*
 * The one heading whose digits are a constant rather than a measurement. It
 * names the timeline's horizon, which is the fact a reader needs in order to
 * know whether an empty list means a quiet fortnight or a quiet year.
 */
const TIMELINE_HEADING = "Järgmised 30 päeva";

test("the shell renders every section with a truthful empty state", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);

  for (const section of SECTIONS) {
    // Level 2 pins this to the section headings. A domain card names its
    // dashboard too ("Liikmeskond"), and that label is an h3 inside the card.
    await expect(page.getByRole("heading", { name: section, exact: true, level: 2 })).toBeVisible();
  }
  // With nothing imported, every card says so rather than showing a nought.
  await expect(page.getByText("Andmeallikas ei ole ühendatud.").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("every domain has a card even before anything is connected", async ({ page }) => {
  // A domain silently absent from the front page reads as a domain that does
  // not exist. The card is the page's structure, not a reward for a wired feed.
  await signIn(page);

  const cards = page.getByRole("region", { name: "Põhinäitajad" }).locator("article");

  await expect(cards).toHaveCount(6);
  for (const label of [
    "Liikmeskond",
    "Õigusloome",
    "Sündmused",
    "Koduleht ja uudised",
    "Otsepostitused",
    "E-pood",
  ]) {
    await expect(cards.getByRole("heading", { name: label, level: 3 })).toBeVisible();
  }
});

test("the attention section is silent rather than empty", async ({ page }) => {
  /*
   * Nothing flagged means no section at all — not a header and a line of
   * reassurance. A reader who learns to skim `Tähelepanu` when it is full of
   * routine will skim it on the day it is not.
   */
  await signIn(page);

  await expect(page.getByRole("heading", { name: "Tähelepanu", level: 2 })).toHaveCount(0);
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

  // The whole of `main` with one carve-out, and it is a constant rather than a
  // measurement: the timeline's heading names its own horizon. Everything else
  // is scanned — not one digit, anywhere.
  const text = await page.evaluate(() => document.querySelector("main").innerText);

  expect(text.split(TIMELINE_HEADING).join("")).not.toMatch(/\d/);
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
