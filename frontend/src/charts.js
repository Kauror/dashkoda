/*
 * DashKoda chart bootstrap.
 *
 * Bundled locally and mounted by the Liikmeskond page, which draws the
 * membership trend. It was written before any module had verified data to draw
 * and deliberately left unreferenced until one did; that is no longer the case.
 *
 * Contract for a module that mounts a chart:
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

/*
 * Tooltips are built on the server and rendered here as DOM nodes.
 *
 * Every figure in a readout was formatted in Python by the same helpers that
 * wrote the headline above the chart, so a tooltip cannot spell a number
 * differently from the page around it, and this file never has to know what a
 * percentage point is or how Estonian groups thousands.
 *
 * Nodes rather than a markup string, and `textContent` rather than `innerHTML`,
 * so a label that arrived from a source can never be interpreted as markup. The
 * Content Security Policy would stop a script from running, but a stray tag
 * would still wreck the layout, and the honest fix is not to build markup from
 * data at all.
 */
function tooltipNode(readout) {
  const root = document.createElement("div");
  root.className = "dk-chart-tooltip";

  const title = document.createElement("p");
  title.className = "dk-chart-tooltip-title";
  title.textContent = readout.title || "";
  root.append(title);

  const list = document.createElement("dl");
  list.className = "dk-chart-tooltip-rows";
  for (const row of readout.rows || []) {
    const term = document.createElement("dt");
    term.textContent = row.label;
    const value = document.createElement("dd");
    value.textContent = row.value;
    if (row.emphasis) {
      value.className = "dk-chart-tooltip-lead";
    }
    list.append(term, value);
  }
  root.append(list);

  if (readout.note) {
    const note = document.createElement("p");
    note.className = "dk-chart-tooltip-note";
    note.textContent = readout.note;
    root.append(note);
  }
  return root;
}

/**
 * An axis-trigger formatter that looks each point's readout up by the key the
 * server attached to the datum itself. Deriving the key from the axis value
 * would put a timezone between a point and its own tooltip.
 */
function tooltipFormatter(readouts) {
  return (params) => {
    const points = Array.isArray(params) ? params : [params];
    for (const point of points) {
      const key = point && point.data && point.data.tip;
      if (key && readouts[key]) {
        return tooltipNode(readouts[key]);
      }
    }
    return "";
  };
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
  /*
   * `dashkoda` carries what ECharts must not receive as option: the
   * server-rendered tooltip readouts, keyed by the `tip` each datum holds. It is
   * lifted out here so the rest of the payload is a plain ECharts option.
   */
  const { dashkoda = {}, ...option } = payload;

  /*
   * Axis labels the server supplied as a finite list, which the axis indexes
   * into. This is the whole of the "formatter metadata" contract: no date
   * arithmetic and no language crosses over, because a browser formatting a
   * month name would be a second place Estonian was spelled.
   */
  if (dashkoda.axisLabels && Array.isArray(dashkoda.axisLabels.x)) {
    const labels = dashkoda.axisLabels.x;
    option.xAxis = {
      ...(option.xAxis || {}),
      axisLabel: {
        ...((option.xAxis || {}).axisLabel || {}),
        formatter: (value) => labels[Math.round(value)] ?? "",
      },
    };
  }

  if (dashkoda.tooltip) {
    option.tooltip = {
      ...(option.tooltip || {}),
      formatter: tooltipFormatter(dashkoda.tooltip),
      // A tooltip that runs off the edge of a phone is a tooltip nobody can
      // read. ECharts keeps it inside the canvas when told to confine it.
      confine: true,
      enterable: false,
    };
  }

  /*
   * `animation` is applied after the payload, not before it. Spread the other
   * way round and a payload carrying its own `animation: true` — which every
   * server-built option did — silently overrode the reduced-motion preference,
   * so the setting looked respected in this file and was not respected on the
   * page. The reader's preference is the last word, so it is written last.
   */
  instance.setOption({
    ...chartTheme(),
    ...option,
    animation: option.animation !== false && !prefersReducedMotion(),
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
