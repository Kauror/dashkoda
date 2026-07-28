import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

const EXPECTED_CSP =
  "default-src 'self'; base-uri 'self'; object-src 'none'; " +
  "frame-ancestors 'none'; form-action 'self'; script-src 'self'; " +
  "style-src 'self'; img-src 'self' data:; connect-src 'self'";

test("the strict Content Security Policy is unchanged and protected HTML is not cached", async ({
  page,
}) => {
  const login = await page.request.get("/sisene/");
  expect(login.headers()["content-security-policy"]).toBe(EXPECTED_CSP);
  expect(login.headers()["x-robots-tag"]).toBe("noindex, nofollow");

  await signIn(page);
  const protectedPage = await page.request.get("/");
  expect(protectedPage.headers()["content-security-policy"]).toBe(EXPECTED_CSP);
  expect(protectedPage.headers()["cache-control"]).toBe("private, no-store");
});

test("every script and stylesheet is served from this origin", async ({ page, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const external = [];
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith(origin) && !url.startsWith("data:")) {
      external.push(url);
    }
  });

  await signIn(page);

  expect(external).toEqual([]);
});

test("the ECharts bootstrap module loads under the same policy without errors", async ({
  page,
}) => {
  const errors = watchConsole(page);

  await signIn(page);
  await page.addScriptTag({ url: "/static/build/charts.js", type: "module" });
  await page.waitForFunction(() => Boolean(window.DashKodaCharts));

  const api = await page.evaluate(() => Object.keys(window.DashKodaCharts).sort());
  expect(api).toEqual(["chartTheme", "mountChart", "mountCharts", "readPayload"]);

  // No chart is mounted anywhere yet: PR-04 has no verified data to draw.
  const mounted = await page.evaluate(() => window.DashKodaCharts.mountCharts().length);
  expect(mounted).toBe(0);

  expect(errors).toEqual([]);
});

test("static file serving does not expose application routes", async ({ page }) => {
  const attempts = ["/static/", "/static/../manage.py", "/static/build/"];

  for (const path of attempts) {
    const response = await page.request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBeGreaterThanOrEqual(300);
  }
});
