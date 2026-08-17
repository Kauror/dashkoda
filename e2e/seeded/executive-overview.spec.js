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

  const status = page.getByRole("region", { name: "Põhinäitajad" });

  for (const card of CARDS) {
    await expect(status.getByRole("heading", { name: card, level: 3 })).toBeVisible();
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
  const cards = page.getByRole("region", { name: "Põhinäitajad" }).locator("article");
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
  const order = ["Kiireloomuline", "Tähelepanu", "Tähelepanuväärne"];
  for (const word of words) {
    expect(order).toContain(word);
  }
  const positions = words.map((word) => order.indexOf(word));
  expect([...positions].sort()).toEqual(positions);
});

test("the membership card leads with the public directory count", async ({ page }) => {
  /*
   * The card prints one total and no captions. What holds the "never two
   * unlabelled totals" rule is that the directory count is the only member
   * total on the card at all — the report contributes ratios and a
   * joined/removed pair, and the period line names both cadences so a reader
   * can see which figure is recounted daily and which is reported monthly.
   */
  const card = page
    .getByRole("region", { name: "Põhinäitajad" })
    .locator("article", { hasText: "Liikmeskond" })
    .first();

  await expect(card.getByText("liiget")).toBeVisible();
  await expect(card.getByText("Tasunud liikmete osakaal")).toBeVisible();
  await expect(card.getByText(/kataloog \d/)).toBeVisible();
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
    .getByRole("region", { name: "Põhinäitajad" })
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
    .getByRole("region", { name: "Põhinäitajad" })
    .locator("article", { hasText: "Sündmused" })
    .first();

  await expect(card.getByText("sündmust järgmise 30 päeva jooksul")).toBeVisible();
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
    .getByRole("region", { name: "Põhinäitajad" })
    .locator("article", { hasText: "Koduleht ja uudised" })
    .first();

  await expect(card.getByText("külastust")).toBeVisible();
  await expect(card.getByText("Uudiste vaatamised")).toBeVisible();
  await expect(card.getByText("Uudiste osa kodulehe vaatamistest")).toBeVisible();
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
    .getByRole("region", { name: "Põhinäitajad" })
    .locator("article", { hasText: "Otsepostitused" })
    .first();

  await expect(card.getByText("e-Teataja avamismäär")).toBeVisible();
  await expect(card.getByText("e-Teataja klikimäär")).toBeVisible();
  for (const forbidden of [/tellija/i, /auditoorium/i, /kokku/i]) {
    await expect(card.getByText(forbidden)).toHaveCount(0);
  }
});

test("the shop card never calls ordered value revenue", async ({ page }) => {
  const card = page
    .getByRole("region", { name: "Põhinäitajad" })
    .locator("article", { hasText: "E-pood" })
    .first();

  await expect(card.getByText("ühikut ostetud")).toBeVisible();
  await expect(card.getByText("Tellitud väärtus (KM-ta)")).toBeVisible();
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
    expect(["Õigusloome", "Sündmused"]).toContain(lane);
  }
});

test("the interest strip is website, news and shop, each with its own metric", async ({ page }) => {
  /*
   * Three columns whose figures are not comparable — page views, article views,
   * acquired units — so each states its own metric name and nothing ranks them.
   * The next scheduled event was a fourth until 2026-08-17; it answered a
   * different question and events already hold a card and the whole timeline.
   */
  const interest = page.getByRole("region", { name: "Praegu enim huvi" });

  const headings = await interest
    .locator("article h3")
    .evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));
  expect(headings).toEqual(["Koduleht", "Uudised", "E-pood"]);

  await expect(interest.getByText("lehevaatamist")).toBeVisible();
  await expect(interest.getByText("vaatamist perioodil")).toBeVisible();
  await expect(interest.getByText("ühikut ostetud")).toBeVisible();
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
    .getByRole("region", { name: "Põhinäitajad" })
    .locator("article", { hasText: "Koduleht ja uudised" })
    .first();
  await expect(card.getByText("külastust")).toBeVisible();

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
  // Hand-entered figures never borrow a collected feed's vocabulary.
  for (const forbidden of [/sünkroonitud/i, /API-ga ühendatud/i, /automaatselt uuendatud/i]) {
    await expect(channels.getByText(forbidden)).toHaveCount(0);
  }
});

test("data quality stays in Admin and does not return to the front page", async ({ page }) => {
  await expect(page.locator("main")).not.toContainText("Andmete seis");
  await expect(page.locator("main")).not.toContainText("Skeemi versioon");
  await expect(page.getByRole("link", { name: "Andmete kohta" })).toBeVisible();
});

test("the page never scrolls sideways with content on it", async ({ page }) => {
  await expectNoHorizontalOverflow(page);
});
