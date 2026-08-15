"""The one navigation question a template cannot ask for itself.

`nav_item.html` compares an entry's key with `active_nav` to decide the current
page, which needs no help. What it cannot express is "is the active page one of
*this* entry's children" — Django templates call methods without arguments, so
a `NavItem.owns(key)` would never be reachable from the markup, and the
alternative is every view computing a trail and passing it into the shell.

So the lookup lives here, over the same tuple the sidebar is already rendering.
"""

from __future__ import annotations

from django import template

from apps.dashboard.navigation import NavItem

register = template.Library()


@register.filter
def has_active_child(item: NavItem, active_nav: str) -> bool:
    """Whether the page on screen is one of `item`'s children.

    Used to keep `Koduleht` visibly recognisable as the parent while `Uudised`,
    `E-pood` or `Otsepostitused` is open. The parent is *not* marked
    `aria-current`: the child holds that, and one page is current at a time.
    """
    if not active_nav:
        return False
    return any(child.key == active_nav for child in item.children)
