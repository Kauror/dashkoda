/*
 * Instant readouts for the server-drawn trend chart.
 *
 * The chart in `dashboard/components/trend_chart.html` is plain SVG rendered
 * by Django: one full-height <rect> strip per observation date, each carrying
 * its reading as an SVG <title>. Without JavaScript the browser shows that
 * <title> as its own tooltip — after its own delay, only while the pointer
 * holds perfectly still, and never on touch. That stays the floor.
 *
 * This module lifts each <title> out of the live document and shows the same
 * text in one shared element per figure that follows the pointer with no
 * delay and also answers a tap. Lifting the <title> matters: left in place,
 * the browser would eventually stack its native tooltip on top of this one.
 * The svg's aria-label and the data table under the figure are untouched, so
 * what a screen reader or a keyboard reaches is exactly what it was.
 *
 * The strict Content Security Policy holds: everything declarative lives in
 * the `.dk-chart-tip` component class, and the two coordinates that cannot be
 * declared in advance are CSSOM property assignments, which `style-src 'self'`
 * permits. No style attribute is written into markup, no <style> element is
 * injected, and there is no markup string anywhere here.
 */

/* How far the readout sits from the pointer, so the pointer never covers it. */
const POINTER_CLEARANCE = 14;

function mountTrendTooltip(figure) {
  const svg = figure.querySelector("svg");
  if (!svg) {
    return;
  }

  /* The readings, keyed by their strip. Read once and removed from the
     document up front rather than looked up per event, so the native tooltip
     is gone from the first hover and not just from the second. */
  const readings = new Map();
  for (const strip of svg.querySelectorAll("rect")) {
    const title = strip.querySelector("title");
    if (title) {
      readings.set(strip, title.textContent);
      title.remove();
    }
  }
  if (readings.size === 0) {
    return;
  }

  const tip = document.createElement("div");
  tip.className = "dk-chart-tip";
  tip.hidden = true;
  figure.append(tip);

  const show = (event) => {
    const reading = readings.get(event.target);
    if (!reading) {
      tip.hidden = true;
      return;
    }
    tip.textContent = reading;
    tip.hidden = false;

    /* Centred over the pointer, folded back inside the figure so the readout
       never clips at the card edge, and above the pointer unless there is no
       room there. Measured after the text is set, because the text is what
       decides the width. */
    const bounds = figure.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const left = Math.min(Math.max(x - tip.offsetWidth / 2, 0), bounds.width - tip.offsetWidth);
    let top = y - tip.offsetHeight - POINTER_CLEARANCE;
    if (top < 0) {
      top = y + POINTER_CLEARANCE;
    }
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  };

  /* Pointer events cover mouse, pen and touch in one vocabulary: `pointermove`
     tracks a hover, `pointerdown` answers the tap a native <title> ignores. */
  svg.addEventListener("pointermove", show);
  svg.addEventListener("pointerdown", show);
  svg.addEventListener("pointerleave", () => {
    tip.hidden = true;
  });
}

/** Mount every `[data-trend-chart]` figure inside `root`. */
export function mountTrendTooltips(root = document) {
  for (const figure of root.querySelectorAll("[data-trend-chart]")) {
    mountTrendTooltip(figure);
  }
}
