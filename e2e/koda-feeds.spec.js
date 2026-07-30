import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

// The three public-feed pages. Without a synchronisation run they render their
// truthful empty states, which is exactly what CI should see: the suite never
// contacts koda.ee.
const PAGES = [
  { path: "/liikmeskond/", heading: "Liikmeskond" },
  { path: "/uudised/", heading: "Uudised" },
  { path: "/sundmused/", heading: "Sündmused" },
];

for (const { path, heading } of PAGES) {
  test(`${path} renders its shell and a truthful empty state`, async ({ page }) => {
    const errors = watchConsole(page);
    await signIn(page);

    await page.goto(path);

    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText("Andmeallikas ei ole veel ühendatud.").first()).toBeVisible();
    expect(errors).toEqual([]);
  });

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

    expect(text).not.toContain("teataja");
    expect(text).not.toContain("uusi liikmeid");
    expect(text).not.toContain("sel aastal");
  }
});
