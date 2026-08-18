import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

/*
 * The Uudised intelligence dashboard.
 *
 * There were five sections behind separate addresses at one point. The
 * newsletters left for `Otsepostitused`, at their own address under Koduleht —
 * `otsepostitused.spec.js` holds that half, including that none of it is still
 * here. `Avaldamine` folded into the overview on 2026-08-16. `Uudiste mõju` and
 * `Arhiiv` were the last two focuses standing after that, and they folded into
 * the same one view as the overview on 2026-08-17: the dashboard, the one chart
 * `Uudiste mõju` still drew, and the archive table, top to bottom on one screen.
 * Every retired key still resolves to this one view rather than raising.
 *
 * A second merge followed on 2026-08-18: the page carried two time controls,
 * a publication window (`periood=`) and a measurement window (`loetud=`), until
 * this round's mockup showed one picker governing everything. `apps/news/page.py`
 * states why that interface merge is safe where merging the two questions'
 * arithmetic would not have been.
 * `loetud=` and `vaade=` (the retired lens control) are no longer read at all.
 *
 * The page used to be one scroll — an archive table with the newsletter
 * material beneath it — before it grew a focus, and it is one scroll again now
 * that the focus is gone: what these assert is that every section is on the
 * page in the right order, that no tab navigation is offered where there is
 * nothing left to choose between, and that a stale link to any retired focus
 * or retired window parameter still renders instead of erroring.
 *
 * CI runs against a container with an empty database, so what these assert is
 * the layout, the controls and the *truthful empty state*.
 */

const OVERVIEW = "/uudised/";
/** Retired focus keys, kept because a saved link must keep rendering — each
 * resolves to `OVERVIEW`'s one view now rather than to a view of its own. */
const RETIRED_IMPACT = "/uudised/?fookus=moju";
const RETIRED_PUBLISHING = "/uudised/?fookus=avaldamine";
const RETIRED_ARCHIVE = "/uudised/?fookus=arhiiv";

/** Sign in, then open Uudised. */
async function openNews(page, url = OVERVIEW) {
  await signIn(page);
  await page.goto(url);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
}

test("the page offers no focus navigation", async ({ page }) => {
  const errors = watchConsole(page);

  await openNews(page);

  // A nav with one, unclickable, already-active chip in it reads as a fault —
  // the same rule Liikmeskond's focus navigation follows once it is down to
  // one option.
  await expect(page.getByRole("navigation", { name: "Vaade" })).toHaveCount(0);
  // Scoped to `main`: the shell's own sidebar carries a top-level "Ülevaade"
  // link to the whole dashboard's home, which is not this page's retired
  // focus tab and would otherwise make this assertion fail for the wrong
  // reason.
  const main = page.locator("main");
  for (const label of [
    "Ülevaade",
    "Uudiste mõju",
    "Arhiiv",
    "Uudiskirjad",
    "Avaldamine",
  ]) {
    await expect(
      main.getByRole("link", { name: label, exact: true }),
    ).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test("every retired focus key still renders the one view", async ({ page }) => {
  await signIn(page);
  for (const url of [
    RETIRED_IMPACT,
    RETIRED_PUBLISHING,
    RETIRED_ARCHIVE,
    "/uudised/?fookus=zzz",
    "/uudised/?loetud=90",
    "/uudised/?vaade=kuu",
  ]) {
    const response = await page.goto(url);
    expect(response.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Uudised");
  }
});

test("the retired newsletter focus leaves for Otsepostitused", async ({
  page,
}) => {
  // A real bookmark. Falling through to the overview would render happily and
  // tell the reader nothing about where what they asked for went.
  await signIn(page);
  await page.goto("/uudised/?fookus=uudiskirjad");

  await expect(page).toHaveURL(/\/otsepostitused\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Otsepostitused",
  );
});

test("the dashboard leads, the archive follows", async ({ page }) => {
  // The regression this redesign is for. The archive answers "find me the
  // article about excise duty" and the dashboard answers "how are we doing";
  // both are on the page now, but not in the same order they'd matter in.
  //
  // CI runs against an empty database, so `Põhinäitajad` itself never
  // renders — the dashboard section's own truthful empty state is what's on
  // screen instead, and it is still the first thing on the page, which is
  // the property this test exists to check.
  //
  // The archive's own zero-results state (`archive.empty_message`, in
  // `_news_results.html`) says the same true thing about the same
  // unconnected source, so the same sentence appears twice on this page —
  // `.first()` is the dashboard's copy, since it renders before the archive
  // does.
  await openNews(page);

  const dashboard = page
    .getByText("Andmeallikas ei ole veel ühendatud.")
    .first();
  const archiveSearch = page.getByLabel("Otsi uudist");
  await expect(dashboard).toBeVisible();
  await expect(archiveSearch).toBeVisible();

  const dashboardTop = (await dashboard.boundingBox()).y;
  const archiveTop = (await archiveSearch.boundingBox()).y;
  expect(dashboardTop).toBeLessThan(archiveTop);
});

test("the news page renders no newsletter material at all", async ({
  page,
}) => {
  // The move out, asserted the way the move off Nähtavus was: not the section
  // heading, not the card, not the subject search.
  await openNews(page);

  await expect(
    page.getByRole("heading", { name: "Uudiskirjad", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Uudiskirjade tulemused", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Otsi uudiskirja")).toHaveCount(0);
});

test("the archive keeps every control it had", async ({ page }) => {
  // `Avaldamisperiood` moved to the page header on 2026-08-18, since it now
  // governs every section on the page rather than only the archive — see
  // `apps/news/page.py`. It is `Periood` there now, and it is one control
  // rather than the three the page used to carry.
  await openNews(page);

  await expect(page.getByRole("navigation", { name: "Periood" })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Uudise liik" }),
  ).toBeVisible();
  await expect(page.getByLabel("Otsi uudist")).toBeVisible();
});

test("the news search still works", async ({ page }) => {
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

test("a search keeps whatever state was already in the URL and adds no focus marker", async ({
  page,
}) => {
  // The pushed URL used to assert `fookus=arhiiv` on every search, to keep the
  // reader on the archive focus. There is no other focus to fall back to now,
  // so a search just keeps what was already there — here, the reading period —
  // and does not invent a `fookus` that was never asked for.
  await signIn(page);
  await page.goto("/uudised/?periood=1a");

  const box = page.getByLabel("Otsi uudist");
  await box.click();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/uudised/otsi/")),
    box.pressSequentially("eeln", { delay: 60 }),
  ]);

  await expect(page).toHaveURL(/periood=1a/);
  await expect(page).not.toHaveURL(/fookus=/);
});

test("the page never scrolls sideways", async ({ page }) => {
  await openNews(page);
  await expectNoHorizontalOverflow(page);
});
