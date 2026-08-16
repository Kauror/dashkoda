import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "../helpers.js";

/**
 * `Koja töölaud` against seeded data.
 *
 * The empty-database suite in `e2e/shell.spec.js` proves the page survives
 * having no sources. This one proves it says something once it has them, which
 * is the half that a green empty suite has never been able to show: every
 * assertion below is invisible until real figures, a real signal and real dated
 * work exist.
 */

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

/*
 * Three tests were removed on 2026-08-16 with the sections they covered:
 * two on `Mis vajab tähelepanu?` (its one-of-two-valid-states contract and the
 * seeded-signal count) and one on `Praegu huvi pakkuv` (each panel naming its
 * own metric and period).
 *
 * The rules they held are not orphaned. `collect_signals` and
 * `_interest_panels` are untouched and still unit-tested; what is gone is the
 * only place a reader saw them, so there is nothing left to assert in a
 * browser. If either section returns, these are the contracts to restore.
 */

test("the executive status fills with figures rather than empty states", async ({ page }) => {
  const errors = watchConsole(page);

  const status = page.getByRole("region", { name: "Koja seis" });

  // Four strategic areas. The Nähtavus pillar is `Koduleht ja uudised` — the
  // old product name is retired and must not come back on the front page — and
  // Digiteenused is deliberately absent: the board removed the card on
  // 2026-08-15, while the shop keeps its interest panel, its signals and its
  // Andmete seis row.
  for (const pillar of ["Liikmeskond", "Koduleht ja uudised"]) {
    await expect(status.getByRole("heading", { name: pillar, level: 3 })).toBeVisible();
  }
  // Three cards have been removed at the owner's request rather than lost:
  // Digiteenused on 2026-08-15, Huvikaitse and Kaasamine on 2026-08-16. Each
  // domain keeps its own dashboard, its signals and its Andmete seis row.
  for (const gone of ["Digiteenused", "Huvikaitse", "Kaasamine"]) {
    await expect(status.getByRole("heading", { name: gone, level: 3 })).toHaveCount(0);
  }

  // Not a single pillar may be showing the unconnected state.
  await expect(page.getByText("Andmeallikas ei ole ühendatud.")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the membership pillar leads with the public directory count", async ({ page }) => {
  /*
   * The card prints one total and no captions. The question lines and the
   * period · source · seis rows came off every pillar on 2026-08-15, so what
   * holds the "never two unlabelled totals" rule now is that the directory
   * count is the only member total on the card at all — the report contributes
   * ratios and a joined/removed pair, and Andmete seis says which source is
   * which.
   */
  const status = page.getByRole("region", { name: "Koja seis" });
  const pillar = status.locator("article", { hasText: "Liikmeskond" }).first();

  await expect(pillar.getByText("liiget")).toBeVisible();
  await expect(pillar.getByText("Tasunud liikmete osakaal")).toBeVisible();
  // The struck chrome must stay gone.
  await expect(pillar.getByText("Liikmeid kokku")).toHaveCount(0);
  await expect(pillar.getByText("Koda.ee liikmekataloog")).toHaveCount(0);
  await expect(pillar.getByText("Koja sisemine liikmeskonna aruanne")).toHaveCount(0);
});

test("the timeline is chronological and every row is dated", async ({ page }) => {
  const timeline = page.getByRole("region", { name: "Eesolevad tegevused" });

  await expect(timeline.getByText("Lähiajal ei ole tähtaegu ega sündmusi.")).toHaveCount(0);

  const stamps = await timeline.locator("time").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("datetime")),
  );

  expect(stamps.length).toBeGreaterThan(0);
  expect(stamps.length).toBeLessThanOrEqual(10);
  expect([...stamps].sort()).toEqual(stamps);
});

test("a failed refresh keeps the figures and discloses itself", async ({ page }) => {
  /**
   * The seed marks the news feed as having failed its most recent check. Both
   * halves matter and they pull in opposite directions: the data must not be
   * withdrawn, and the page must not pretend the source is current.
   */
  /* The disclosure moved to `/haldus/` on 2026-08-15 with `Andmete seis`; the
     figures staying put on the overview is the half that must never move. Both
     are checked, because a disclosure deleted from one page and never rendered
     on the other would satisfy the overview half on its own. */
  await expect(page.locator("main")).not.toContainText("Andmete seis");

  // The pillar the failed feed contributes to still shows its figures. Its
  // caption `Kodulehe seansid` came off the card on 2026-08-15 with the rest of
  // the per-figure chrome, so what proves the data was not withdrawn is the
  // unit beside the number.
  const pillars = page.getByRole("region", { name: "Koja seis" });
  const visibility = pillars.locator("article", { hasText: "Koduleht ja uudised" }).first();
  await expect(visibility.getByText("külastust")).toBeVisible();
  await expect(visibility.getByText("Kodulehe külastused")).toHaveCount(0);

  await page.goto("/haldus/");
  const status = page.getByRole("region", { name: "Andmete seis" });
  await expect(status.getByText("Vananenud pärast ebaõnnestunud uuendust")).toBeVisible();
});

test("the channel audiences are never totalled", async ({ page }) => {
  const channels = page.getByRole("region", { name: "Kanalid" });

  await expect(channels).toBeVisible();
  for (const forbidden of ["Kokku auditoorium", "Auditoorium kokku", "Kogu auditoorium"]) {
    await expect(channels.getByText(forbidden)).toHaveCount(0);
  }
});

test("the page never scrolls sideways with content on it", async ({ page }) => {
  await expectNoHorizontalOverflow(page);
});
