import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

test("the freshness fragment is swapped in place without leaving the page", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);
  const url = page.url();

  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/dashboard/varskus/")),
    page.getByRole("button", { name: "Kontrolli uuesti" }).click(),
  ]);

  expect(response.status()).toBe(200);
  expect(response.request().headers()["hx-request"]).toBe("true");
  await expect(page).toHaveURL(url);
  await expect(page.locator("#freshness-region")).toContainText(
    "Andmeallikas ei ole veel ühendatud.",
  );
  expect(errors).toEqual([]);
});

test("the fragment route stays protected and answers HTMX with a redirect header", async ({
  page,
}) => {
  const anonymous = await page.request.get("/dashboard/varskus/", {
    headers: { "HX-Request": "true" },
    maxRedirects: 0,
  });

  expect(anonymous.status()).toBe(204);
  expect(anonymous.headers()["hx-redirect"]).toContain("/sisene/");
});

test("without JavaScript the refresh control is an ordinary form submission", async ({
  browser,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();

  await page.goto("/sisene/");
  await page.getByLabel("PIN-kood").fill(process.env.DASHKODA_E2E_PIN || "4071");
  await page.getByRole("button", { name: "Sisene" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koja juhatuse töölaud");

  await page.getByRole("button", { name: "Kontrolli uuesti" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Koja juhatuse töölaud");
  await expect(page.locator("#freshness-region")).toContainText(
    "Andmeallikas ei ole veel ühendatud.",
  );
  await expect(page.getByRole("navigation", { name: /Peamenüü/ }).first()).toBeAttached();

  await context.close();
});
