import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * `Otsepostitused` — the Chamber's newsletter section, under Koduleht.
 *
 * The Smaily material has moved twice: it was Nähtavus's, then Uudised's. This
 * suite is the browser half of the second move — that the section renders at its
 * own address, that its two views navigate between each other, and that every
 * retired address still arrives. `news.spec.js` and `visibility.spec.js` hold
 * the other side: that neither of those pages still renders any of it.
 *
 * CI runs against a container with an empty database, so what these assert is
 * the layout, the controls and the *truthful empty state*. A newsletter nobody
 * has collected must read as missing, never as zero.
 */

const OVERVIEW = "/otsepostitused/";
// `/otsepostitused/ajalugu/` is a redirect since 2026-08-16, not a view.
const OLD_HISTORY = "/otsepostitused/ajalugu/";

async function openMailings(page, url = OVERVIEW) {
  await signIn(page);
  await page.goto(url);
}

test("the section renders at its own address", async ({ page }) => {
  const errors = watchConsole(page);

  await openMailings(page);

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Otsepostitused",
  );
  // The old audience card folded into `Kanalid` on 2026-08-18 — see
  // apps/visibility/mailings_page.py — then the analytics section under it.
  await expect(
    page.getByRole("heading", { name: "Kanalid", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Uudiskirjade tulemused", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("one view, and the address the second one left still answers", async ({
  page,
}) => {
  // `Saadetised` merged into `Ülevaade` on 2026-08-16. There is no view
  // navigation left to click, and the archive is a section of this page.
  await openMailings(page);

  await expect(page.getByRole("navigation", { name: "Vaade" })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Otsepostitused",
  );

  await page.goto(OLD_HISTORY);
  await expect(page).toHaveURL(/\/otsepostitused\/$|\/otsepostitused\/#/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Otsepostitused",
  );
});

test("the newsletter filter chips work", async ({ page }) => {
  await openMailings(page);

  const chips = page.getByRole("navigation", { name: "Uudiskiri" });
  await expect(chips).toBeVisible();
  await chips.getByRole("link", { name: "e-Teataja", exact: true }).click();

  await expect(page).toHaveURL(/uudiskiri=newsletter_eteataja/);
  // Still in the section rather than back at the landing state.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Otsepostitused",
  );
});

test("a filter chip carries no news parameter", async ({ page }) => {
  // While this section sat on `/uudised/` its chips carried the article
  // archive's period so a click could not reset it. There is no archive here,
  // and an address holding keys the page cannot read back is an address that
  // lies about what is on screen.
  await signIn(page);
  await page.goto("/otsepostitused/?periood=1a");

  await page
    .getByRole("navigation", { name: "Uudiskiri" })
    .getByRole("link", { name: "e-Teataja", exact: true })
    .click();

  await expect(page).toHaveURL(/uudiskiri=newsletter_eteataja/);
  await expect(page).not.toHaveURL(/periood=1a/);
});

test("the section shows no fabricated newsletter figure", async ({ page }) => {
  await openMailings(page);

  // `Kanalid`'s own missing-vs-zero state: a dash for every rate and every
  // subscriber count no reading exists for, never a `0`. The three rows
  // still render, each under its own newsletter name.
  const channels = page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Kanalid", exact: true }),
    });
  for (const label of ["e-Teataja", "eNews", "e-Vestnik"]) {
    await expect(
      channels.getByRole("rowheader", { name: label, exact: true }),
    ).toBeVisible();
  }
  await expect(
    page.getByText(
      "Saadetud uudiskirjad ilmuvad siia pärast esimest Smaily kogumist.",
    ),
  ).toBeVisible();
});

/*
 * The subject search is deliberately not driven here.
 *
 * It is inside the `has_any_data` guard, so with no campaigns collected the
 * section renders its empty state and the box does not exist — and
 * `seed_e2e_data` creates no Smaily data in either suite. Its behaviour is
 * pinned where it can be pinned honestly:
 *
 *   - `tests/visibility/test_mailings_fragments.py` drives the fragment with
 *     real campaigns, including that it pushes `/otsepostitused/`;
 *   - `live-search.spec.js` drives the same live-search mechanics in a real
 *     browser on the send archive, whose form renders unconditionally.
 */

test("every retired address arrives in this section", async ({ page }) => {
  await signIn(page);

  for (const [old, expected] of [
    ["/uudised/?fookus=uudiskirjad", /\/otsepostitused\/$/],
    ["/uudised/uudiskirjad/", /\/otsepostitused\/$/],
    ["/nahtavus/uudiskirjad/", /\/otsepostitused\/$/],
  ]) {
    await page.goto(old);
    await expect(page).toHaveURL(expected);
  }
});

test("the page does not scroll sideways", async ({ page }) => {
  await signIn(page);
  for (const url of [OVERVIEW]) {
    await page.goto(url);
    await expectNoHorizontalOverflow(page);
  }
});
