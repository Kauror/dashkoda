import { expect, test } from "@playwright/test";

/**
 * The freshness fragment, which no page requests any more.
 *
 * The overview's connection-state strip — `Ühendatud andmeallikaid: 4/4`, the
 * last check time and a `Kontrolli uuesti` button that swapped the count over
 * htmx — was removed on 2026-08-11. The two tests that drove that button went
 * with it, and so did the only htmx in the application.
 *
 * `/dashboard/varskus/` is still served on purpose, so the strip can be put
 * back or moved to a page meant for whoever operates the collectors. What is
 * still worth asserting from a browser is the part no Django test covers: that
 * the route stays behind the viewer gate and answers an HTMX caller with a
 * redirect header rather than a login page inside the fragment.
 */
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
