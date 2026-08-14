import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The Uudised intelligence dashboard: its five focus views, and the newsletter
 * material that lives on the fourth of them.
 *
 * The newsletters were under Nähtavus until the two were separated — a reader
 * asking what the Chamber published is already here, and Nähtavus keeps the
 * website and the social channels. `visibility.spec.js` holds the other half of
 * that: that none of this is still over there.
 *
 * The page used to be one scroll: an archive table with the newsletter material
 * beneath it. It is now five focus views over one address, so what these assert
 * is which view holds what, that the focus links navigate, and that state
 * survives moving between them.
 *
 * CI runs against a container with an empty database, so what these assert is
 * the layout, the controls and the *truthful empty state*. A newsletter nobody
 * has collected must read as missing, never as zero.
 */

/** The five focus views, by the parameter that selects each. */
const OVERVIEW = "/uudised/";
const IMPACT = "/uudised/?fookus=moju";
const PUBLISHING = "/uudised/?fookus=avaldamine";
const NEWSLETTERS = "/uudised/?fookus=uudiskirjad";
const ARCHIVE = "/uudised/?fookus=arhiiv";

/** Sign in, then open one focus of Uudised. */
async function openNews(page, url = OVERVIEW) {
  await signIn(page);
  await page.goto(url);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
}

test("the five focus views are all reachable as links", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page);

  const nav = page.getByRole("navigation", { name: "Vaade" });
  await expect(nav).toBeVisible();
  for (const label of ["Ülevaade", "Uudiste mõju", "Avaldamine", "Uudiskirjad", "Arhiiv"]) {
    await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
  }

  // Ordinary navigation: a real URL, so back returns to the overview.
  await nav.getByRole("link", { name: "Arhiiv", exact: true }).click();
  await expect(page).toHaveURL(/fookus=arhiiv/);
  await page.goBack();
  await expect(page).not.toHaveURL(/fookus=arhiiv/);
  expect(errors).toEqual([]);
});

test("an unreadable focus renders the overview rather than an error", async ({ page }) => {
  await signIn(page);
  await page.goto("/uudised/?fookus=zzz");

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
  await expect(page.getByRole("navigation", { name: "Vaade" })).toBeVisible();
});

