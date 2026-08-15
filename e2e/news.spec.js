import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The Uudised intelligence dashboard and its four focus views.
 *
 * There were five. The newsletters were the fourth and are `Otsepostitused`
 * now, at their own address under Koduleht — `otsepostitused.spec.js` holds
 * that half, including that none of it is still here.
 *
 * The page used to be one scroll: an archive table with the newsletter material
 * beneath it. It is now four focus views over one address, so what these assert
 * is which view holds what, that the focus links navigate, and that state
 * survives moving between them.
 *
 * CI runs against a container with an empty database, so what these assert is
 * the layout, the controls and the *truthful empty state*.
 */

/** The four focus views, by the parameter that selects each. */
const OVERVIEW = "/uudised/";
const IMPACT = "/uudised/?fookus=moju";
const PUBLISHING = "/uudised/?fookus=avaldamine";
const ARCHIVE = "/uudised/?fookus=arhiiv";

/** Sign in, then open one focus of Uudised. */
async function openNews(page, url = OVERVIEW) {
  await signIn(page);
  await page.goto(url);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
}

test("the four focus views are all reachable as links", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page);

  const nav = page.getByRole("navigation", { name: "Vaade" });
  await expect(nav).toBeVisible();
  for (const label of ["Ülevaade", "Uudiste mõju", "Avaldamine", "Arhiiv"]) {
    await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
  // The retired fifth is gone from the navigation entirely.
  await expect(nav.getByRole("link", { name: "Uudiskirjad", exact: true })).toHaveCount(0);

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

test("the retired newsletter focus leaves for Otsepostitused", async ({ page }) => {
  // A real bookmark. Falling through to the overview would render happily and
  // tell the reader nothing about where what they asked for went.
  await signIn(page);
  await page.goto("/uudised/?fookus=uudiskirjad");

  await expect(page).toHaveURL(/\/otsepostitused\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Otsepostitused");
});

test("the overview leads with the dashboard, not with the archive", async ({ page }) => {
  // The regression this redesign is for. The archive answers "find me the
  // article about excise duty" and answered "how are we doing" somewhere in the
  // middle of itself, so it is no longer the first thing on the page.
  await openNews(page);

  await expect(page.getByLabel("Otsi uudist")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Vaade" })).toBeVisible();
});

test("the news page renders no newsletter material at all", async ({ page }) => {
  // The move out, asserted the way the move off Nähtavus was: not the section
  // heading, not the card, not the subject search.
  await openNews(page);

  await expect(page.getByRole("heading", { name: "Uudiskirjad", exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Uudiskirjade tulemused", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Otsi uudiskirja")).toHaveCount(0);
});

test("the archive keeps every control it had", async ({ page }) => {
  await openNews(page, ARCHIVE);

  await expect(page.getByRole("navigation", { name: "Avaldamisperiood" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Uudise liik" })).toBeVisible();
  await expect(page.getByLabel("Otsi uudist")).toBeVisible();
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

test("no focus view scrolls sideways", async ({ page }) => {
  // Every view, at every width the suite runs. Long article titles, a wide
  // category table and the focus navigation itself are each capable of widening
  // the page, and only one of them is on screen at a time.
  await signIn(page);
  for (const url of [OVERVIEW, IMPACT, PUBLISHING, ARCHIVE]) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
    await expectNoHorizontalOverflow(page);
  }
});
