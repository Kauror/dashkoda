import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The Koduleht page and the overview's audience strip.
 *
 * CI runs against a container with an empty database, so every assertion here is
 * about the *truthful empty state* and the layout. That is the state a fresh
 * deployment is in, and it is the one most likely to be got wrong: a band with
 * nothing in it must show no figure at all rather than a row of zeros, and a
 * page with no collected day must say so rather than draw an empty axis.
 *
 * The website page was `Nähtavus` and carried a five-slot social channel band
 * above its traffic section. It is `Koduleht` now and answers questions about
 * the website; the four hand-entered social figures are untouched and are still
 * on the overview's strip, which is what the first block below covers. The
 * newsletters left earlier and are `Otsepostitused` now — see
 * `otsepostitused.spec.js`.
 *
 * The strip had a sixth slot, `Kodulehe külastused`, until 2026-08-17. It was
 * the front page's only consumer and the rebuilt `Koduleht ja uudised` card
 * states the same measure over a properly measured window, so the slot went
 * rather than saying one thing twice under two labels. Sessions are visits, not
 * an audience, which is the other half of why the strip is better without it.
 */

const CHANNELS = [
  "e-Teataja uudiskiri",
  "eNews uudiskiri",
  "e-Vestnik uudiskiri",
  "Facebooki jälgijad",
  "LinkedIni jälgijad",
  "Instagrami jälgijad",
  "YouTube’i tellijad",
];

/** Sign in, then open Koduleht. `signIn` always lands on the overview. */
async function openKoduleht(page) {
  await signIn(page);
  await page.goto("/koduleht/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koduleht");
}

test("the overview strip names every audience and no website figure", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);

  // One row per audience since 2026-08-18: the three lists were three sub-rows
  // of one cell, which made them look like parts of one audience. A row is a
  // term in a description list rather than a heading.
  for (const channel of CHANNELS) {
    await expect(page.getByText(channel, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText("Kodulehe külastused")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the audience strip never scrolls the overview sideways", async ({ page }) => {
  await signIn(page);

  await expectNoHorizontalOverflow(page);
});

test("Koduleht is reachable from the navigation", async ({ page }) => {
  await signIn(page);

  if (page.viewportSize().width < 1024) {
    await page.getByRole("button", { name: "Ava menüü" }).click();
  }
  const menu = page.getByRole("navigation", { name: "Peamenüü" }).last();
  await menu.getByRole("link", { name: "Koduleht" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koduleht");
});

test("the old address still reaches the page", async ({ page }) => {
  // A board member who bookmarked `/nahtavus/` should arrive, not meet a 404.
  await signIn(page);
  await page.goto("/nahtavus/?periood=90");

  await expect(page).toHaveURL(/\/koduleht\//);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koduleht");
});

test("an uncollected source says so rather than drawing an empty chart", async ({ page }) => {
  const errors = watchConsole(page);

  await openKoduleht(page);

  await expect(page.getByText("Google Analyticsi andmeid ei ole veel kogutud.").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("the focus navigation offers every view", async ({ page }) => {
  await openKoduleht(page);

  const views = page.getByRole("navigation", { name: "Vaade" });
  for (const label of ["Ülevaade", "Sisu ja lehed", "Kanalid"]) {
    await expect(views.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
  // The two retired views are not offered — their bookmarks resolve, their
  // tabs are gone.
  for (const label of ["Liiklus", "Lehed"]) {
    await expect(views.getByRole("link", { name: label, exact: true })).toHaveCount(0);
  }
});

test("each focus view is a real URL that renders on its own", async ({ page }) => {
  // Not an SPA: every view is bookmarkable, shareable and reload-safe. The two
  // retired keys stay in the list on purpose: a saved link must keep rendering.
  await signIn(page);

  for (const focus of ["ulevaade", "liiklus", "sisu", "kanalid", "lehed"]) {
    await page.goto(`/koduleht/?fookus=${focus}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koduleht");
  }
});

test("an unknown focus renders the overview rather than an error", async ({ page }) => {
  await signIn(page);
  await page.goto("/koduleht/?fookus=ei-ole-olemas");

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koduleht");
});

test("the social channel band is not on this page", async ({ page }) => {
  // Koduleht answers questions about the website. The four typed figures are
  // not deleted and not hidden — they are on the overview's band, which the
  // first test in this file covers.
  await openKoduleht(page);

  await expect(page.getByRole("heading", { name: "Facebooki jälgijad", exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByRole("heading", { name: "Uudiskirjad", exact: true })).toHaveCount(0);
  await expect(page.getByText("Sotsiaalmeedia")).toHaveCount(0);
});

test("the page shows no fabricated audience figure", async ({ page }) => {
  await openKoduleht(page);

  const text = await page.evaluate(() => document.querySelector("main").innerText);

  // The stale thresholds are the only digits an empty page states, and they
  // describe the freshness rule rather than an audience.
  const withoutThresholds = text.replace(/Vananenuks märgitakse pärast \d+ päeva\./g, "");
  expect(withoutThresholds).not.toMatch(/\d/);
});

test("an empty page ships no chart bundle", async ({ page }) => {
  // The bundle loads only when the current view has something to draw.
  await openKoduleht(page);

  const scripts = await page.evaluate(() =>
    Array.from(document.querySelectorAll("script[src]")).map((node) => node.getAttribute("src")),
  );
  expect(scripts.some((src) => src.includes("charts.js"))).toBe(false);
});

test("an ordinary viewer sees no data-entry control", async ({ page }) => {
  await openKoduleht(page);

  await expect(page.getByRole("link", { name: "Lisa andmed" })).toHaveCount(0);
});

test("the page never scrolls sideways", async ({ page }) => {
  await openKoduleht(page);

  await expectNoHorizontalOverflow(page);
});

test("every focus view holds its width", async ({ page }) => {
  // The `sr-only` escape is this codebase's recurring layout bug, and a table
  // or a chart label is exactly where it reappears. Each view is measured.
  await signIn(page);

  for (const focus of ["ulevaade", "liiklus", "sisu", "kanalid", "lehed"]) {
    await page.goto(`/koduleht/?fookus=${focus}`);
    await expectNoHorizontalOverflow(page);
  }
});

test("the page stays usable at 200% zoom", async ({ page }) => {
  // Measured from the desktop viewport only, as the shell suite does. Halving
  // 320 px would ask the layout to hold up at 160, which is below every width
  // the design system supports and is not what 200% zoom means to a reader.
  test.skip(page.viewportSize().width < 1024, "measured from the desktop viewport");

  await openKoduleht(page);

  // Browser zoom halves the CSS-pixel viewport, so it is emulated by halving the
  // viewport rather than by setting CSS zoom, which does not scale the layout
  // viewport and makes overflow measurements meaningless.
  const { width, height } = page.viewportSize();
  await page.setViewportSize({ width: Math.round(width / 2), height: Math.round(height / 2) });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