test("the overview leads with the dashboard, not with the archive", async ({ page }) => {
  // The regression this redesign is for. The archive answers "find me the
  // article about excise duty" and answered "how are we doing" somewhere in the
  // middle of itself, so it is no longer the first thing on the page.
  await openNews(page);

  await expect(page.getByLabel("Otsi uudist")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Vaade" })).toBeVisible();
});

test("the archive keeps every control it had", async ({ page }) => {
  await openNews(page, ARCHIVE);

  await expect(page.getByRole("navigation", { name: "Avaldamisperiood" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Uudise liik" })).toBeVisible();
  await expect(page.getByLabel("Otsi uudist")).toBeVisible();
});

test("the newsletters are on the news page", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page, NEWSLETTERS);

  // The audience card, then the analytics section under it.
  await expect(page.getByRole("heading", { name: "Uudiskirjad", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Uudiskirjade tulemused", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("the newsletter filter chips work", async ({ page }) => {
  await openNews(page, NEWSLETTERS);

  const chips = page.getByRole("navigation", { name: "Uudiskiri" });
  await expect(chips).toBeVisible();
  await chips.getByRole("link", { name: "e-Teataja", exact: true }).click();

  await expect(page).toHaveURL(/uudiskiri=newsletter_eteataja/);
  // Still on the newsletter focus rather than back at the overview.
  await expect(page).toHaveURL(/fookus=uudiskirjad/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
});

test("a newsletter chip keeps the news archive's own state", async ({ page }) => {
  // The two sections no longer share a screen and still share an address bar.
  // Choosing a newsletter must not silently reset the period the reader picked
  // in the archive, which they will be returned to when they switch back.
  await signIn(page);
  await page.goto("/uudised/?fookus=uudiskirjad&periood=1a");

  await page
    .getByRole("navigation", { name: "Uudiskiri" })
    .getByRole("link", { name: "e-Teataja", exact: true })
    .click();

  await expect(page).toHaveURL(/periood=1a/);
  await expect(page).toHaveURL(/uudiskiri=newsletter_eteataja/);
});

test("switching focus keeps the archive's period", async ({ page }) => {
  await signIn(page);
  await page.goto("/uudised/?fookus=arhiiv&periood=1a");

  await page
    .getByRole("navigation", { name: "Vaade" })
    .getByRole("link", { name: "Avaldamine", exact: true })
    .click();

  await expect(page).toHaveURL(/fookus=avaldamine/);
  await expect(page).toHaveURL(/periood=1a/);
});

/*
 * The newsletter section's own subject search is deliberately not driven here.
 *
 * It is inside the `has_any_data` guard, so with no campaigns collected the
 * section renders its empty state and the box does not exist — and
 * `seed_e2e_data` creates no Smaily data in either suite. Adding some to make a
 * browser assertion possible would change what the seeded suite means elsewhere,
 * for a control whose behaviour is already pinned where it can be pinned
 * honestly:
 *
 *   - `tests/news/test_newsletter_fragments.py` drives the fragment with real
 *     campaigns, including that it pushes `/uudised/` and never `/nahtavus/`;
 *   - `live-search.spec.js` drives the same live-search mechanics in a real
 *     browser on the archive page, whose form renders unconditionally.
 *
 * What is browser-checked here is what only a browser can answer: the views
 * being reachable, the chips navigating, and the layout holding.
 */

test("the news search still works inside the archive focus", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page, ARCHIVE);

  const box = page.getByLabel("Otsi uudist");
  await box.click();

  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/uudised/otsi/")),
    box.pressSequentially("eeln", { delay: 60 }),
  ]);

  expect(response.status()).toBe(200);
  await expect(box).toHaveValue("eeln");
  await expect(box).toBeFocused();
  expect(errors).toEqual([]);
});

test("a search inside the archive stays inside the archive", async ({ page }) => {
  // The pushed URL has to keep the focus, or reloading after a search would
  // land the reader on the overview with their search gone.
  await openNews(page, ARCHIVE);

  const box = page.getByLabel("Otsi uudist");
  await box.click();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/uudised/otsi/")),
    box.pressSequentially("eeln", { delay: 60 }),
  ]);

  await expect(page).toHaveURL(/fookus=arhiiv/);
});

test("the news page shows no fabricated newsletter figure", async ({ page }) => {
  await openNews(page, NEWSLETTERS);

  await expect(page.getByText("Andmed puuduvad.").first()).toBeVisible();
  await expect(
    page.getByText("Saadetud uudiskirjad ilmuvad siia pärast esimest Smaily kogumist."),
  ).toBeVisible();
});

test("no focus view scrolls sideways", async ({ page }) => {
  // Every view, at every width the suite runs. Long article titles, a wide
  // category table and the focus navigation itself are each capable of widening
  // the page, and only one of them is on screen at a time.
  await signIn(page);
  for (const url of [OVERVIEW, IMPACT, PUBLISHING, NEWSLETTERS, ARCHIVE]) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
    await expectNoHorizontalOverflow(page);
  }
});

test("the newsletter archive is a news page and returns to Uudised", async ({ page }) => {
  await signIn(page);
  await page.goto("/uudised/uudiskirjad/");

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Saadetud uudiskirjad");
  await expect(page.getByRole("link", { name: /Tagasi uudiste lehele/ })).toBeVisible();

  await page.getByRole("link", { name: /Tagasi uudiste lehele/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
});

test("the newsletter archive never scrolls sideways", async ({ page }) => {
  await signIn(page);
  await page.goto("/uudised/uudiskirjad/");

  await expectNoHorizontalOverflow(page);
});
