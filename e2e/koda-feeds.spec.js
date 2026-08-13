import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

// The public-feed pages. Without a synchronisation run they render their
// truthful empty states, which is exactly what CI should see: the suite never
// contacts koda.ee.
const PUBLIC_FEED_PAGES = [
  { path: "/uudised/", heading: "Uudised" },
  { path: "/sundmused/", heading: "Sündmused" },
];

// /liikmeskond/ used to belong to the list above and no longer does. It is the
// board report now, fed by a one-off history import and manual entry rather
// than by collection from koda.ee, so "the data source is not yet connected"
// would be the wrong thing for it to say and it asserts its own empty state
// below. Its layout and heading rules are still shared.
const BOARD_REPORT_PAGE = { path: "/liikmeskond/", heading: "Liikmeskond" };

const PAGES = [...PUBLIC_FEED_PAGES, BOARD_REPORT_PAGE];

for (const { path, heading } of PUBLIC_FEED_PAGES) {
  test(`${path} renders its shell and a truthful empty state`, async ({ page }) => {
    const errors = watchConsole(page);
    await signIn(page);

    await page.goto(path);

    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText("Andmeallikas ei ole veel ühendatud.").first()).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test(`${BOARD_REPORT_PAGE.path} renders its shell and a truthful empty state`, async ({ page }) => {
  const errors = watchConsole(page);
  await signIn(page);

  await page.goto(BOARD_REPORT_PAGE.path);

  await expect(
    page.getByRole("heading", { level: 1, name: BOARD_REPORT_PAGE.heading }),
  ).toBeVisible();
  // Not a feed that failed to connect: nobody has imported a board report yet.
  await expect(
    page.getByText("Sisemist liikmeskonna aruannet ei ole veel imporditud.").first(),
  ).toBeVisible();
  await expect(page.getByText("Andmeallikas ei ole veel ühendatud.")).toHaveCount(0);
  expect(errors).toEqual([]);
});

for (const { path } of PAGES) {
  test(`${path} does not scroll horizontally`, async ({ page }) => {
    await signIn(page);
    await page.goto(path);

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );

    expect(overflows).toBe(false);
  });

  test(`${path} starts its headings at a single h1`, async ({ page }) => {
    await signIn(page);
    await page.goto(path);

    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  });
}

test("no page advertises a year-to-date member figure or Teataja", async ({ page }) => {
  await signIn(page);

  for (const { path } of [{ path: "/" }, ...PAGES]) {
    await page.goto(path);
    const text = (await page.locator("body").innerText()).toLowerCase();

    // `e-Teataja` is one of the Chamber's own newsletters rather than Riigi
    // Teataja, and it is on /uudised/ since the newsletter material moved
    // there. It is removed rather than the news page being dropped from this
    // loop, which would take the guard off everything else that page renders.
    expect(text.replaceAll("e-teataja", "")).not.toContain("teataja");
    expect(text).not.toContain("uusi liikmeid");
    expect(text).not.toContain("sel aastal");
  }
});
