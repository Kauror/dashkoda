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
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koja juhatuse töölaud");
}

/** The page itself must never scroll sideways, at any supported width. */
export async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth - root.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(0);
}
