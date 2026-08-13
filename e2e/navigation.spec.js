import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

const ROUTED = ["Ülevaade", "Liikmeskond", "Õigusloome", "Sündmused", "Uudised", "Nähtavus"];

const isDesktop = (page) => page.viewportSize().width >= 1024;

test("navigation routes every module it names", async ({ page }) => {
  await signIn(page);

  if (!isDesktop(page)) {
    await page.getByRole("button", { name: "Ava menüü" }).click();
  }

  const menu = page.getByRole("navigation", { name: "Peamenüü" }).last();
  for (const label of ROUTED) {
    await expect(menu.getByRole("link", { name: label })).toBeVisible();
  }
  // Fookusteemad was the last planned entry and is gone. Nothing in the sidebar
  // is an inert item any more, so nothing carries the Lisamisel badge.
  await expect(menu.locator('[aria-disabled="true"]')).toHaveCount(0);
  await expect(menu.getByText("Lisamisel")).toHaveCount(0);
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
