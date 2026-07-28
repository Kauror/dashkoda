import { expect, test } from "@playwright/test";

import {
  LOGOUT_BUTTON,
  LOGOUT_FORM,
  expectNoHorizontalOverflow,
  signIn,
  visibleLogout,
  watchConsole,
} from "./helpers.js";

test("login page loads, is branded and stays within the viewport", async ({ page }) => {
  const errors = watchConsole(page);

  await page.goto("/sisene/");

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sisene DashKodasse");
  await expect(page.getByLabel("PIN-kood")).toBeVisible();
  await expect(page.getByRole("img", { name: /Kaubandus-Tööstuskoda/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  expect(errors).toEqual([]);
});

test("the synthetic PIN opens the dashboard shell", async ({ page }) => {
  const errors = watchConsole(page);

  await signIn(page);

  await expect(page).toHaveURL(/\/$/);
  expect(errors).toEqual([]);
});

test("a wrong PIN is refused with a readable message", async ({ page }) => {
  await page.goto("/sisene/");
  await page.getByLabel("PIN-kood").fill("0000");
  await page.getByRole("button", { name: "Sisene" }).click();

  await expect(page.getByText("PIN-kood ei ole õige.")).toBeVisible();
  await expect(page).toHaveURL(/\/sisene\//);
});

test("logout ends the session and the protected root redirects again", async ({ page }) => {
  await signIn(page);

  await visibleLogout(page).click();
  await expect(page).toHaveURL(/\/sisene\//);

  await page.goto("/");
  await expect(page).toHaveURL(/\/sisene\/\?next=/);
});

test("each layout shows exactly one logout control", async ({ page }) => {
  await signIn(page);
  const isDesktop = page.viewportSize().width >= 1024;

  await expect(visibleLogout(page)).toHaveCount(1);

  if (isDesktop) {
    // The desktop header must not repeat the control the sidebar already has,
    // while keeping its breadcrumb and its slot for later global controls.
    await expect(page.locator(`header ${LOGOUT_BUTTON}`)).toHaveCount(0);
    await expect(page.locator(`.dk-sidebar ${LOGOUT_BUTTON}`)).toHaveCount(1);
    await expect(page.locator("#page-header")).toBeVisible();
    await expect(page.locator("#page-header")).toContainText("Ülevaade");
  } else {
    await expect(page.locator(`header ${LOGOUT_BUTTON}`)).toHaveCount(1);

    await page.getByRole("button", { name: "Ava menüü" }).click();
    await expect(page.locator(`#main-drawer ${LOGOUT_BUTTON}`)).toHaveCount(1);
  }
});

test("every logout control is a CSRF-protected POST and GET is refused", async ({ page }) => {
  await signIn(page);

  const forms = await page.locator(LOGOUT_FORM).evaluateAll((nodes) =>
    nodes.map((form) => ({
      method: (form.getAttribute("method") || "").toLowerCase(),
      hasToken: Boolean(form.querySelector('input[name="csrfmiddlewaretoken"]')),
    })),
  );

  expect(forms.length).toBeGreaterThan(0);
  for (const form of forms) {
    expect(form.method).toBe("post");
    expect(form.hasToken).toBe(true);
  }

  const refused = await page.request.get("/logi-valja/", { maxRedirects: 0 });
  expect(refused.status()).toBe(405);
});

test("health endpoints stay public and the root stays protected", async ({ page }) => {
  const live = await page.request.get("/health/live/");
  const ready = await page.request.get("/health/ready/");
  const robots = await page.request.get("/robots.txt");
  const root = await page.request.get("/", { maxRedirects: 0 });

  expect(live.status()).toBe(200);
  expect(ready.status()).toBe(200);
  expect(robots.status()).toBe(200);
  expect(root.status()).toBe(302);
  expect(root.headers()["location"]).toContain("/sisene/");
});
