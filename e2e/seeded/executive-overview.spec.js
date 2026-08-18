import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "../helpers.js";

/**
 * `Koja töölaud` against seeded data.
 *
 * The empty-database suite in `e2e/shell.spec.js` proves the page survives
 * having no sources. This one proves it says something once it has them, which
 * is the half a green empty suite has never been able to show: every assertion
 * below is invisible until real figures, a real signal and real dated work
 * exist.
 */

/** The six domain cards, in the order the page reads them. */
const CARDS = [
  "Liikmeskond",
  "Õigusloome",
  "Sündmused",
  "Koduleht ja uudised",
  "Otsepostitused",
  "E-pood",
];

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("every domain dashboard has a card, and none is an empty state", async ({ page }) => {
  const errors = watchConsole(page);

  const status = page.getByLabel("Põhinäitajad");

  // Level 2: the cards are the page's own second level since the
  // `Põhinäitajad` heading came off, and an `h3` under the `h1` would skip one.
  for (const card of CARDS) {
    await expect(status.getByRole("heading", { name: card, level: 2 })).toBeVisible();
  }
  // The retired strategic labels must not come back with the cards. Each of
  // these named a group of domains rather than a dashboard, and a reader
  // following one would look for a page that does not exist.
  for (const gone of ["Digiteenused", "Huvikaitse", "Kaasamine", "Nähtavus", "Koja seis"]) {
    await expect(page.getByText(gone, { exact: true })).toHaveCount(0);
  }

  // Not a single card may be showing the unconnected state.
  await expect(page.getByText("Andmeallikas ei ole ühendatud.")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the six cards use the width rather than stacking down the page", async ({ page }) => {
  /*
   * The defect the tall pillar card produced and the reason it was replaced: at
   * a desktop width six cards in one column is a page nobody can see the end
   * of. Three columns from `xl`, two from `sm`, one on a phone — measured on
   * the rendered grid rather than asserted from the class list, because a class
   * that stops applying leaves no trace anywhere else.
   *
   * Three columns begin at 1280 rather than at 1024 because the persistent
   * sidebar arrives at 1024 and takes 17rem out of this grid.
   */
  const width = page.viewportSize().width;
  const cards = page.getByLabel("Põhinäitajad").locator("article");
  await expect(cards).toHaveCount(6);

  const lefts = await cards.evaluateAll((nodes) =>
    nodes.map((node) => Math.round(node.getBoundingClientRect().left)),
  );
  const columns = new Set(lefts).size;

  if (width >= 1280) {
    expect(columns).toBe(3);
  } else if (width >= 640) {
    expect(columns).toBe(2);
  } else {
    expect(columns).toBe(1);
  }
});

test("the cards come before the exceptions", async ({ page }) => {
  /*
   * `Tähelepanu` opened the page for a day and a half and was the wrong thing
   * to meet first: a section of exceptions means nothing until the ordinary
   * state is on screen, and on a quiet week it is not rendered at all — a page
   * that opens with an empty section reads as a broken one.
   */
  const cards = await page.getByLabel("Põhinäitajad").boundingBox();
  const attention = await page.getByRole("region", { name: "Tähelepanu" }).boundingBox();

  expect(cards.y).toBeLessThan(attention.y);
});

test("the attention section shows what a domain flagged, worst first", async ({ page }) => {
  /*
   * The page's one genuinely cross-domain capability. It left the overview on
   * 2026-08-16 and came back on 2026-08-17, and this is the contract it came
   * back with: a signal is shown with its urgency as a **word**, so the
   * priority survives greyscale and a reader who cannot separate the two
   * warning tones.
   *
   * The section renders only when something was flagged. The seed produces at
   * least one — a stale news feed and a synthetic programme with unlinked
   * events — so its absence here would be a defect rather than a quiet day.
   */
  const attention = page.getByRole("region", { name: "Tähelepanu" });
  await expect(attention).toBeVisible();

  const rows = attention.locator("li");
  const count = await rows.count();
  expect(count).toBeGreaterThan(0);
  expect(count).toBeLessThanOrEqual(5);

  const words = await attention
    .locator(".dk-badge")
    .evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));
  // `Positiivne` is the one badge that is not an urgency: the domain said the
  // movement is the direction it wants. It sorts with the notable ones, which
  // is what it is.
  const order = ["Kiireloomuline", "Tähelepanu", "Tähelepanuväärne", "Positiivne"];
  for (const word of words) {
    expect(order).toContain(word);
  }
  const rank = { Kiireloomuline: 0, Tähelepanu: 1, Tähelepanuväärne: 2, Positiivne: 2 };
  const positions = words.map((word) => rank[word]);
  expect([...positions].sort()).toEqual(positions);
});

