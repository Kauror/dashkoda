import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "../helpers.js";

/*
 * The Sündmused page once the Excel-backed programme is published.
 *
 * The seed imports a synthetic workbook through the real parser and importer, so
 * this suite drives the same page production will: server-side filters, a
 * paginated history, titles linked only where the workbook supplied a public
 * page, and undated records disclosed rather than dropped.
 *
 * Every name, figure and URL on the page is synthetic. The last test in this file
 * is what keeps that true.
 */

const PAGE = "/sundmused/";

/* `/sundmused/` now opens on Ülevaade and has three focus views. The register
   folded onto Ülevaade on 2026-08-17 and no longer has one of its own — it
   renders whenever the overview does — but every test below is still about
   the register specifically, so the focus is composed here rather than
   repeated in fourteen call sites, exactly as it was pinned to its own focus
   before the merge. */
const REGISTER = "fookus=ulevaade";

function registerUrl(query = "") {
  const rest = query.replace(/^\?/, "");
  return `${PAGE}?${REGISTER}${rest ? `&${rest}` : ""}`;
}

/* `browser.newContext()` does not inherit the config's `use`, so the one test
   that needs its own context has to be told where the app is. */
const BASE_URL = process.env.DASHKODA_E2E_BASE_URL || "http://127.0.0.1:8000";

/* Assertions that do not depend on the viewport run once. */
const VIEWPORT_INDEPENDENT = "desktop";
const oncePerRun = () =>
  test.skip(
    test.info().project.name !== VIEWPORT_INDEPENDENT,
    "viewport-independent; runs once on the desktop project",
  );

const rows = (page) => page.locator("main table tbody tr");

async function open_(page, query = "") {
  await signIn(page);
  await page.goto(registerUrl(query));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sündmused");
}

test("the workbook programme is the page's primary content", async ({ page }) => {
  oncePerRun();
  const errors = watchConsole(page);

  await open_(page);

  await expect(page.getByRole("heading", { name: "Sündmuste programm" })).toBeVisible();

  /* The provenance block that used to sit folded at the foot of this page moved
     to `/haldus/` on 2026-08-15, and the public calendar — the secondary
     connection it named — went with it. Both halves are checked: gone from
     here, and still named there. */
  await expect(page.locator('section[aria-labelledby="section-quality"]')).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("Koda.ee avalik kalender");
  expect(errors).toEqual([]);

  await page.goto("/haldus/");
  // Scoped to Sündmused' own block by id — see events-intelligence.spec.js
  // for why counting every `<details>` in the shared section stopped working.
  const provenance = page.locator("#sundmused-andmeallikad");
  await expect(provenance).toHaveCount(1);
  await provenance.locator("summary").click();
  await expect(page.getByRole("heading", { name: "Koda.ee avalik kalender" })).toBeVisible();
});

test("historical rows are visible across every year", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all");

  // The seeded programme spans four years and runs past one page of 50.
  await expect(page.getByText(/Vastavaid sündmusi:/)).toBeVisible();
  expect(await rows(page).count()).toBe(50);

  const years = await page.locator("#filter-year option").allInnerTexts();
  expect(years.filter((label) => /^\d{4}$/.test(label)).length).toBeGreaterThanOrEqual(3);
});

test("year filtering narrows the table and states the period", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all");

  const years = await page
    .locator("#filter-year option")
    .evaluateAll((nodes) => nodes.map((node) => node.value).filter((value) => /^\d{4}$/.test(value)));
  const oldest = years[years.length - 1];

  await page.goto(registerUrl(`year=${oldest}`));
  await expect(page.getByText(`Valitud periood: ${oldest}`)).toBeVisible();

  const dates = await page.locator("main table tbody tr td:first-child").allInnerTexts();
  expect(dates.length).toBeGreaterThan(0);
  /*
   * The filter option carries a four-digit year; the cell writes the short
   * `j.m.y` date, so its year is two digits. Anchored to the end of the string
   * rather than searched for anywhere in it: `23` is also a plausible day, and
   * `toContain("23")` would pass happily on `23.05.24` — a different year
   * entirely, which is precisely what this test exists to catch.
   */
  const shortYear = new RegExp(`\\.${oldest.slice(-2)}$`);
  for (const cell of dates) {
    expect(cell.trim()).toMatch(shortYear);
  }
});

