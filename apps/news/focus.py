"""What is left of the three-view navigation this page once had.

`Ülevaade`, `Uudiste mõju` and `Arhiiv` were three real `GET` links between
2026-08-16 and 2026-08-17, then merged onto one screen. `apps/news/page.py`
stopped parsing `fookus=` entirely on 2026-08-18, along with the reading
window it used to sit beside — see that module's docstring. A stray
`?fookus=moju` or `?fookus=arhiiv` still opens the page; Django simply never
reads the parameter, the same as any key this page does not understand.

One value is still read, ahead of that: `fookus=uudiskirjad`, the retired
newsletter focus. `apps/news/views.py` recognises it and redirects to
`Otsepostitused`, because that saved link deserves to land on the page that
actually answers it rather than silently opening the news overview instead.
That is the only reason this module still exists.
"""

from __future__ import annotations

#: The query parameter `views.py` still checks, for the one retired value below.
PARAM_FOCUS = "fookus"

#: The retired newsletter focus. Not resolved to anything on this page — it is
#: a redirect target, checked in `views.py` before any other parameter parsing.
LEGACY_FOCUS_NEWSLETTERS = "uudiskirjad"

__all__ = [
    "LEGACY_FOCUS_NEWSLETTERS",
    "PARAM_FOCUS",
]
