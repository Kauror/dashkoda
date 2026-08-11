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

    offenders.sort((left, right) => right.right - left.right);
    return {
      overflow: root.scrollWidth - limit,
      limit,
      offenders: offenders.slice(0, 6),
    };
  });

  const detail = report.offenders
    .map(
      (item) =>
        `\n  right=${item.right} (limit ${report.limit}) ${item.position}` +
        `${item.clipped ? " [inside a scroller]" : " [UNCONTAINED]"} ${item.what}`,
    )
    .join("");
  expect(report.overflow, `page scrolls sideways; widest boxes:${detail}`).toBeLessThanOrEqual(0);
}
