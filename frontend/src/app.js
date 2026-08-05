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

/*
 * Three mutually exclusive panels inside one card.
 *
 * Not a generalisation of `tabPair` over an index, because the CSP build cannot
 * pass one: a directive may name a property or a method and nothing else, so
 * `select(2)` is not expressible. Three named methods is what the build allows,
 * and spelling out the third pair of properties costs less than the indirection
 * that would be needed to avoid it.
 *
 * Same progressive enhancement as `tabPair`: the tablist carries `x-cloak`, so
 * before this runs there are no tabs and all three panels are visible, each
 * under its own heading.
 */
Alpine.data("tabTrio", () => ({
  firstSelected: true,
  secondSelected: false,
  thirdSelected: false,
  firstTabClass: TAB_ACTIVE,
  secondTabClass: TAB_BASE,
  thirdTabClass: TAB_BASE,

  select(index) {
    this.firstSelected = index === 0;
    this.secondSelected = index === 1;
    this.thirdSelected = index === 2;
    this.firstTabClass = this.firstSelected ? TAB_ACTIVE : TAB_BASE;
    this.secondTabClass = this.secondSelected ? TAB_ACTIVE : TAB_BASE;
    this.thirdTabClass = this.thirdSelected ? TAB_ACTIVE : TAB_BASE;
  },

  showFirst() {
    this.select(0);
  },

  showSecond() {
    this.select(1);
  },

  showThird() {
    this.select(2);
  },
}));

Alpine.start();
