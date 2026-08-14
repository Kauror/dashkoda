import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "../helpers.js";

/*
 * The five analytical focus views of Sündmused.
 *
 * `e2e/seeded/events.spec.js` drives the register. This file drives everything
 * else, and it exists because two classes of defect on this dashboard are
 * invisible to every value-inspecting test in the Python suite:
 *
 *   - a page that renders every section and loads no chart JavaScript, because a
 *     context key was renamed in one place and not the other. Django resolves the
 *     missing variable to falsy, the rest of the page renders perfectly, and no
 *     assertion about values notices;
 *   - horizontal overflow, which has shipped four times in this repository — each
 *     time from an `sr-only` note or a wide table escaping its container — and is
 *     only ever visible at a width somebody actually looked at.
 *
 * Every figure and name on these pages is synthetic.
 */

const PAGE = "/sundmused/";

/* Assertions that do not depend on the viewport run once. */
const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

/* The five focus views this file covers: the query value, its chip label, and a
   heading only that view shows. The register has its own file. */
const FOCUS = [
  ["ulevaade", "Ülevaade", "Mida korraldame?"],
  ["maht", "Maht ja kalender", "Maht ja kalender"],
  ["formaadid", "Formaadid ja teemad", "Programmi seis"],
  ["huvi", "Huvi", "Mõõdetavus"],
  ["planeerimine", "Planeerimine", "Planeerimisvaru"],
];

/* Where a chart is drawn, and therefore where the bundle has to load. */
const CHART_FOCUS = ["maht", "formaadid", "huvi", "planeerimine"];

/* Navigation only. `signIn` is **not** idempotent — it fills a PIN field that a
   signed-in session no longer has — so a test that visits several focus views
   signs in once and then moves between them with this. */
async function visit(page, focus) {
  await page.goto(`${PAGE}?fookus=${focus}&year=all`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sündmused");
}

async function open_(page, focus) {
  await signIn(page);
  await visit(page, focus);
}

for (const [focus, label, heading] of FOCUS) {
  test(`the ${focus} focus renders its own content`, async ({ page }) => {
    oncePerRun();
    const errors = watchConsole(page);

    await open_(page, focus);

    /* `exact` matters: Playwright matches an accessible name by substring by
       default, and `Planeerimisvaru` is a prefix of `Planeerimisvaru aastate
       lõikes` on the same page — two headings, one locator, and a strict-mode
       error that reads nothing like the naming collision it is. */
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();

    /* Exactly one chip is current, and it is the one the URL asked for. Scoped
       to this nav by its label: the shell's sidebar marks `Sündmused` current
       too, and every layout keeps its own copy in the DOM, so an unscoped
       selector counts those as well. */
    const current = page.locator('nav[aria-label="Sündmuste vaated"] a[aria-current="page"]');
    await expect(current).toHaveCount(1);
    await expect(current).toHaveText(label);
    expect(errors).toEqual([]);
  });
}

test("an unknown focus falls back to the overview rather than erroring", async ({ page }) => {
  oncePerRun();
  await signIn(page);
  const response = await page.goto(`${PAGE}?fookus=ei-ole-olemas`);

  expect(response.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Mida korraldame?", exact: true })).toBeVisible();
});

for (const focus of CHART_FOCUS) {
  test(`the ${focus} focus actually draws its charts`, async ({ page }) => {
    oncePerRun();
    const errors = watchConsole(page);

    await open_(page, focus);

    /* The canvas is filled by the bundle. An empty one is exactly what a page
       that shipped no chart JavaScript looks like, and it is silent. */
    const canvas = page.locator("[data-chart-canvas]").first();
    await expect(canvas).toBeVisible();
    await expect(canvas.locator("canvas, svg").first()).toBeVisible({ timeout: 15000 });
    expect(errors).toEqual([]);
  });
}

test("every chart keeps its data table in the document", async ({ page }) => {
  oncePerRun();
  await open_(page, "maht");

  /* The table is not a fallback that appears when something breaks. It stays for
     every reader, and it is what makes these charts reachable by keyboard and by
     screen reader at all — neither tooltip is. */
  const figures = page.locator("figure[data-chart]");
  const count = await figures.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await expect(figures.nth(index).locator("table[data-chart-table]")).toHaveCount(1);
    await expect(figures.nth(index).locator("[data-chart-canvas]")).toHaveAttribute(
      "aria-label",
      /.+/,
    );
  }
});

test("the period control says it selects by the event's own date", async ({ page }) => {
  oncePerRun();
  await open_(page, "ulevaade");

  // One control that silently meant three different periods at once is the
  // confusion this dashboard is built to avoid, so it says which it means.
  await expect(page.getByText(/oma alguskuupäeva/)).toBeVisible();
});

test("the provenance block is present on every focus and folded", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  for (const [focus] of FOCUS) {
    await visit(page, focus);
    const details = page.locator("details", { hasText: "Andmete kohta" }).first();
    await expect(details).toHaveCount(1);
    // A dashboard should not open with pipeline diagnostics, and should never
    // hide them either.
    await expect(details).not.toHaveAttribute("open", /.*/);
  }
});

test("no focus claims attendance, capacity or satisfaction", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  for (const [focus] of FOCUS) {
    await visit(page, focus);
    const text = await page.locator("main").innerText();
    for (const forbidden of ["Osalejaid", "Kohal käinud", "Täitumus", "Vabu kohti", "Rahulolu"]) {
      expect(text, `${focus} must not say ${forbidden}`).not.toContain(forbidden);
    }
  }
});

/* -- responsive ------------------------------------------------------------ */

for (const [focus] of FOCUS) {
  test(`the ${focus} focus does not scroll sideways`, async ({ page }) => {
    await open_(page, focus);
    await expectNoHorizontalOverflow(page);
  });
}

test("a wide chart table scrolls inside its own container", async ({ page }) => {
  await open_(page, "formaadid");

  /* A wide table legitimately scrolls inside its wrapper. The document not
     scrolling sideways is the invariant, and it is the one that has broken here
     before. */
  const tables = page.locator("figure[data-chart] table");
  const count = await tables.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const contained = await tables.nth(index).evaluate((node) => {
      if (node.getBoundingClientRect().width <= node.parentElement.clientWidth + 1) {
        return true;
      }
      for (let element = node.parentElement; element; element = element.parentElement) {
        const overflowX = getComputedStyle(element).overflowX;
        if (overflowX === "auto" || overflowX === "scroll") {
          return true;
        }
        if (element.tagName === "MAIN") {
          break;
        }
      }
      return false;
    });
    expect(contained).toBe(true);
  }
  await expectNoHorizontalOverflow(page);
});