test("month filtering uses the event's own month", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all&month=05");

  const dates = await page.locator("main table tbody tr td:first-child").allInnerTexts();
  expect(dates.length).toBeGreaterThan(0);
  for (const cell of dates) {
    expect(cell).toMatch(/\.05\./);
  }
});

test("tag filtering keeps only that tag", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all");

  const tag = await page.locator("#filter-tag option:not([value=''])").first().getAttribute("value");
  await page.goto(registerUrl(`year=all&tag=${tag}`));

  /* Read by column heading rather than by position. The table gained Tüüp and
     Viis, so a hard-coded `nth-child` now reads a neighbouring column and
     compares the wrong values — which is what it did, silently, until the set
     had two members in it. */
  const column = await page.evaluate(() => {
    const headings = [...document.querySelectorAll("main table thead th")];
    return headings.findIndex((cell) => cell.textContent.trim() === "Teema") + 1;
  });
  expect(column).toBeGreaterThan(0);
  const labels = await page
    .locator(`main table tbody tr td:nth-child(${column})`)
    .allInnerTexts();
  expect(labels.length).toBeGreaterThan(0);
  expect(new Set(labels.map((label) => label.trim())).size).toBe(1);
});

test("combined filters narrow together", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all&review=required&public_link=unlinked");

  const before = await rows(page).count();
  await page.goto(registerUrl("year=all"));
  const all = await rows(page).count();

  expect(before).toBeLessThan(all);
  expect(before).toBeGreaterThan(0);
});

test("clearing the filters returns to the default period", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all&tag=seminar");

  await page.getByRole("link", { name: "Eemalda filtrid" }).click();

  /* Back to the default period, and still on the register: clearing a filter is
     not a request to be moved to another view. The cleared link carries no
     query string at all now — `Ülevaade` is where the register lives and is
     also the page's own default, so nothing needs writing out. */
  const cleared = new URL(page.url());
  expect(cleared.pathname).toBe(PAGE);
  expect(cleared.search).toBe("");
  await expect(page.getByText(/Valitud periood: \d{4}/)).toBeVisible();
});

test("undated records are disclosed and reachable", async ({ page }) => {
  oncePerRun();
  await open_(page);

  const disclosure = page.getByRole("link", { name: /Kuupäev teadmata: \d+/ });
  await expect(disclosure).toBeVisible();
  await disclosure.click();

  await expect(page.getByText("Valitud periood: Kõik aastad")).toBeVisible();
  const dates = await page.locator("main table tbody tr td:first-child").allInnerTexts();
  expect(dates.length).toBeGreaterThan(0);
  for (const cell of dates) {
    expect(cell.trim()).toBe("Kuupäev teadmata");
  }
});

test("a linked title is a link and an unlinked title is plain text", async ({ page }) => {
  oncePerRun();

  await open_(page, "?year=all&public_link=linked");
  const linkedCells = page.locator("main table tbody tr td:nth-child(2)");
  expect(await linkedCells.count()).toBeGreaterThan(0);
  await expect(linkedCells.first().locator("a")).toHaveAttribute(
    "href",
    /^https:\/\/www\.koda\.ee\/et\/sundmused\//,
  );

  await page.goto(registerUrl("year=all&public_link=unlinked"));
  const unlinkedCells = page.locator("main table tbody tr td:nth-child(2)");
  expect(await unlinkedCells.count()).toBeGreaterThan(0);
  expect(await unlinkedCells.locator("a").count()).toBe(0);
});

