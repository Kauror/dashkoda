import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

const PLANNED = ["Liikmeskond", "Arvamused", "Sündmused", "Uudised", "Finantsid"];
const ROUTED = ["Ülevaade", "Õigusloome"];

const isDesktop = (page) => page.viewportSize().width >= 1024;

test("navigation routes the implemented modules and marks the rest planned", async ({ page }) => {
  await signIn(page);

  if (!isDesktop(page)) {
    await page.getByRole("button", { name: "Ava menüü" }).click();
  }

  const menu = page.getByRole("navigation", { name: "Peamenüü" }).last();
  for (const label of ROUTED) {
    await expect(menu.getByRole("link", { name: label })).toBeVisible();
  }
  for (const label of PLANNED) {
    // A planned module is an inert item carrying its label and the Lisamisel
    // badge, never a link.
    const item = menu.locator('[aria-disabled="true"]', { hasText: label });
    await expect(item).toBeVisible();
    await expect(item).toContainText("Lisamisel");
    await expect(menu.getByRole("link", { name: label })).toHaveCount(0);
  }
});

test("the desktop sidebar is persistent and the hamburger is absent", async ({ page }) => {
  test.skip(!isDesktop(page), "desktop layout only");

  await signIn(page);

  await expect(page.getByRole("navigation", { name: "Peamenüü" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ava menüü" })).toBeHidden();
});

test("the mobile drawer opens, closes and reports its state", async ({ page }) => {
  test.skip(isDesktop(page), "narrow layouts only");
  const errors = watchConsole(page);

  await signIn(page);
  const toggle = page.getByRole("button", { name: "Ava menüü" });
  const drawer = page.locator("#main-drawer");

  await expect(toggle).toHaveAttribute("aria-controls", "main-drawer");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(drawer).toBeHidden();

  await toggle.click();
  await expect(drawer).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Sulge menüü" })).toBeFocused();

  await page.getByRole("button", { name: "Sulge menüü" }).click();
  await expect(drawer).toBeHidden();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();

  expect(errors).toEqual([]);
});

test("Escape closes the mobile drawer", async ({ page }) => {
  test.skip(isDesktop(page), "narrow layouts only");

  await signIn(page);
  const toggle = page.getByRole("button", { name: "Ava menüü" });
  const drawer = page.locator("#main-drawer");

  await toggle.click();
  await expect(drawer).toBeVisible();

  await page.keyboard.press("Escape");

  await expect(drawer).toBeHidden();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("the drawer toggle meets a usable touch size", async ({ page }) => {
  test.skip(isDesktop(page), "narrow layouts only");

  await signIn(page);
  const box = await page.getByRole("button", { name: "Ava menüü" }).boundingBox();

  expect(box.width).toBeGreaterThanOrEqual(40);
  expect(box.height).toBeGreaterThanOrEqual(40);
});
