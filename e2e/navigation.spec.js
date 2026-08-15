import { expect, test } from "@playwright/test";

import { signIn, watchConsole } from "./helpers.js";

/** The five top-level entries, in the order the board reads them. */
const TOP_LEVEL = ["Ülevaade", "Liikmeskond", "Õigusloome", "Sündmused", "Koduleht"];

/** Koduleht's three children, in order. Nesting is information architecture:
 *  they are three separately routed pages that share a menu parent and nothing
 *  else. */
const CHILDREN = ["Uudised", "E-pood", "Otsepostitused"];

const isDesktop = (page) => page.viewportSize().width >= 1024;

/** Open the menu on a narrow viewport, where it lives behind the hamburger. */
async function openMenu(page) {
  if (!isDesktop(page)) {
    await page.getByRole("button", { name: "Ava menüü" }).click();
  }
  return page.getByRole("navigation", { name: "Peamenüü" }).last();
}

test("navigation routes every module it names", async ({ page }) => {
  await signIn(page);

  const menu = await openMenu(page);
  for (const label of [...TOP_LEVEL, ...CHILDREN]) {
    await expect(menu.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
  // Fookusteemad was the last planned entry and is gone. Nothing in the sidebar
  // is an inert item any more, so nothing carries the Lisamisel badge.
  await expect(menu.locator('[aria-disabled="true"]')).toHaveCount(0);
  await expect(menu.getByText("Lisamisel")).toHaveCount(0);
});

test("the three children sit inside Koduleht's own list", async ({ page }) => {
  // Nested markup rather than indentation alone: the relationship has to be in
  // the document, or a screen reader is read six flat destinations.
  await signIn(page);

  const menu = await openMenu(page);
  const sublist = menu.locator(".dk-nav-sublist");

  await expect(sublist).toHaveCount(1);
  for (const label of CHILDREN) {
    await expect(sublist.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
});

test("a child page marks itself current and its parent as its parent", async ({ page }) => {
  await signIn(page);

  for (const [url, label] of [
    ["/uudised/", "Uudised"],
    ["/epood/", "E-pood"],
    ["/otsepostitused/", "Otsepostitused"],
  ]) {
    await page.goto(url);
    const menu = await openMenu(page);

    await expect(menu.getByRole("link", { name: label, exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // Koduleht is recognisable as the parent without claiming to be the page.
    const parent = menu.getByRole("link", { name: "Koduleht", exact: true });
    await expect(parent).toHaveClass(/dk-nav-item-ancestor/);
    await expect(parent).not.toHaveAttribute("aria-current", "page");
  }
});

test("Koduleht is itself a page, not a folder", async ({ page }) => {
  await signIn(page);

  const menu = await openMenu(page);
  await menu.getByRole("link", { name: "Koduleht", exact: true }).click();

  await expect(page).toHaveURL(/\/koduleht\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Koduleht");
});

test("the nested navigation stays readable at the narrowest width", async ({ page }) => {
  // The indent and the rule are what carry the nesting visually, and both eat
  // horizontal space that a 320px drawer does not have to spare.
  test.skip(isDesktop(page), "narrow layouts only");

  await signIn(page);
  const menu = await openMenu(page);

  for (const label of CHILDREN) {
    const link = menu.getByRole("link", { name: label, exact: true });
    await expect(link).toBeVisible();
    const box = await link.boundingBox();
    expect(box.width).toBeGreaterThan(0);
  }
  const scrolls = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(scrolls).toBe(false);
});

test("Admin is a quiet link by the build stamp, not a navigation entry", async ({ page }) => {
  await signIn(page);

  const menu = await openMenu(page);
  // Not among the primary entries...
  await expect(menu.getByRole("link", { name: "Admin", exact: true })).toHaveCount(0);

  // ...but present in the shell, and it opens the page.
  const admin = page.getByRole("link", { name: "Admin", exact: true }).last();
  await expect(admin).toBeVisible();
  await admin.click();

  await expect(page).toHaveURL(/\/haldus\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Admin");
});

test("the Admin link does not collide with the build stamp", async ({ page }) => {
  await signIn(page);
  await openMenu(page);

  const admin = page.getByRole("link", { name: "Admin", exact: true }).last();
  const stamp = page.locator(".dk-version").last();

  await expect(admin).toBeVisible();
  if ((await stamp.count()) === 0) {
    // No build stamp outside a built image, which is a legitimate state.
    return;
  }

  const [a, b] = [await admin.boundingBox(), await stamp.boundingBox()];
  const overlaps =
    a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  expect(overlaps).toBe(false);
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
