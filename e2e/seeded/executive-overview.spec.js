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

test("the executive status fills with figures rather than empty states", async ({ page }) => {
  const errors = watchConsole(page);

  const status = page.getByRole("region", { name: "Koja seis" });

  // Five strategic areas, each answering its own question. The Nähtavus pillar
  // is `Koduleht ja uudised`: the old product name is retired and must not come
  // back on the front page.
  for (const pillar of [
    "Liikmeskond",
    "Huvikaitse",
    "Kaasamine",
    "Koduleht ja uudised",
    "Digiteenused",
  ]) {
    await expect(status.getByRole("heading", { name: pillar, level: 3 })).toBeVisible();
  }

  // Not a single pillar may be showing the unconnected state.
  await expect(page.getByText("Andmeallikas ei ole ühendatud.")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the membership pillar leads with the public directory count", async ({ page }) => {
  const status = page.getByRole("region", { name: "Koja seis" });
  const pillar = status.locator("article", { hasText: "Liikmeskond" }).first();

  await expect(pillar.getByText("Liikmeid kokku")).toBeVisible();
  await expect(pillar.getByText("Koda.ee liikmekataloog")).toBeVisible();
  // The board report contributes ratios, each naming itself as their source —
  // never a second total under the same words.
  await expect(pillar.getByText("Koja sisemine liikmeskonna aruanne").first()).toBeVisible();
  await expect(pillar.getByText("Liikmeid kokku · koja aruanne")).toHaveCount(0);
});

test("the attention section renders exactly one of its two valid states", async ({ page }) => {
  /*
   * Signals or silence, never both and never neither. The section is scoped by
   * its heading rather than by a class, so this also holds the heading itself.
   *
   * The seeded legal register carries deadlines two and five days out and one
   * overdue matter whose opinion has not gone, so signals are expected here —
   * but the assertion is written as the contract rather than as that
   * expectation. A seed that stopped producing one should fail on the *count*
   * assertion below with a readable number, not on a locator that silently
   * resolved to nothing.
   */
  const attention = page.getByRole("region", { name: "Mis vajab tähelepanu?" });
  await expect(attention).toBeVisible();

  const quiet = attention.getByText("Olulisi muutusi või lähenevaid tähtaegu ei ole.");
  const rows = attention.locator("li");

  const [quietCount, rowCount] = await Promise.all([quiet.count(), rows.count()]);

  // Exactly one state. Both would mean the template lost its branch; neither
  // would mean the section rendered empty, which says nothing to a reader.
  expect({ quiet: quietCount, signals: rowCount }).toEqual(
    rowCount > 0 ? { quiet: 0, signals: rowCount } : { quiet: 1, signals: 0 },
  );

  if (rowCount === 0) {
    return;
  }

  // Bounded: an exception list, not another dashboard.
  expect(rowCount).toBeLessThanOrEqual(5);

  // Urgency is a word before it is a colour, so it survives greyscale, a
  // printer and a reader who cannot separate the two warning tones. Every row
  // carries one, not just the first.
  const priorities = ["Kiireloomuline", "Tähelepanu", "Tähelepanuväärne"];
  const texts = await rows.allInnerTexts();
  for (const text of texts) {
    expect(priorities.some((word) => text.includes(word))).toBe(true);
    // And evidence beneath the claim: a headline with no measurement under it
    // is an assertion the reader cannot check.
    expect(text.trim().split("\n").length).toBeGreaterThan(1);
  }
});

test("the seeded register produces at least one signal", async ({ page }) => {
  /*
   * Separate from the contract test above on purpose. This one is about the
   * *seed*: if it stops producing a signal, the section's populated branch is
   * never exercised by any browser run, and the contract test would pass on the
   * quiet state forever without anyone noticing.
   */
  const attention = page.getByRole("region", { name: "Mis vajab tähelepanu?" });

  await expect(attention.locator("li").first()).toBeVisible();
});

test("the timeline is chronological and every row is dated", async ({ page }) => {
  const timeline = page.getByRole("region", { name: "Järgmised 30 päeva" });

  await expect(timeline.getByText("Lähiajal ei ole tähtaegu ega sündmusi.")).toHaveCount(0);

  const stamps = await timeline.locator("time").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("datetime")),
  );

  expect(stamps.length).toBeGreaterThan(0);
  expect(stamps.length).toBeLessThanOrEqual(10);
  expect([...stamps].sort()).toEqual(stamps);
});

test("each interest panel states its own metric and its own period", async ({ page }) => {
  const interest = page.getByRole("region", { name: "Praegu huvi pakkuv" });

  for (const domain of ["Koduleht", "Uudised", "Sündmused", "E-pood"]) {
    await expect(interest.getByRole("heading", { name: domain, level: 3 })).toBeVisible();
  }
  // Four different metrics. Nothing ranks them against one another, so no panel
  // may borrow another's unit.
  await expect(interest.getByText("lehevaatamist")).toBeVisible();
  await expect(interest.getByText("ühikut soetatud")).toBeVisible();
});

test("a failed refresh keeps the figures and discloses itself", async ({ page }) => {
  /**
   * The seed marks the news feed as having failed its most recent check. Both
   * halves matter and they pull in opposite directions: the data must not be
   * withdrawn, and the page must not pretend the source is current.
   */
  const status = page.getByRole("region", { name: "Andmete seis" });

  await expect(status.getByText("Vananenud pärast ebaõnnestunud uuendust")).toBeVisible();
  // The pillar the failed feed contributes to still shows its figures.
  const pillars = page.getByRole("region", { name: "Koja seis" });
  const visibility = pillars.locator("article", { hasText: "Koduleht ja uudised" }).first();
  await expect(visibility.getByText("Kodulehe seansid")).toBeVisible();
});

test("the channel audiences are never totalled", async ({ page }) => {
  const channels = page.getByRole("region", { name: "Kanalite auditoorium" });

  await expect(channels).toBeVisible();
  for (const forbidden of ["Kokku auditoorium", "Auditoorium kokku", "Kogu auditoorium"]) {
    await expect(channels.getByText(forbidden)).toHaveCount(0);
  }
});

test("the page never scrolls sideways with content on it", async ({ page }) => {
  await expectNoHorizontalOverflow(page);
});
