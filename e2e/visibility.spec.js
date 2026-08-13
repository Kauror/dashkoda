import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The Nähtavus page and the overview's six-slot channel band.
 *
 * CI runs against a container with an empty database, so every assertion here is
 * about the *truthful empty state* and the layout. That is the state a fresh
 * deployment is in, and it is the one most likely to be got wrong: a band with
 * nothing in it must show no figure at all rather than a row of zeros.
 *
 * The newsletters are not on this page any more — see `news-newsletters.spec.js`
 * for where they went. The overview's band below still names all six channels:
 * Uudiskirjad is a communication channel on the executive summary whatever page
 * carries its analytics.
 */

const CHANNELS = [
  "Kodulehe külastused",
  "Uudiskirjad",
  "Facebooki jälgijad",
  "LinkedIni jälgijad",
  "Instagrami jälgijad",
  "YouTube’i tellijad",
];

/** Sign in, then open Nähtavus. `signIn` always lands on the overview. */
async function openVisibility(page) {
  await signIn(page);
  await page.goto("/nahtavus/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Mõju ja nähtavus");
}

test("the overview band names all six channels", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);

  for (const channel of CHANNELS) {
    await expect(page.getByRole("heading", { name: channel, exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("the six-slot band never scrolls the overview sideways", async ({ page }) => {
  await signIn(page);

  await expectNoHorizontalOverflow(page);
});

test("Nähtavus is reachable from the navigation", async ({ page }) => {
  await signIn(page);

  if (page.viewportSize().width < 1024) {
    await page.getByRole("button", { name: "Ava menüü" }).click();
  }
  const menu = page.getByRole("navigation", { name: "Peamenüü" }).last();
  await menu.getByRole("link", { name: "Nähtavus" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Mõju ja nähtavus");
});

test("the visibility page renders its sections and its empty states", async ({ page }) => {
  const errors = watchConsole(page);

  await openVisibility(page);

  await expect(page.getByRole("heading", { name: "Praegune seis", exact: true })).toBeVisible();
  await expect(page.getByText("Google Analytics ei ole ühendatud.").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("the newsletters are no longer on this page", async ({ page }) => {
  // They moved to Uudised. The band here keeps the website and the four social
  // channels; the overview's own band still carries Uudiskirjad, which is why
  // this asserts on Nähtavus rather than on the shell.
  await openVisibility(page);

  await expect(page.getByRole("heading", { name: "Uudiskirjad", exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Uudiskirjade tulemused", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Otsi uudiskirja")).toHaveCount(0);

  // And the half that stayed is still here.
  await expect(
    page.getByRole("heading", { name: "Kodulehe külastused", exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Facebooki jälgijad", exact: true }),
  ).toBeVisible();
});

test("the visibility page shows no fabricated audience figure", async ({ page }) => {
  await openVisibility(page);

  const text = await page.evaluate(() => document.querySelector("main").innerText);

  // The stale thresholds are the only digits an empty page states, and they
  // describe the freshness rule rather than an audience.
  const withoutThresholds = text.replace(/Vananenuks märgitakse pärast \d+ päeva\./g, "");
  expect(withoutThresholds).not.toMatch(/\d/);
});

test("an ordinary viewer sees no data-entry control", async ({ page }) => {
  await openVisibility(page);

  await expect(page.getByRole("link", { name: "Lisa andmed" })).toHaveCount(0);
});

test("the band leaves no empty cell at any width", async ({ page }) => {
  /*
   * The strip paints `bg-border` behind its cells and separates them with a
   * one-pixel gap, so a grid track with no card in it is not blank — it is a
   * grey block the width of a card. That is what appeared on the right of this
   * band when the newsletter card moved to Uudised and left five cards in a
   * six-column grid.
   *
   * The invariant is the one the stylesheet's own comments describe for every
   * strip: the column count divides the card count into full rows. Asserted
   * from the computed style rather than from the class name, because the class
   * is only a promise about what the columns will be, and it runs at every
   * configured viewport, so a breakpoint that divides ragged fails on its own.
   */
  await openVisibility(page);

  const band = await page.evaluate(() => {
    const strip = document.querySelector(".dk-kpi-strip");
    return {
      cards: strip.children.length,
      columns: getComputedStyle(strip).gridTemplateColumns.split(" ").length,
    };
  });

  expect(
    band.cards % band.columns,
    `${band.cards} cards in a ${band.columns}-column grid leaves ` +
      `${band.columns - (band.cards % band.columns)} empty cell(s) showing the strip's background`,
  ).toBe(0);
});

test("the visibility page never scrolls sideways", async ({ page }) => {
  await openVisibility(page);

  await expectNoHorizontalOverflow(page);
});

test("the visibility page stays usable at 200% zoom", async ({ page }) => {
  // Measured from the desktop viewport only, as the shell suite does. Halving
  // 320 px would ask the layout to hold up at 160, which is below every width
  // the design system supports and is not what 200% zoom means to a reader.
  test.skip(page.viewportSize().width < 1024, "measured from the desktop viewport");

  await openVisibility(page);

  // Browser zoom halves the CSS-pixel viewport, so it is emulated by halving the
  // viewport rather than by setting CSS zoom, which does not scale the layout
  // viewport and makes overflow measurements meaningless.
  const { width, height } = page.viewportSize();
  await page.setViewportSize({ width: Math.round(width / 2), height: Math.round(height / 2) });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
