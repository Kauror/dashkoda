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

/*
 * Two mutually exclusive panels inside one card.
 *
 * The CSP build allows a directive to name a property or a method and nothing
 * else. Everything a directive reads is therefore a plain reactive property,
 * updated by the two methods the tabs call. Getters would be terser, but a
 * directive silently resolving to `undefined` would hide both panels rather
 * than fail loudly, and these tabs only render once there is data to show — so
 * the browser suite, which runs against an empty database, would not catch it.
 *
 * Progressive enhancement: the tablist itself carries `x-cloak`, so before this
 * runs there are no tabs and both panels are simply visible, each under its own
 * heading. Nothing is hidden that cannot be revealed again.
 */
const TAB_BASE = "dk-tab";
const TAB_ACTIVE = "dk-tab dk-tab-active";

Alpine.data("tabPair", () => ({
  firstSelected: true,
  secondSelected: false,
  firstTabClass: TAB_ACTIVE,
  secondTabClass: TAB_BASE,

  select(first) {
    this.firstSelected = first;
    this.secondSelected = !first;
    this.firstTabClass = first ? TAB_ACTIVE : TAB_BASE;
    this.secondTabClass = first ? TAB_BASE : TAB_ACTIVE;
  },

  showFirst() {
    this.select(true);
  },

  showSecond() {
    this.select(false);
  },
}));


Alpine.start();