test("the membership card leads with the public directory count", async ({ page }) => {
  /*
   * The card prints one total and no captions. What holds the "never two
   * unlabelled totals" rule is that the directory count is the only member
   * total on the card at all — the report contributes ratios and a
   * joined/removed pair, and `Andmete seis` at `/haldus/` names each source's
   * own date.
   *
   * The card's own two-date line went on 2026-08-18 with the period lines of
   * the three cards whose figures are a current state rather than a window.
   */
  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "Liikmeskond" })
    .first();

  await expect(card.getByText("liiget")).toBeVisible();
  await expect(card.getByText("Tasunud liikmete osakaal")).toBeVisible();
  // The struck chrome must stay gone.
  await expect(card.getByText("Liikmeid kokku")).toHaveCount(0);
  await expect(card.getByText("Koda.ee liikmekataloog")).toHaveCount(0);
});

test("the legal card leads with open matters, not with opinions sent", async ({ page }) => {
  /*
   * `Arvamusi välja saadetud tänavu` led this card until 2026-08-17 and was the
   * wrong headline for a management page: cumulative, only ever rising, and
   * silent about what the Chamber is holding now. Both figures are on the card;
   * this pins which one is the headline.
   */
  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "Õigusloome" })
    .first();

  await expect(card.getByText("teemat töös")).toBeVisible();
  await expect(card.getByText("Arvamusi saadetud tänavu")).toBeVisible();
  // Opinion volume is output, never impact.
  await expect(card.getByText(/mõju/i)).toHaveCount(0);
});

test("the events card leads with the near-term horizon and claims no attendance", async ({
  page,
}) => {
  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "Sündmused" })
    .first();

  await expect(card.getByText("järgmise 30 päeva jooksul")).toBeVisible();
  await expect(card.getByText("Sündmusi tänavu")).toBeVisible();
  // DashKoda holds no attendance figure at all, so no wording may imply one.
  for (const forbidden of [/osalej/i, /kohalolij/i, /registreerimis/i]) {
    await expect(card.getByText(forbidden)).toHaveCount(0);
  }
});

test("the website card spells sessions and page views differently", async ({ page }) => {
  /*
   * GA4 sessions are `külastused` and GA4 page views are `vaatamised`. The
   * commonest way to overstate a website is to spell the larger measure with
   * the smaller one's word.
   */
  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "Koduleht ja uudised" })
    .first();

  await expect(card.getByText(/külastust · \d+ p/)).toBeVisible();
  await expect(card.getByText("Uudiste vaatamised")).toBeVisible();
  // Both sides of the share are page views over the same days. Spelling the
  // denominator as visits would claim a ratio between two different measures.
  await expect(card.getByText("Uudiste osa vaatamistest")).toBeVisible();
  // The newsletter rate moved to its own card on 2026-08-17.
  await expect(card.getByText(/e-Teataja/)).toHaveCount(0);
});

test("the mailings card carries rates and never an audience", async ({ page }) => {
  /*
   * Three lists whose overlap nobody has measured. The card states weighted
   * open and click rates; the list sizes are `Auditooriumid`'s job, one per
   * list, and no number anywhere is a sum across them.
   */
  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "Otsepostitused" })
    .first();

  await expect(card.getByText("e-Teataja avamismäär")).toBeVisible();
  await expect(card.getByText("e-Teataja klikimäär")).toBeVisible();
  // How much went out, which is the one thing the rates cannot say.
  await expect(card.getByText(/Uudiskirju saadetud viimased \d+ päeva/)).toBeVisible();
  for (const forbidden of [/tellija/i, /auditoorium/i, /kokku/i]) {
    await expect(card.getByText(forbidden)).toHaveCount(0);
  }
});

test("the shop card never calls ordered value revenue", async ({ page }) => {
  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "E-pood" })
    .first();

  // The unit is the domain's own period label, so this asserts the card states
  // *a* period rather than guessing which: `resolve_period` decides the
  // export's window, and a seeded window is not the production one.
  await expect(card.getByText("Tellitud väärtus (KM-ta)")).toBeVisible();
  await expect(card.getByText(/\d{2}\.\d{2}\.\d{2}\s*–\s*\d{2}\.\d{2}\.\d{2}/)).toBeVisible();
  for (const forbidden of [/tulu/i, /käive/i, /laekumine/i]) {
    await expect(card.getByText(forbidden)).toHaveCount(0);
  }
});

test("the timeline is chronological, dated, and named for its horizon", async ({ page }) => {
  const timeline = page.getByRole("region", { name: "Järgmised 30 päeva" });

  await expect(timeline.getByText("Lähiajal ei ole tähtaegu ega sündmusi.")).toHaveCount(0);

  const stamps = await timeline
    .locator("time")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("datetime")));

  expect(stamps.length).toBeGreaterThan(0);
  expect(stamps.length).toBeLessThanOrEqual(10);
  expect([...stamps].sort()).toEqual(stamps);

  // Two lanes and no more: nothing else in DashKoda has a date.
  const lanes = new Set(
    await timeline.locator(".dk-badge").evaluateAll((nodes) =>
      nodes.map((node) => node.textContent.trim()),
    ),
  );
  for (const lane of lanes) {
    // Singular since 2026-08-18: each row is one, and `Sündmused` is the
    // dashboard's name, which reads as a heading beside a single title.
    expect(["Õigusloome", "Sündmus"]).toContain(lane);
  }
});

