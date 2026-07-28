import { defineConfig, devices } from "@playwright/test";

/*
 * Browser smoke tests run against the Compose development runtime that CI
 * starts before this suite. They never start a server themselves and never
 * touch a real environment. The PIN used here is synthetic and exists only for
 * CI; the real PIN is never present in this repository.
 */
const baseURL = process.env.DASHKODA_E2E_BASE_URL || "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "tablet",
      use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 } },
    },
    {
      name: "phone",
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: false },
    },
    {
      name: "phone-narrow",
      use: { ...devices["Desktop Chrome"], viewport: { width: 320, height: 720 }, isMobile: false },
    },
  ],
});
