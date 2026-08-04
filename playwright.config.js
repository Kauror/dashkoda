import { defineConfig, devices } from "@playwright/test";

/*
 * Browser smoke tests run against the Compose development runtime that CI
 * starts before this suite. They never start a server themselves and never
 * touch a real environment. The PIN used here is synthetic and exists only for
 * CI; the real PIN is never present in this repository.
 */
const baseURL = process.env.DASHKODA_E2E_BASE_URL || "http://127.0.0.1:8000";

/*
 * Two mutually exclusive stages, selected by `DASHKODA_E2E_SEEDED`.
 *
 * The default stage runs `e2e/` against an empty database, where the point is
 * that the dashboard shows honest empty states — `shell.spec.js` even asserts
 * that no digit appears anywhere. The seeded stage runs `e2e/seeded/` after
 * `manage.py seed_e2e_data` has published synthetic content, where the point is
 * the opposite: real content, long enough to truncate and wrap.
 *
 * They cannot share a run, because each one's expectations are the other's
 * failure. Selecting by directory keeps that separation impossible to get
 * wrong by accident, and each stage writes its own report so both survive.
 */
const seeded = process.env.DASHKODA_E2E_SEEDED === "1";

export default defineConfig({
  testDir: seeded ? "./e2e/seeded" : "./e2e",
  testIgnore: seeded ? undefined : "**/seeded/**",
  outputDir: seeded ? "./test-results-seeded" : "./test-results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [
        ["list"],
        [
          "html",
          {
            open: "never",
            outputFolder: seeded ? "playwright-report-seeded" : "playwright-report",
          },
        ],
      ]
    : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  /*
   * Six widths, chosen to sit on both sides of every layout breakpoint the
   * design system defines: 320 and 375 below `sm`, 768 at the tablet grid, 1024
   * where the persistent sidebar replaces the drawer, 1440 for an ordinary
   * desktop and 1920 for a meeting-room display — which is also where the
   * six-slot channel band first fits on one line.
   */
  projects: [
    {
      name: "wide",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "laptop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1024, height: 768 } },
    },
    {
      name: "tablet",
      use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 } },
    },
    {
      name: "phone",
      use: { ...devices["Desktop Chrome"], viewport: { width: 375, height: 812 }, isMobile: false },
    },
    {
      name: "phone-narrow",
      use: { ...devices["Desktop Chrome"], viewport: { width: 320, height: 720 }, isMobile: false },
    },
  ],
});
