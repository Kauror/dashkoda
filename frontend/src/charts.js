/*
 * DashKoda chart bootstrap.
 *
 * This module is bundled locally and is deliberately not referenced by any
 * template yet: PR-04 has no verified data, and an invented chart would be a
 * fabricated metric. It exists so that the first real data module can mount a
 * chart without redesigning the frontend build or the Content Security Policy.
 *
 * Contract for later modules:
 *
 *   <figure data-chart data-chart-payload="membership-trend">
 *     <div data-chart-canvas role="img" aria-label="...text alternative..."></div>
 *     <p data-chart-empty>Andmeallikas ei ole veel ühendatud.</p>
 *     <figcaption>...title...</figcaption>
 *     <table data-chart-table>...same values as rows...</table>
 *   </figure>
 *   <script type="application/json" id="membership-trend">{ ... }</script>
 *
 * The payload is read from a non-executable `application/json` block, so no
 * inline script and no `unsafe-eval` is ever required. The table is the
 * accessible alternative and stays in the document; only the canvas is hidden
 * when there is nothing to draw. The text description is the canvas's own
 * `aria-label` — it used to be a visible caption and the board asked for it
 * off the page.
 */
import * as echarts from "echarts";

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const token = (name, fallback) => {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
};

/**
 * Theme derived from the design-system tokens so charts cannot drift away from
 * the rest of the interface.
 */
export function chartTheme() {
  return {
    textStyle: {
      color: token("--color-text-secondary", "#9aa7b4"),
      fontFamily: token("--font-sans", "system-ui, sans-serif"),
      fontSize: 12,
    },
    backgroundColor: "transparent",
    color: [
      token("--color-brand", "#009fda"),
      token("--color-info", "#5fb3e8"),
      token("--color-success", "#4fbf95"),
      token("--color-warning", "#e3ac4e"),
      token("--color-danger", "#ef7d6e"),
    ],
  };
}

/**
 * Read a chart payload from a same-document `application/json` block.
 *
 * Returns `null` when the block is missing or unparsable; callers then keep the
 * truthful empty state instead of guessing values.
 */
export function readPayload(id) {
  const block = document.getElementById(id);
  if (!block || block.type !== "application/json") {
    return null;
  }
  try {
    return JSON.parse(block.textContent);
  } catch (error) {
    console.warn("DashKoda: unreadable chart payload", id, error);
    return null;
  }
}

const hasSeriesData = (payload) =>
  Boolean(payload) &&
  Array.isArray(payload.series) &&
  payload.series.some(
    (series) => Array.isArray(series.data) && series.data.length > 0,
  );

/**
 * Mount one `[data-chart]` figure. Does nothing and reports `null` when the
 * payload carries no data points, leaving the empty state visible.
 */
export function mountChart(figure) {
  const canvas = figure.querySelector("[data-chart-canvas]");
  const empty = figure.querySelector("[data-chart-empty]");
  const payload = readPayload(figure.dataset.chartPayload || "");

  if (!canvas || !hasSeriesData(payload)) {
    if (canvas) {
      canvas.hidden = true;
    }
    if (empty) {
      empty.hidden = false;
    }
    return null;
  }

  canvas.hidden = false;
  if (empty) {
    empty.hidden = true;
  }

  const instance = echarts.init(canvas, null, {
    renderer: "canvas",
    useDirtyRect: true,
  });
  instance.setOption({
    ...chartTheme(),
    animation: !prefersReducedMotion(),
    ...payload,
  });

  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(canvas);
    instance.dashkodaDisconnect = () => observer.disconnect();
  } else {
    const onResize = () => instance.resize();
    window.addEventListener("resize", onResize);
    instance.dashkodaDisconnect = () =>
      window.removeEventListener("resize", onResize);
  }

  return instance;
}

/** Mount every `[data-chart]` figure inside `root`. */
export function mountCharts(root = document) {
  return Array.from(root.querySelectorAll("[data-chart]"))
    .map(mountChart)
    .filter(Boolean);
}

/*
 * Exposed for the browser smoke test, which loads this module on its own and
 * asserts that it initialises under the strict CSP without console errors.
 */
window.DashKodaCharts = { chartTheme, readPayload, mountChart, mountCharts };

/*
 * Mount on load. A page opts in by including this module, and the alternative
 * would be an inline script calling `mountCharts()`, which the Content Security
 * Policy forbids and should keep forbidding.
 *
 * The text summary and the data table are rendered server-side and are already
 * in the document, so a page where this never runs is still complete — it just
 * shows the numbers as a table instead of as a picture.
 */
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => mountCharts());
} else {
  mountCharts();
}
