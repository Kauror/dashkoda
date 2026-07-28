import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, signIn, watchConsole } from "./helpers.js";

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

  await page.getByRole("button", { name: "Logi välja" }).first().click();
  await expect(page).toHaveURL(/\/sisene\//);

  await page.goto("/");
  await expect(page).toHaveURL(/\/sisene\/\?next=/);
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
