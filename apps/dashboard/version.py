"""The running build's identity, shown at the foot of the sidebar.

The stamp is baked into the image when it is built rather than read from the
working tree: a runtime container carries no git metadata, and what a viewer
needs to know is which deploy they are looking at, not which commit happens to
exist somewhere else.

An absent or unreadable value is a legitimate state, not an error. A runtime
started outside an image build has no build to name, so the sidebar omits the
stamp rather than inventing one or refusing to render.
"""

import datetime as dt
import os

from django.http import HttpRequest
from django.utils import timezone

BUILD_TIME_VARIABLE = "DASHKODA_BUILD_TIME"
COMMIT_VARIABLE = "DASHKODA_COMMIT"


def _parse(raw: str) -> dt.datetime | None:
    """Read an ISO-8601 build time, treating anything unreadable as absent."""
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    # The build stamps UTC. A naive value can only have come from a hand-set
    # variable, and reading it as UTC keeps the displayed time honest rather
    # than silently shifting it by the server's offset.
    if timezone.is_naive(moment):
        return moment.replace(tzinfo=dt.UTC)
    return moment


def build_version(request: HttpRequest) -> dict[str, str]:
    """Expose the build stamp and its commit to every template."""
    raw = os.environ.get(BUILD_TIME_VARIABLE, "").strip()
    moment = _parse(raw) if raw else None
    if moment is None:
        return {"build_version": "", "build_commit": ""}

    # Ascending order so the stamp sorts and reads as a version, in the
    # project's own timezone so it agrees with every other time on the page.
    return {
        "build_version": timezone.localtime(moment).strftime("v%Y.%m.%d-%H%M"),
        "build_commit": os.environ.get(COMMIT_VARIABLE, "").strip(),
    }