test("the page carries no interest strip", async ({ page }) => {
  /*
   * `Praegu enim huvi` left on 2026-08-18. Which single page, article and
   * product happened to lead is a browsing question, and the three domain cards
   * already carry the volumes those leaders are a slice of.
   */
  await expect(page.getByRole("heading", { name: "Praegu enim huvi" })).toHaveCount(0);
  await expect(page.getByText("lehevaatamist")).toHaveCount(0);
});

test("the homepage does not reproduce the Õigusloome lists", async ({ page }) => {
  // Two seven-row lists of `/oigusloome/` sat a scroll above the link to it
  // until 2026-08-17.
  await expect(page.getByRole("heading", { name: "Viimased välja saadetud" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Töös", exact: true })).toHaveCount(0);
});

test("a failed refresh keeps the figures and discloses itself", async ({ page }) => {
  /**
   * The seed marks the news feed as having failed its most recent check. Both
   * halves matter and they pull in opposite directions: the data must not be
   * withdrawn, and the page must not pretend the source is current.
   *
   * The disclosure moved to `/haldus/` on 2026-08-15 with `Andmete seis`; the
   * figures staying put on the overview is the half that must never move. Both
   * are checked, because a disclosure deleted from one page and never rendered
   * on the other would satisfy the overview half on its own.
   */
  await expect(page.locator("main")).not.toContainText("Andmete seis");

  const card = page
    .getByLabel("Põhinäitajad")
    .locator("article", { hasText: "Koduleht ja uudised" })
    .first();
  await expect(card.getByText(/külastust · \d+ p/)).toBeVisible();

  await page.goto("/haldus/");
  const status = page.getByRole("region", { name: "Andmete seis" });
  await expect(status.getByText("Vananenud pärast ebaõnnestunud uuendust")).toBeVisible();
});

test("the audience strip lists every channel and totals none of them", async ({ page }) => {
  const channels = page.getByRole("region", { name: "Auditooriumid" });

  await expect(channels).toBeVisible();
  for (const forbidden of ["Kokku auditoorium", "Auditoorium kokku", "Kogu auditoorium"]) {
    await expect(channels.getByText(forbidden)).toHaveCount(0);
  }
  // The website is not an audience row: its sessions are a card headline above,
  // and one measure under two labels invites a reconciliation nobody can do.
  await expect(channels.getByText("Kodulehe külastused")).toHaveCount(0);
  // One row per audience since 2026-08-18, largest first. The three lists were
  // three sub-rows of one cell, which made them look like parts of one
  // audience when they are three.
  const values = await channels
    .locator("dd")
    .evaluateAll((nodes) =>
      nodes
        .map((node) => Number(node.textContent.replace(/[^0-9]/g, "")))
        .filter((value) => Number.isFinite(value) && value > 0),
    );
  expect(values.length).toBeGreaterThan(3);
  expect([...values].sort((a, b) => b - a)).toEqual(values);
  // Hand-entered figures never borrow a collected feed's vocabulary.
  for (const forbidden of [/sünkroonitud/i, /API-ga ühendatud/i, /automaatselt uuendatud/i]) {
    await expect(channels.getByText(forbidden)).toHaveCount(0);
  }
});

test("the footer names when data last came in and where the dates live", async ({ page }) => {
  /*
   * `Uuendatud` is the last moment any source finished publishing — not a claim
   * that every figure above is current as of then. The seven sources are
   * collected on seven cadences, which is exactly what the link beside it is
   * for.
   */
  await expect(page.getByText(/Uuendatud \d{2}\.\d{2}\.\d{4} kell \d{2}:\d{2}/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Andmete kohta" })).toBeVisible();
});

test("an event already under way is listed without a start date", async ({ page }) => {
  /*
   * A year-long programme that opened on 1 January sorted to the top of
   * `Järgmised 30 päeva` under `01.01`, which is not a thing happening in the
   * next thirty days — it is a thing already happening. Those rows say `kestev`
   * and sit at the end.
   */
  const timeline = page.getByRole("region", { name: "Järgmised 30 päeva" });
  const ongoing = timeline.locator("li", { hasText: "kestev" });

  if ((await ongoing.count()) === 0) {
    return;
  }
  await expect(ongoing.first().locator("time")).toHaveCount(0);
});

test("data quality stays in Admin and does not return to the front page", async ({ page }) => {
  await expect(page.locator("main")).not.toContainText("Andmete seis");
  await expect(page.locator("main")).not.toContainText("Skeemi versioon");
  await expect(page.getByRole("link", { name: "Andmete kohta" })).toBeVisible();
});

test("the page never scrolls sideways with content on it", async ({ page }) => {
  await expectNoHorizontalOverflow(page);
});