test("pagination preserves the active filters", async ({ page }) => {
  oncePerRun();
  // Unlinked rows are numerous enough to need a second page, and the filter is
  // checkable on that page: not one of its titles may be an anchor.
  await open_(page, "?year=all&public_link=unlinked");

  const next = page.getByRole("link", { name: "Järgmine" });
  await expect(next).toBeVisible();
  await next.click();

  await expect(page).toHaveURL(/public_link=unlinked/);
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText("Lehekülg 2 /")).toBeVisible();

  const names = page.locator("main table tbody tr td:nth-child(2)");
  expect(await names.count()).toBeGreaterThan(0);
  expect(await names.locator("a").count()).toBe(0);
});

/* -- layout ---------------------------------------------------------- */

const LAYOUT_VIEWS = ["", "?year=all", "?year=all&status=date_unknown", "?year=all&page=2"];

for (const query of LAYOUT_VIEWS) {
  test(`the programme never scrolls the page sideways (${query || "default"})`, async ({ page }) => {
    await open_(page, query);

    await expectNoHorizontalOverflow(page);
  });
}

test("a very long linked event name does not widen the page", async ({ page }) => {
  /*
   * The shape of a defect that shipped once. `sr-only` is absolutely positioned,
   * and an absolutely positioned box is only contained by an ancestor that is its
   * containing block — so an unpositioned anchor let the hidden
   * "(koda.ee, avaneb uuel vahelehel)" note settle at the full text width and
   * widen the whole page.
   *
   * The row's own width is not the invariant: a wide table legitimately scrolls
   * inside its container. The document not scrolling sideways is.
   */
  await open_(page, "?year=all&public_link=linked");

  /* The long name specifically, not whichever linked row sorts first: the seed
     gained several linked events and the newest of them is a short one. Picking
     `.first()` measured that instead and the assertion stopped describing the
     defect it was written for. */
  const links = page.locator("main table a", { has: page.locator("span.sr-only") });
  await expect(links.first()).toBeVisible();
  const texts = await links.allInnerTexts();
  const longest = texts.reduce((a, b) => (a.length >= b.length ? a : b), "");
  expect(longest.length).toBeGreaterThan(150);

  await expectNoHorizontalOverflow(page);
});

test("the wide programme table scrolls inside its own container", async ({ page }) => {
  await open_(page, "?year=all");

  const table = page.locator("main table").first();
  const contained = await table.evaluate((node) => {
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
  await expectNoHorizontalOverflow(page);
});

test("the programme never scrolls sideways at 200% zoom", async ({ page }) => {
  test.skip(page.viewportSize().width < 1024, "measured from the desktop viewport");

  await open_(page, "?year=all");
  // Browser zoom halves the CSS-pixel viewport, so emulate it by halving the
  // viewport rather than by setting CSS zoom.
  const { width, height } = page.viewportSize();
  await page.setViewportSize({ width: Math.round(width / 2), height: Math.round(height / 2) });

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the filter controls fit inside the narrow layout", async ({ page }) => {
  test.skip(page.viewportSize().width >= 640, "narrow layouts only");

  await open_(page);

  // Every control stays inside the viewport, so the filter grid has collapsed to
  // one column rather than pushing the page sideways.
  const escaping = await page
    .locator("main select, main input")
    .evaluateAll((nodes) =>
      nodes.filter((node) => node.getBoundingClientRect().right > document.documentElement.clientWidth + 1).length,
    );

  expect(escaping).toBe(0);
  await expectNoHorizontalOverflow(page);
});

test("filtering works with JavaScript disabled", async ({ browser }) => {
  oncePerRun();
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL: BASE_URL });
  const page = await context.newPage();
  try {
    await page.goto("/sisene/");
    await page.getByLabel("PIN-kood").fill(process.env.DASHKODA_E2E_PIN || "4071");
    await page.getByRole("button", { name: "Sisene" }).click();
    await page.goto(registerUrl("year=all"));

    // `Avalik leht` is one of the six filters behind the `Täpsem valik`
    // disclosure. `<details>` is native HTML and needs no script to open, which
    // is why those filters could be folded away without costing a reader with
    // JavaScript unavailable anything — so opening it here is part of what this
    // test asserts rather than a workaround for it.
    await page.locator("summary", { hasText: "Täpsem valik" }).click();
    await page.locator("#filter-link").selectOption("linked");
    await page.getByRole("button", { name: "Filtreeri" }).click();

    await expect(page).toHaveURL(/public_link=linked/);
    expect(await rows(page).count()).toBeGreaterThan(0);
  } finally {
    await context.close();
  }
});

