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

/* The three analytical focus views this file covers: the query value, its chip
   label, and a heading only that view shows. The register has its own file.

   `Huvi` and `Planeerimine` were the other two and came off on 2026-08-15.
   `Maht ja kalender` no longer names itself in a heading — its summary strip
   went too — so the volume view is identified by the section that stayed. */
const FOCUS = [
  ["ulevaade", "Ülevaade", "Mida korraldame?"],
  ["maht", "Maht ja kalender", "Kvartalid"],
  ["formaadid", "Formaadid ja teemad", "Programmi seis"],
];

/* Where a chart is drawn, and therefore where the bundle has to load. Ülevaade
   is among them since `Hinnastruktuur` moved onto it. */
const CHART_FOCUS = ["ulevaade", "maht", "formaadid"];

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

test("every chart names itself for a reader who cannot see the canvas", async ({
  page,
}) => {
  oncePerRun();
  await open_(page, "maht");

  /* The accessible data table left every chart on 2026-08-17. `chart.summary`,
     the canvas's own `aria-label`, is what is left to reach these charts by
     keyboard or by screen reader — neither tooltip does either. */
  const figures = page.locator("figure[data-chart]");
  const count = await figures.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await expect(figures.nth(index).locator("[data-chart-canvas]")).toHaveAttribute(
      "aria-label",
      /.+/,
    );
  }
});

/* The period control used to carry a paragraph saying it selects by the event's
   own start date, and a spec asserting it. The board struck the paragraph on
   2026-08-15, so there is nothing left on the page for that spec to read and it
   was removed rather than pointed somewhere it would pass without meaning it.

   The selection itself is unchanged — `?year=` is still an event cohort, still
   resolved from the start date, and `test_intelligence_page.py` pins that. What
   is gone is the sentence explaining it to a reader. */

test("the provenance block is on no focus, and is on /haldus/", async ({ page }) => {
  oncePerRun();
  await signIn(page);

  /* It was folded away at the foot of every focus until 2026-08-15, when the
     board moved it to Admin. Both halves are checked here: gone from each view,
     and actually rendered where it went — deleting it from one page and never
     wiring it into the other would satisfy the first half alone. */
  for (const [focus] of FOCUS) {
    await visit(page, focus);
    /* Scoped to the provenance section. Every chart figure carries a `details`
       of its own — the accessible data table — so counting them all here would
       be counting the wrong thing on exactly the views that draw. */
    await expect(page.locator('section[aria-labelledby="section-quality"]')).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText("Andmete kohta");
  }

  await page.goto("/haldus/");
  // Scoped to Sündmused' own block by id: `Andmeallikad ja import` holds more
  // than one domain's `<details>` since Õigusloome joined it on 2026-08-17,
  // so counting every `<details>` in the shared section counts the wrong thing.
  const details = page.locator("#sundmused-andmeallikad");
  await expect(details).toHaveCount(1);
  // Still folded: Admin is where the diagnostics live, not where they shout.
  await expect(details).not.toHaveAttribute("open", /.*/);

  await details.locator("summary").click();
  await expect(
    page.getByRole("heading", { name: "Mida need andmed ei tõesta", exact: true }),
  ).toBeVisible();
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
