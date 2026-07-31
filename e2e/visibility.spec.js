import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The Nähtavus page and the overview's six-slot channel band.
 *
 * CI runs against a container with an empty database, so every assertion here is
 * about the *truthful empty state* and the layout. That is the state a fresh
 * deployment is in, and it is the one most likely to be got wrong: a band with
 * nothing in it must show no figure at all rather than a row of zeros.
 */

const CHANNELS = [
  "Kodulehe külastused",
  "Uudiskirja saajad",
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

  for (const section of [
    "Praegune seis",
    "Uudiskirja auditoorium",
    "Sotsiaalmeedia",
    "Vaatluste ajalugu",
    "Allikate määratlused",
  ]) {
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
  }
  await expect(page.getByText("Google Analytics ei ole ühendatud.").first()).toBeVisible();
  expect(errors).toEqual([]);
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

test("the visibility page never scrolls sideways, including at 200% zoom", async ({ page }) => {
  await openVisibility(page);
  await expectNoHorizontalOverflow(page);

  // Browser zoom halves the CSS-pixel viewport, so it is emulated by halving the
  // viewport rather than by setting CSS zoom, which does not scale the layout
  // viewport and makes overflow measurements meaningless.
  const { width, height } = page.viewportSize();
  await page.setViewportSize({ width: Math.round(width / 2), height: Math.round(height / 2) });
  await expectNoHorizontalOverflow(page);
});
