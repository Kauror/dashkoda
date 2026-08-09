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

/*
 * Estonian number formatting for axis ticks.
 *
 * ECharts formats a value-axis tick with English grouping, so 3820 is drawn as
 * `3,820` — which an Estonian reader parses as three point eight two. The
 * separator has to be a space and the decimal mark a comma, the same as
 * everywhere else on the page.
 *
 * The server names a format; this implements the finite set of names. No
 * business rule crosses over, only how a number is spelled.
 */
const GROUP = " ";

function groupThousands(value) {
  const whole = Math.round(Math.abs(value));
  return String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, GROUP);
}

const AXIS_FORMATS = {
  /* A plain count. */
  integer: (value) => (value < 0 ? "−" : "") + groupThousands(value),
  /* A share of a budget. */
  percent: (value) => groupThousands(value) + "%",
  /*
   * A diverging bar chart draws departures as negative numbers so the bars
   * extend leftwards. That negation is geometry, and an axis tick reading
   * `−40` states it as a business quantity — nobody reports minus forty
   * members leaving. The axis shows the magnitude, which is what both sides of
   * the chart actually measure.
   */
  absolute: (value) => groupThousands(value),
};

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

  /*
   * Full repaints, not dirty rectangles.
   *
   * `useDirtyRect` repaints only the regions ECharts believes changed. Moving
   * the pointer across a chart sweeps a narrow strip, and the strip it repaints
   * does not always cover the grid line that ran through it — so the line is
   * erased and not redrawn, and the reader watches pale vertical gaps open up
   * across the plot as they hover.
   *
   * It is an optimisation for charts with thousands of elements. These have
   * dozens, and a whole repaint of a 900×420 canvas is not something anyone can
   * perceive. Correct drawing is worth more than an optimisation nobody asked
   * for on a chart this size.
   */
  const instance = echarts.init(canvas, null, { renderer: "canvas" });
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

  /*
   * Axis tick spelling, where the server named a format.
   */
  for (const axis of ["xAxis", "yAxis"]) {
    const format = (dashkoda.axisFormat || {})[axis === "xAxis" ? "x" : "y"];
    const formatter = AXIS_FORMATS[format];
    if (!formatter || Array.isArray(option[axis])) {
      continue;
    }
    option[axis] = {
      ...(option[axis] || {}),
      axisLabel: { ...((option[axis] || {}).axisLabel || {}), formatter },
    };
  }

  if (dashkoda.tooltip) {
    option.tooltip = {
      ...(option.tooltip || {}),
      formatter: tooltipFormatter(dashkoda.tooltip),
      /*
       * The tooltip container is ECharts' own element, not ours, and its
       * default is a near-white panel with dark text. On this dark interface
       * that put our light readout text on a light panel and made every
       * tooltip on the page unreadable — the numbers were correct and nobody
       * could see them. The surface is set from the same tokens the rest of
       * the interface uses so it cannot drift out of the theme again.
       */
      backgroundColor: token("--color-elevated", "#1e242b"),
      borderColor: token("--color-border-strong", "#3d4954"),
      borderWidth: 1,
      padding: [10, 12],
      textStyle: { color: token("--color-text", "#e8edf2") },
      extraCssText: "box-shadow: 0 2px 6px rgb(0 0 0 / 0.4);",
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
