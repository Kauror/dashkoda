import { expect } from "@playwright/test";

/**
 * Synthetic CI-only viewer PIN. The real PIN is never written to this
 * repository, to test code, or to workflow files.
 */
export const TEST_PIN = process.env.DASHKODA_E2E_PIN || "4071";

/** Collect console errors and uncaught page errors for a test. */
export function watchConsole(page) {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      errors.push(message.text());
    }
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

export async function signIn(page) {
  await page.goto("/sisene/");
  await page.getByLabel("PIN-kood").fill(TEST_PIN);
  await page.getByRole("button", { name: "Sisene" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koja töölaud");
}

export const LOGOUT_FORM = 'form[action="/logi-valja/"]';
export const LOGOUT_BUTTON = `${LOGOUT_FORM} button:visible`;

/**
 * Every logout control that is actually on screen right now. Each layout keeps
 * its own control in the DOM, so counting only the visible ones is what proves
 * a layout is not showing a duplicate.
 */
export function visibleLogout(page) {
  return page.locator(LOGOUT_BUTTON);
}

/**
 * The page itself must never scroll sideways, at any supported width.
 *
 * The failure names the elements sticking out past the viewport, because
 * "expected <= 0, received 324" on a page with forty nested containers says
 * only that something is wrong and nothing about what. Each offender is
 * reported with whether an ancestor was clipping it: an element extending past
 * the edge *inside* a scrolling container is doing exactly what the container
 * is for, so the interesting ones are those nothing contains.
 */
export async function expectNoHorizontalOverflow(page) {
  const report = await page.evaluate(() => {
    const root = document.documentElement;
    const limit = root.clientWidth;
    const offenders = [];

    for (const node of document.querySelectorAll("body *")) {
      const box = node.getBoundingClientRect();
      if (box.width === 0 || box.right <= limit + 1) {
        continue;
      }
      let clipped = false;
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        if (getComputedStyle(parent).overflowX !== "visible") {
          clipped = true;
          break;
        }
      }
      const style = getComputedStyle(node);
      const name = node.tagName.toLowerCase();
      const classes = String(node.className || "").slice(0, 60);
      offenders.push({
        what: `${name}${classes ? `.${classes.trim().split(/\s+/).join(".")}` : ""}`,
        right: Math.round(box.right),
        position: style.position,
        clipped,
      });
    }

    /*
     * Uncontained first, and only then by width. Sorting by width alone buries
     * the diagnosis: a `min-w-max` table inside a scrolling wrapper is the
     * widest box on the page by design and fills the whole list, while the box
     * that actually widened the document — which is always a narrower one,
     * since the document stops where it stops — never appears.
     */
    offenders.sort(
      (left, right) => Number(left.clipped) - Number(right.clipped) || right.right - left.right,
    );

    /*
     * And the direct answer: which boxes end where the document ends. The
     * scrollable width is set by the furthest box the document actually
     * accounts for, so a box sitting on that edge is the one to fix — anything
     * reaching past it is already being clipped by something.
     */
    const edge = offenders.filter((item) => Math.abs(item.right - root.scrollWidth) <= 2);

    return {
      overflow: root.scrollWidth - limit,
      documentWidth: root.scrollWidth,
      limit,
      atEdge: edge.slice(0, 4),
      offenders: offenders.slice(0, 8),
    };
  });

  const detail = report.offenders
    .map(
      (item) =>
        `\n  right=${item.right} ${item.position}` +
        `${item.clipped ? " [inside a scroller]" : " [UNCONTAINED]"} ${item.what}`,
    )
    .join("");
  const describe = (item) =>
    `\n  right=${item.right} ${item.position}` +
    `${item.clipped ? " [inside a scroller]" : " [UNCONTAINED]"} ${item.what}`;
  const edge = report.atEdge.length
    ? `\n at the document's own edge (${report.documentWidth}px):${report.atEdge.map(describe).join("")}`
    : "";
  const heading =
    `page scrolls sideways: document ${report.documentWidth} > viewport ${report.limit}.` +
    `${edge}\n boxes past the viewport, uncontained first:`;
  expect(report.overflow, `${heading}${detail}`).toBeLessThanOrEqual(0);
}
