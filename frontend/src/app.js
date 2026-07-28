/*
 * DashKoda application bundle.
 *
 * Everything here is local, bundled and loaded as a module script. There is no
 * CDN, no inline script and no runtime evaluation, so the strict
 * `script-src 'self'` policy stays unchanged.
 */
import Alpine from "@alpinejs/csp";
import htmx from "htmx.org";

/*
 * htmx normally injects a <style> element for `.htmx-indicator`. That would
 * need `style-src 'unsafe-inline'`, so it is disabled here and the equivalent
 * rules ship in the compiled stylesheet instead. The same value is also set as
 * a `htmx-config` meta tag, which applies before this module runs.
 */
htmx.config.includeIndicatorStyles = false;

/*
 * Alpine runs as the CSP build: directive values may only name a property or a
 * method of a registered component, never an inline expression. Components hold
 * local interface state only, never business data.
 */
Alpine.data("mobileNav", () => ({
  open: false,

  toggle() {
    if (this.open) {
      this.close();
    } else {
      this.show();
    }
  },

  show() {
    this.open = true;
    this.$nextTick(() => {
      const close = this.$refs.drawerClose;
      if (close) {
        close.focus();
      }
    });
  },

  close() {
    if (!this.open) {
      return;
    }
    this.open = false;
    this.$nextTick(() => {
      const toggle = this.$refs.drawerToggle;
      if (toggle) {
        toggle.focus();
      }
    });
  },
}));

Alpine.start();
