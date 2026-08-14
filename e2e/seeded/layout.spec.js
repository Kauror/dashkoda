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
   * untruncated anchor let the hidden "(koda.ee, avaneb uuel vahelehel)" note
   * settle at the full text width and widen the whole page.
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

test("a hidden note inside a scrolling table cannot escape it", async ({ page }) => {
  /*
   * The invariant behind the 324-pixel overflow the seeded analytics exposed on
   * the website page, asserted directly rather than only through its symptom.
   *
   * `sr-only` is absolutely positioned, and an absolutely positioned box is
   * clipped by an ancestor's `overflow` only when that ancestor is in its
   * containing-block chain. The scrolling wrappers around these tables are
   * `static`, so a note inside an unpositioned anchor is contained by nothing
   * and settles wherever the untruncated text ends — pushing the document out
   * with it, while the table itself scrolls perfectly correctly inside its
   * wrapper and looks innocent.
   *
   * The overflow test only catches this where the content happens to be long
   * enough; this catches it wherever the shape exists.
   */
  await signIn(page);

  for (const page_ of PAGES) {
    await page.goto(page_.path);
    const escapees = await page.evaluate(() => {
      const found = [];
      for (const note of document.querySelectorAll("span.sr-only")) {
        if (getComputedStyle(note).position !== "absolute") {
          continue;
        }
        // The containing block is the nearest positioned ancestor. If a
        // scrolling ancestor is reached before one, nothing clips the note.
        for (let parent = note.parentElement; parent; parent = parent.parentElement) {
          const style = getComputedStyle(parent);
          if (style.position !== "static") {
            break;
          }
          if (style.overflowX !== "visible") {
            found.push(`${parent.tagName.toLowerCase()} > … > ${note.textContent.trim()}`);
            break;
          }
        }
      }
      return found;
    });

    expect(escapees, `${page_.path} lets a hidden note escape its scroller`).toEqual([]);
  }
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

/*
 * The Õigusloome card is a preview of at most seven records per tab, and its
 * two tabs almost never hold seven each. Whichever tab is shorter must not pull
 * the card's footer — and the whole right-hand column below it — up the page.
 *
 * Both panels reserve the height of a seven-row list in CSS. These tests
 * measure the rendered card rather than the declaration, because a reserve
 * derived from theme variables silently stops applying if one of them goes
 * away, and the symptom is exactly the collapse this prevents.
 */
const LEGAL_CARD = 'section[aria-labelledby="section-legislation"]';
// Sub-pixel layout rounding is not a collapse. One shorter row would be tens of
// pixels, so this tolerance cannot hide the defect.
const HEIGHT_TOLERANCE = 2;

async function cardHeight(page) {
  return (await page.locator(LEGAL_CARD).boundingBox()).height;
}

/*
 * How far the card's "Seisuga:" row sits below the card's own top — what a
 * collapsing card drags upwards.
 *
 * Measured **relative to the card**, not to the viewport. `boundingBox().y` is
 * a viewport coordinate, and Playwright scrolls an element into view before
 * clicking it: on a narrow phone, clicking the second tab scrolls the page, and
 * the footer's viewport `y` then differs by the scroll distance even though the
 * card never moved. That read as a 436 px collapse on `phone-narrow` while the
 * card's own height, measured on the line above, was unchanged.
 */
async function freshnessOffset(page) {
  const card = await page.locator(LEGAL_CARD).boundingBox();
  const footer = await page.locator(`${LEGAL_CARD} dl`).last().boundingBox();
  return footer.y - card.y;
}

test("the legal card keeps its height when the shorter tab is selected", async ({ page }) => {
  await signIn(page);
  await page.goto("/");

  const openRows = page.locator("#panel-open li");
  const sentRows = page.locator("#panel-sent li");
  await expect(openRows).toHaveCount(7);

  /*
   * This used to require the sent tab to be shorter than the open one, because
   * the legal-work seed carried six sent records in total.
   *
   * That seed now publishes a multi-year opinion history, so the latest-sent
   * preview is always full at the limit and no shorter tab exists to select.
   * The height assertions below are what this test is for and they are
   * unchanged; what is gone is the guarantee that they are exercised against a
   * genuinely shorter panel. If a shorter-tab fixture is wanted back, it needs
   * a preview whose source is not the seeded legal register.
   */
  const sent = await sentRows.count();
  expect(sent).toBeGreaterThan(0);

  const before = await cardHeight(page);
  const footerBefore = await freshnessOffset(page);

  await page.getByRole("tab", { name: "Välja läinud" }).click();
  await expect(sentRows.first()).toBeVisible();

  expect(Math.abs((await cardHeight(page)) - before)).toBeLessThanOrEqual(HEIGHT_TOLERANCE);
  expect(Math.abs((await freshnessOffset(page)) - footerBefore)).toBeLessThanOrEqual(
    HEIGHT_TOLERANCE,
  );
});

test("the legal card keeps its height on the way back to the fuller tab", async ({ page }) => {
  await signIn(page);
  await page.goto("/");

  await page.getByRole("tab", { name: "Välja läinud" }).click();
  await expect(page.locator("#panel-sent li").first()).toBeVisible();
  const shortTab = await cardHeight(page);

  await page.getByRole("tab", { name: "Töös" }).click();
  await expect(page.locator("#panel-open li").first()).toBeVisible();

  expect(Math.abs((await cardHeight(page)) - shortTab)).toBeLessThanOrEqual(HEIGHT_TOLERANCE);
});

test("the reserved list height is seven real rows, not a guessed number", async ({ page }) => {
  /*
   * What makes the reserve survive a tab holding three records, or none: it is
   * the height of seven of this card's own rows. Measuring it against the rows
   * the browser actually drew is what keeps the CSS honest if the row is ever
   * restyled.
   */
  await signIn(page);
  await page.goto("/");

  const reserved = await page.locator("#panel-open").evaluate((node) => {
    const declared = getComputedStyle(node).minHeight;
    return declared.endsWith("px") ? parseFloat(declared) : NaN;
  });
  expect(Number.isNaN(reserved)).toBe(false);

  const sevenRows = await page.locator("#panel-open li").evaluateAll((nodes) => {
    const rows = nodes.slice(0, 7);
    const first = rows[0].getBoundingClientRect();
    const last = rows[rows.length - 1].getBoundingClientRect();
    return last.bottom - first.top;
  });

  expect(Math.abs(reserved - sevenRows)).toBeLessThanOrEqual(HEIGHT_TOLERANCE);
  // Both tabs reserve it, or switching still jumps.
  const sentReserved = await page
    .locator("#panel-sent")
    .evaluate((node) => parseFloat(getComputedStyle(node).minHeight));
  expect(Math.abs(sentReserved - reserved)).toBeLessThanOrEqual(HEIGHT_TOLERANCE);

  await expectNoHorizontalOverflow(page);
});
