import { expect, test } from "@playwright/test";

import { TEST_PIN, signIn, watchConsole } from "./helpers.js";

/**
 * Search that filters while you type.
 *
 * These assertions exist because the interesting failures of a live search are
 * all in the browser and none of them are visible to a Django test: the caret
 * leaving the box on the first keystroke, the address bar going stale, a slow
 * answer landing after a fast one, and the whole thing simply not working with
 * the bundle blocked.
 *
 * The seeded suite has real rows; this one runs against an empty database, so
 * what it can hold is the mechanics rather than which rows come back.
 */

const ARCHIVE = "/uudised/uudiskirjad/";
const ARCHIVE_FRAGMENT = "/uudised/uudiskirjad/otsi/";
const OLD_ARCHIVE = "/nahtavus/uudiskirjad/";

test("typing filters without a navigation and without losing the caret", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);
  await page.goto(ARCHIVE);

  const box = page.getByLabel("Otsi uudiskirja");
  await box.click();

  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes(ARCHIVE_FRAGMENT)),
    box.pressSequentially("foorum", { delay: 60 }),
  ]);

  expect(response.status()).toBe(200);
  expect(response.request().headers()["hx-request"]).toBe("true");

  // The form was never submitted: the results arrived without a page load, so
  // the box still holds the term and still has focus.
  await expect(box).toHaveValue("foorum");
  await expect(box).toBeFocused();
  expect(errors).toEqual([]);
});

test("the address bar keeps up, so a live result can be reloaded and shared", async ({ page }) => {
  await signIn(page);
  await page.goto(`${ARCHIVE}?uudiskiri=koik`);

  const box = page.getByLabel("Otsi uudiskirja");
  await box.pressSequentially("foorum", { delay: 60 });

  // Pushed by the fragment's HX-Push-Url header. Without it the reader would be
  // looking at a filtered page whose URL describes an unfiltered one.
  await expect(page).toHaveURL(/otsi=foorum/);
  await expect(page).toHaveURL(new RegExp(ARCHIVE.replace(/\//g, "\\/")));

  // And the pushed URL is real: reloading it renders the same search server-side.
  await page.reload();
  await expect(page.getByLabel("Otsi uudiskirja")).toHaveValue("foorum");
});

test("clearing the box returns to the unfiltered page", async ({ page }) => {
  await signIn(page);
  await page.goto(ARCHIVE);

  const box = page.getByLabel("Otsi uudiskirja");
  await box.pressSequentially("foorum", { delay: 60 });
  await expect(page).toHaveURL(/otsi=foorum/);

  await box.fill("");
  // An emptied box is the unfiltered page, and its URL says so rather than
  // carrying `?otsi=`.
  await expect(page).not.toHaveURL(/otsi=/);
});

test("without JavaScript the same box is an ordinary form", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();

  await page.goto("/sisene/");
  await page.getByLabel("PIN-kood").fill(TEST_PIN);
  await page.getByRole("button", { name: "Sisene" }).click();

  await page.goto(ARCHIVE);
  await page.getByLabel("Otsi uudiskirja").fill("foorum");
  await page.getByRole("button", { name: "Otsi" }).click();

  // A real navigation this time, to the page itself rather than the fragment,
  // and the term survives it. Live search is an enhancement; this is the floor.
  await expect(page).toHaveURL(/\/uudised\/uudiskirjad\/\?/);
  await expect(page).toHaveURL(/otsi=foorum/);
  await expect(page.getByLabel("Otsi uudiskirja")).toHaveValue("foorum");

  await context.close();
});

test("the archive's old address still opens it, keeping the question", async ({ page }) => {
  // The archive moved under Uudised with the rest of the newsletter material. A
  // board member who bookmarked it under Nähtavus must land on the same filtered
  // view rather than on a 404 or on fourteen unfiltered years.
  await signIn(page);
  await page.goto(`${OLD_ARCHIVE}?uudiskiri=koik&otsi=foorum`);

  await expect(page).toHaveURL(/\/uudised\/uudiskirjad\//);
  await expect(page).toHaveURL(/otsi=foorum/);
  await expect(page.getByLabel("Otsi uudiskirja")).toHaveValue("foorum");
});
