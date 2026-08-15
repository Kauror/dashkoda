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

/** The registered ECharts theme's name. One theme, re-registered per mount. */
const THEME_NAME = "dashkoda";

/**
 * Axis styling, applied to every axis of every chart.
 *
 * ECharts' own defaults are written for a light background and **override the
 * theme's `textStyle`** for axis labels specifically. On this surface that put
 * the labels at `#6E7079` — 3.47:1, under the 4.5:1 a reader needs — while the
 * gridline default `#E0E6F1` landed at 13.67:1. The result was exactly
 * backwards: the text you have to read was the faintest thing on the card and
 * the grid behind it was the loudest.
 *
 * So both are named here rather than left to ECharts. Labels take the same
 * secondary ink as the rest of the interface (6.98:1) and the grid drops to
 * `border-strong` (1.86:1) — visible when you look for it, gone when you are
 * reading the data. The axis line and ticks go with it: a category axis whose
 * labels are legible does not also need a rule under them.
 */
const AXIS_BASE = () => ({
  axisLabel: { color: token("--color-text-secondary", "#9aa7b4") },
  axisLine: { lineStyle: { color: token("--color-border-strong", "#3d4954") } },
  axisTick: { show: false },
  splitLine: { lineStyle: { color: token("--color-border-strong", "#3d4954") } },
});

/**
 * The legend, for the same reason and with the same fix.
 *
 * `legend.textStyle` has its own light-background default that outranks the
 * theme's `textStyle`, exactly as `axisLabel` does — so leaving it unnamed drew
 * the legend labels at roughly 2.2:1 against the card while the axis labels
 * beside them sat at 6.98:1. On a stacked chart the legend *is* the key: it is
 * the only thing that says which colour is `Veebis`. Dimming it below the
 * numbers it explains is the worst of both — the reader can see the value and
 * not what it counts.
 *
 * `inactiveColor` is the swatch of a series the reader has toggled off. Its
 * default is `#ccc`, which on this surface is brighter than the active labels
 * and reads as emphasis rather than as "off"; muted ink says off while staying
 * legible enough to toggle back on.
 */
const LEGEND_BASE = () => ({
  textStyle: { color: token("--color-text-secondary", "#9aa7b4") },
  inactiveColor: token("--color-text-muted", "#7d8b99"),
  inactiveBorderColor: token("--color-text-muted", "#7d8b99"),
  pageTextStyle: { color: token("--color-text-secondary", "#9aa7b4") },
  pageIconColor: token("--color-text-secondary", "#9aa7b4"),
  pageIconInactiveColor: token("--color-border-strong", "#3d4954"),
});

/**
 * The tooltip surface.
 *
 * ECharts' tooltip is its own DOM element with a near-white panel and dark text
 * by default, so on this interface it renders as a white card in the middle of
 * a dark page. This *was* fixed — but only inside the branch that runs when a
 * payload carries server-rendered readouts, which left every chart that just
 * says `{"trigger": "axis"}` still drawing the white panel. Ten builders across
 * Õigusloome, Liikmeskond, Uudised and Otsepostitused were in that state.
 *
 * A surface belongs to the theme, not to the branch that happens to also set a
 * formatter: put it here and a chart cannot opt out of it by not needing a
 * custom readout. `confine` goes with it for the same reason — a tooltip that
 * runs off the edge of a phone is unreadable whoever built it.
 */
const TOOLTIP_BASE = () => ({
  backgroundColor: token("--color-elevated", "#1e242b"),
  borderColor: token("--color-border-strong", "#3d4954"),
  borderWidth: 1,
  padding: [10, 12],
  textStyle: { color: token("--color-text", "#e8edf2") },
  extraCssText: "box-shadow: 0 2px 6px rgb(0 0 0 / 0.4);",
  confine: true,
  enterable: false,
});

/**
 * Theme derived from the design-system tokens so charts cannot drift away from
 * the rest of the interface.
 *
 * ## The categorical order
 *
 * Six slots, in a fixed order, and **the order is the accessibility mechanism
 * rather than decoration** — it is what keeps every neighbouring pair apart for
 * a colour-blind reader. Do not reorder it, and do not append a seventh: a
 * seventh series folds into `Muu`, which is what the builders already do.
 *
 * It replaced a five-slot list that reused `success`, `warning` and `danger` as
 * series 3, 4 and 5. That was wrong twice over. Those are **status** colours,
 * reserved for saying a thing is good or wrong, and a chart that spends them on
 * "the third category" leaves nothing to say it with. And slots 1 and 2 were
 * both blue — `brand` against `info` measured ΔE 7.8 for a reader with full
 * colour vision, against a floor of 15, and 2.5 under tritanopia. They were not
 * hard to tell apart; for some readers they were the same colour.
 *
 * The Chamber blue stays slot 1 — it is the CVI brand and this is the Chamber's
 * dashboard. The rest are stepped for a dark surface. The set is validated:
 * every adjacent pair clears the CVD and normal-vision floors, every slot sits
 * in the dark lightness band, and all six clear 3:1 against the card.
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
      token("--color-series-2", "#d95926"),
      token("--color-series-3", "#199e70"),
      token("--color-series-4", "#c98500"),
      token("--color-series-5", "#d55181"),
      token("--color-series-6", "#9085e9"),
    ],
    categoryAxis: AXIS_BASE(),
    valueAxis: AXIS_BASE(),
    timeAxis: AXIS_BASE(),
    logAxis: AXIS_BASE(),
    legend: LEGEND_BASE(),
    tooltip: TOOLTIP_BASE(),
  };
}

/**
 * How any label drawn onto the plot is written.
 *
 * Functions rather than constants: a token is read from the live document, and
 * these are evaluated per chart so a theme change is picked up on the next
 * render instead of being frozen at module load.
 *
 * `textBorderWidth: 0` removes the outline ECharts derives for a label by
 * itself. Against this background it derives a pale one, which around bold
 * 12px digits renders as a light smear the size of the text — the reason the
 * bar counts and the budget line's name looked boxed out rather than written.
 */