/* -- the compacted header ------------------------------------------- */

test("the advanced filters stay folded until one of them is applied", async ({ page }) => {
  oncePerRun();

  await open_(page, "?year=all");
  const disclosure = page.locator("details", { hasText: "Täpsem valik" });
  await expect(disclosure).not.toHaveAttribute("open", /.*/);

  // Applied from the URL rather than by clicking, because what matters is the
  // server deciding to render it open: a folded control hiding a filter that is
  // narrowing the table is how a reader ends up mistrusting the row count.
  //
  // `page.goto` rather than a second `open_`: that helper signs in first, and
  // signing in twice in one test lands on a dashboard with no PIN field to fill.
  await page.goto(registerUrl("year=all&public_link=linked"));
  await expect(page.locator("details", { hasText: "Täpsem valik" })).toHaveAttribute("open", /.*/);
  await expect(page.getByText("1 aktiivne")).toBeVisible();
});

test("a search does not unfold the advanced filters", async ({ page }) => {
  oncePerRun();

  // The search box is not behind the disclosure, so typing a name is not a
  // question about quarters — and opening it on every keystroke would undo the
  // compaction exactly when the reader is busiest.
  await open_(page, "?year=all&q=a");

  await expect(page.locator("details", { hasText: "Täpsem valik" })).not.toHaveAttribute(
    "open",
    /.*/,
  );
});

test("the headline strip leaves no empty cell", async ({ page }) => {
  oncePerRun();

  // The strip moved to Ülevaade, which is the page that answers questions; the
  // register lists rows and states its own count under the filters.
  //
  // It paints `bg-border` behind its cells, so a column count that does not
  // divide the card count renders the remainder as a grey block the width of a
  // card — which is what the default four columns did when there were three
  // figures. The strip is three cards or four depending on whether the planning
  // data supports the fourth, so both counts have to divide.
  await signIn(page);
  await page.goto(`${PAGE}?fookus=ulevaade&year=all`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sündmused");

  const strip = await page.evaluate(() => {
    const node = document.querySelector(".dk-kpi-strip");
    return {
      cards: node.children.length,
      columns: getComputedStyle(node).gridTemplateColumns.split(" ").length,
    };
  });

  expect(
    strip.cards % strip.columns,
    `${strip.cards} figures in a ${strip.columns}-column grid leaves an empty cell`,
  ).toBe(0);
});

/* -- nothing real ---------------------------------------------------- */

test("the programme shows nothing that looks like real Chamber data", async ({ page }) => {
  oncePerRun();
  await open_(page, "?year=all");

  const text = await page.locator("main").innerText();
  for (const forbidden of ["Uusi liikmeid sel aastal", "orgusaar", "€", "Liikmehind"]) {
    expect(text).not.toContain(forbidden);
  }
  // Every event name is unmistakably synthetic.
  const names = await page.locator("main table tbody tr td:nth-child(2)").allInnerTexts();
  expect(names.length).toBeGreaterThan(0);
  for (const name of names) {
    expect(name.toLowerCase()).toContain("sünteetiline");
  }
  // And every public link is a synthetic path on the allowed host.
  const hrefs = await page
    .locator("main table a")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
  for (const href of hrefs) {
    expect(href).toContain("sunteetiline");
  }
});
