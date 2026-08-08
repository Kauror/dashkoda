"""The Õigusloome page's section anchors, named once.

The overview's headline counts link to the section that lists the records behind
them, which means an id chosen in `legal_work/overview.html` is depended on from
another app. Naming them here gives that dependency somewhere to be read, and
`tests/legal_work/test_views.py` asserts every one of them is actually rendered
on the page — the template still writes its own `heading_id`, so the test is
what keeps the two from drifting apart, not the constant.

An anchor is added here only when the section genuinely lists the rows a count
describes. A link that lands on a different set of rows than the number claims
is worse than no link, so a count with no matching section stays plain text.
"""

from __future__ import annotations

# Hetkel töös — every open topic.
SECTION_OPEN = "section-open"
# Viimati välja läinud — the opinions that have gone out.
SECTION_SENT = "section-sent"

# Every anchor the overview links to, for the test that holds the page to them.
LINKED_SECTIONS: tuple[str, ...] = (SECTION_OPEN, SECTION_SENT)


def anchor(page_url: str, section_id: str) -> str:
    """One section of the Õigusloome page as a link a card can render."""
    return f"{page_url}#{section_id}"