const LABEL_BASE = () => ({
  color: token("--color-text", "#e8edf2"),
  textBorderWidth: 0,
});

/**
 * A label that names something rather than restating a datum — the end of a
 * line, a reference line — and therefore has to hold its own against whatever
 * it is drawn over. Same surface as the tooltip, from the same tokens.
 */
const LABEL_CHIP = () => ({
  ...LABEL_BASE(),
  fontSize: 13,
  fontWeight: 600,
  backgroundColor: token("--color-elevated", "#1e242b"),
  borderColor: token("--color-border-strong", "#3d4954"),
  borderWidth: 1,
  borderRadius: 4,
  padding: [3, 6],
  distance: 8,
});

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
    /*
     * A hover that landed on the line rather than on one of its points. There
     * is no reading to state — the pointer is between observations — but the
     * reader still asked a question by hovering, and on a chart of several
     * years the question is almost always "which year is this one". Naming the
     * series answers it; saying nothing leaves the hover looking broken.
     */
    const named = points.find((point) => point && point.seriesName);
    return named ? tooltipNode({ title: named.seriesName, rows: [] }) : "";
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
  /*
   * Registered as a real theme rather than spread into `setOption`.
   *
   * That distinction is load-bearing. `textStyle`, `color` and `backgroundColor`
   * are valid *option* keys, so spreading them worked and hid the fact that the
   * rest of a theme is not: `categoryAxis`, `valueAxis` and their siblings are
   * only read when ECharts resolves a registered theme, and passed to
   * `setOption` they are inert. Axis styling written that way would apply to
   * nothing and report no error — the failure is silence, not a stack trace.
   *
   * Re-registered per mount rather than once at module load, for the same
   * reason the label helpers are functions: the values come from live CSS
   * custom properties, and freezing them at import would outlast a theme change.
   * `registerTheme` overwrites by name, so this is idempotent.
   */
  echarts.registerTheme(THEME_NAME, chartTheme());
  const instance = echarts.init(canvas, THEME_NAME, { renderer: "canvas" });
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
      /*
       * Only the readout. The panel it is drawn on comes from `TOOLTIP_BASE`
       * in the theme, so a chart without server-rendered readouts gets the
       * same surface as one with them — which was not true while these keys
       * lived here.
       */
      formatter: tooltipFormatter(dashkoda.tooltip),
    };
  }

  /*
   * Labels drawn on the canvas, made legible.
   *
   * These charts have no legend on purpose: a line is named at its own last
   * point and a bar states its own count, so nothing has to be matched against
   * a swatch in a corner. That only works if the writing can be read, and
   * ECharts' defaults are not written for a dark plot area — a label takes the
   * series colour, sits at the body size, and carries an automatically derived
   * outline that comes out pale. Thin mid-blue digits behind a light smear,
   * with gridlines running through them.
   *
   * Colour is settled here rather than where the chart is built because this
   * is the only side that can read a CSS custom property; what a label *says*
   * — `formatter`, `position`, `distance` — stays with the chart, and the
   * server's keys are spread last so they win.
   */
  if (Array.isArray(option.series)) {
    option.series = option.series.map((series) => {
      if (!series || typeof series !== "object") {
        return series;
      }
      const styled = {
        ...series,
        /*
         * Every label a series draws, whether or not this series draws one.
         * Set at series level so it also reaches the per-datum labels the bar
         * charts carry: ECharts resolves a datum's `label` over the series'.
         *
         * `textBorderWidth: 0` is the important one. ECharts derives a text
         * outline for labels automatically, and against the plot background it
         * derives a pale one — which around 12px bold digits renders as a
         * light smear roughly the size of the text. That is what made the bar
         * counts and the budget line's name look boxed out.
         */
        label: { ...LABEL_BASE(), ...(series.label || {}) },
      };
      if (styled.endLabel && styled.endLabel.show === true) {
        styled.endLabel = { ...LABEL_CHIP(), ...styled.endLabel };
      }
      if (styled.markLine) {
        // Names a reference line rather than a datum, and lands inside the
        // plot where gridlines cross it. It gets the chip for the same reason
        // the end labels do.
        styled.markLine = {
          ...styled.markLine,
          label: { ...LABEL_CHIP(), ...(styled.markLine.label || {}) },
        };
      }
      return styled;
    });
  }

  /*
   * `animation` is applied after the payload, not before it. Spread the other
   * way round and a payload carrying its own `animation: true` — which every
   * server-built option did — silently overrode the reduced-motion preference,
   * so the setting looked respected in this file and was not respected on the
   * page. The reader's preference is the last word, so it is written last.
   */
  instance.setOption({
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
