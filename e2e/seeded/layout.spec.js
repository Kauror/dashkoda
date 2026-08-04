import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn } from "../helpers.js";
import { PAGES } from "./pages.js";

/*
 * Layout against real content.
 *
 * CI's database was always empty, so nothing was ever long enough to truncate
 * and a genuine 152-pixel horizontal overflow shipped while every viewport
 * assertion passed. These tests run after `manage.py seed_e2e_data` has
 * published deliberately long synthetic titles, so the same class of defect
 * fails here instead of in production.
 */

for (const page_ of PAGES) {
  test(`${page_.name} never scrolls sideways with content`, async ({ page }) => {
    await signIn(page);
    await page.goto(page_.path);

    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("the overview never scrolls sideways at 200% zoom with content", async ({ page }) => {
  test.skip(page.viewportSize().width < 1024, "measured from the desktop viewport");

  await signIn(page);
  // Browser zoom halves the CSS-pixel viewport, so emulate it by halving the
  // viewport rather than by setting CSS zoom, which does not scale the layout
  // viewport and makes overflow measurements meaningless.
  const { width, height } = page.viewportSize();
  await page.setViewportSize({ width: Math.round(width / 2), height: Math.round(height / 2) });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("a long linked title with a hidden suffix does not widen the page", async ({ page }) => {
  /*
   * The exact shape of the shipped defect. `sr-only` is absolutely positioned,
   * and an absolutely positioned box is only clipped by an ancestor's
   * `overflow: hidden` when that ancestor is its containing block — so an
   * untruncated anchor let the hidden "(avaneb koda.ee lehel)" note settle at
   * the full text width and widen the whole page.
   */
  await signIn(page);
  await page.goto("/uudised/");

  const links = page.locator("main a", { has: page.locator("span.sr-only") });
  await expect(links.first()).toBeVisible();

  const widest = await links.evaluateAll((nodes) =>
    Math.max(...nodes.map((node) => node.getBoundingClientRect().right)),
  );
  const limit = await page.evaluate(() => document.documentElement.clientWidth);

  expect(widest).toBeLessThanOrEqual(limit);
  await expectNoHorizontalOverflow(page);
});

test("long text is clipped or wrapped rather than allowed to run off", async ({ page }) => {
  await signIn(page);
  await page.goto("/oigusloome/");

  // The seeded legal-work topic is far longer than any card, so if it is
  // neither truncated nor wrapped it must overflow its own container.
  const overflowing = await page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll("main table td, main li, main p"));
    return nodes.filter((node) => node.scrollWidth - node.clientWidth > 1).length;
  });

  expect(overflowing).toBe(0);
});

test("wide tables scroll inside their own container, not the page", async ({ page }) => {
  await signIn(page);
  await page.goto("/oigusloome/");

  const tables = page.locator("main table");
  expect(await tables.count()).toBeGreaterThan(0);

  /*
   * A table wider than the viewport is not a defect — that is what the
   * scrolling wrapper is for, and asserting the table fits would be asserting
   * an implementation accident. The invariant is narrower: a table that
   * overflows must sit inside an ancestor that actually opted into scrolling,
   * so the overflow is contained rather than pushed onto the document.
   */
  const unscrollable = await tables.evaluateAll((nodes) =>
    nodes
      .filter((node) => node.getBoundingClientRect().width > node.parentElement.clientWidth + 1)
      .filter((node) => {
        for (let element = node.parentElement; element; element = element.parentElement) {
          const overflowX = getComputedStyle(element).overflowX;
          if (overflowX === "auto" || overflowX === "scroll") {
            return false;
          }
          if (element.tagName === "MAIN") {
            break;
          }
        }
        return true;
      }).length,
  );

  expect(unscrollable).toBe(0);
  // And whatever the tables do, the document itself must not scroll sideways.
  await expectNoHorizontalOverflow(page);
});
