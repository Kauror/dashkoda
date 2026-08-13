import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The newsletter material on the Uudised page, and the archive behind it.
 *
 * It was under Nähtavus until the two were separated: a reader asking what the
 * Chamber published is already here, and Nähtavus keeps the website and the
 * social channels. `visibility.spec.js` holds the other half of that — that
 * none of this is still over there.
 *
 * CI runs against a container with an empty database, so what these assert is
 * the layout, the controls and the *truthful empty state*. A newsletter nobody
 * has collected must read as missing, never as zero.
 */

/** Sign in, then open Uudised. `signIn` always lands on the overview. */
async function openNews(page) {
  await signIn(page);
  await page.goto("/uudised/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
}

test("the newsletters are on the news page", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page);

  // The audience card, then the analytics section under it.
  await expect(page.getByRole("heading", { name: "Uudiskirjad", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Uudiskirjade tulemused", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("the news archive is still here and still first", async ({ page }) => {
  await openNews(page);

  // The archive's own controls, unchanged by the arrival of the newsletters.
  await expect(page.getByRole("navigation", { name: "Avaldamisperiood" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Uudise liik" })).toBeVisible();
  await expect(page.getByLabel("Otsi uudist")).toBeVisible();

  // And it comes before the newsletter material rather than being wrapped in it.
  const main = await page.evaluate(() => document.querySelector("main").innerText);
  expect(main.indexOf("Uudiste arhiiv")).toBeLessThan(main.indexOf("Uudiskirjade tulemused"));
});

test("the newsletter filter chips work", async ({ page }) => {
  await openNews(page);

  const chips = page.getByRole("navigation", { name: "Uudiskiri" });
  await expect(chips).toBeVisible();
  await chips.getByRole("link", { name: "e-Teataja", exact: true }).click();

  await expect(page).toHaveURL(/uudiskiri=newsletter_eteataja/);
  // The archive above is still on the page it was.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
});

test("a newsletter chip keeps the news archive's own state", async ({ page }) => {
  // The two sections share one page and one address bar. Choosing a newsletter
  // must not silently reset the period the reader had picked.
  await signIn(page);
  await page.goto("/uudised/?periood=1a");

  await page
    .getByRole("navigation", { name: "Uudiskiri" })
    .getByRole("link", { name: "e-Teataja", exact: true })
    .click();

  await expect(page).toHaveURL(/periood=1a/);
  await expect(page).toHaveURL(/uudiskiri=newsletter_eteataja/);
});

test("the newsletter search filters in place and keeps the caret", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page);

  const box = page.getByLabel("Otsi uudiskirja");
  await box.click();

  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/uudised/otsi/uudiskirjad/")),
    box.pressSequentially("foorum", { delay: 60 }),
  ]);

  expect(response.status()).toBe(200);
  await expect(box).toHaveValue("foorum");
  await expect(box).toBeFocused();
  expect(errors).toEqual([]);
});

test("the newsletter search pushes a Uudised address, never a Nähtavus one", async ({ page }) => {
  // The single assertion the move exists for: the fragment used to push
  // `/nahtavus/`, which would now name a page with no newsletter box on it.
  await openNews(page);

  await page.getByLabel("Otsi uudiskirja").pressSequentially("foorum", { delay: 60 });

  await expect(page).toHaveURL(/otsi=foorum/);
  await expect(page).toHaveURL(/\/uudised\//);
  await expect(page).not.toHaveURL(/\/nahtavus\//);
});

test("the news search still works with the newsletters below it", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page);

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

test("the news page shows no fabricated newsletter figure", async ({ page }) => {
  await openNews(page);

  await expect(page.getByText("Andmed puuduvad.").first()).toBeVisible();
  await expect(
    page.getByText("Saadetud uudiskirjad ilmuvad siia pärast esimest Smaily kogumist."),
  ).toBeVisible();
});

test("the news page never scrolls sideways", async ({ page }) => {
  await openNews(page);

  await expectNoHorizontalOverflow(page);
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
