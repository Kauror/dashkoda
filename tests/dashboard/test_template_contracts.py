"""A structural rule the templates must obey, checked against their source.

This is a guard for a defect that no rendered-page test can see. The test
database is empty in CI, so no title is ever long enough to truncate, and the
bug below is invisible until real content arrives. Scanning the markup finds it
regardless of data.

Only one rule lives here, deliberately. Anything that *can* be asserted against
a rendered page belongs in a page test instead, where it sees the real output
rather than the source — a source scan cannot tell markup from a `{% comment %}`
describing markup, and a check that cannot distinguish the two is a false alarm
waiting to happen.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Only this project's own templates. The virtualenv and the uv caches carry
#: third-party HTML that this repository neither owns nor can fix.
TEMPLATE_ROOTS = (REPO_ROOT / "apps", REPO_ROOT / "templates")

ANCHOR = re.compile(r"<a\b[^>]*>.*?</a>", re.S)


def _templates() -> list[pathlib.Path]:
    return sorted(path for root in TEMPLATE_ROOTS for path in root.rglob("*.html"))


def test_the_template_scan_actually_finds_templates():
    """Guards the guard: a mistyped root would make every rule below vacuous."""
    found = _templates()

    assert len(found) > 10, found
    assert any(path.name == "overview.html" for path in found)


def test_a_visually_hidden_note_never_escapes_a_truncating_link():
    """An `sr-only` span inside a `truncate` link needs a positioned anchor.

    `sr-only` is `position: absolute`, and an absolutely positioned box is only
    clipped by an ancestor's `overflow: hidden` when that ancestor is its
    containing block. Inside an unpositioned `truncate` link the note therefore
    escapes the clip, settles at the *untruncated* text width, and extends the
    page's horizontal scroll area.

    This shipped once. The overview's news and events lists scrolled sideways by
    152 px in production while every browser test passed, because CI runs against
    an empty database and nothing was long enough to truncate. The fix is
    `relative` on the anchor.
    """
    offenders = []
    for path in _templates():
        markup = path.read_text(encoding="utf-8")
        for match in ANCHOR.finditer(markup):
            anchor = match.group(0)
            opening = anchor[: anchor.index(">") + 1]
            if "truncate" not in opening or "sr-only" not in anchor:
                continue
            if "relative" not in opening:
                line = markup[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}")

    assert offenders == [], (
        "a truncating link containing an sr-only note must also be `relative`, "
        f"or the note escapes the clip and widens the whole page: {offenders}"
    )
