"""The build stamp shown at the foot of the sidebar.

The stamp is cosmetic, so every failure mode here is "show nothing". These
assert that an unbuilt or mis-stamped runtime stays renderable rather than
raising on a page every viewer sees.
"""

import pytest
from django.template.loader import render_to_string

from apps.dashboard.version import (
    BUILD_TIME_VARIABLE,
    COMMIT_VARIABLE,
    build_version,
)


@pytest.fixture
def stamped(monkeypatch):
    def apply(build_time: str, commit: str = "") -> dict[str, str]:
        monkeypatch.setenv(BUILD_TIME_VARIABLE, build_time)
        monkeypatch.setenv(COMMIT_VARIABLE, commit)
        return build_version(request=None)

    return apply


def test_the_stamp_is_the_build_time_in_ascending_order(stamped):
    assert stamped("2026-08-04T09:14:00Z")["build_version"] == "v2026.08.04-1214"


def test_the_stamp_is_shown_in_the_project_timezone(stamped):
    # 23:30 UTC is already the next day in Tallinn, and the displayed date has
    # to agree with every other date on the page.
    assert stamped("2026-08-04T23:30:00Z")["build_version"] == "v2026.08.05-0230"


def test_a_naive_build_time_is_read_as_utc(stamped):
    assert stamped("2026-08-04T09:14:00")["build_version"] == "v2026.08.04-1214"


def test_the_commit_travels_with_the_stamp(stamped):
    assert stamped("2026-08-04T09:14:00Z", "f4280ec")["build_commit"] == "f4280ec"


@pytest.mark.parametrize("raw", ["", "   ", "not-a-time", "2026-13-45T99:99:99Z"])
def test_an_unusable_build_time_yields_no_stamp(stamped, raw):
    assert stamped(raw)["build_version"] == ""


def test_an_unbuilt_runtime_yields_no_stamp(monkeypatch):
    monkeypatch.delenv(BUILD_TIME_VARIABLE, raising=False)
    monkeypatch.delenv(COMMIT_VARIABLE, raising=False)

    assert build_version(request=None) == {"build_version": "", "build_commit": ""}


def test_the_sidebar_renders_the_stamp_below_the_sign_out_button():
    html = render_to_string(
        "dashboard/partials/sidebar.html",
        {"navigation": [], "build_version": "v2026.08.04-1214", "build_commit": "f4280ec"},
    )

    assert "dk-version" in html
    assert "v2026.08.04-1214" in html
    assert html.index("Logi välja") < html.index("v2026.08.04-1214")


def test_the_sidebar_omits_the_stamp_when_there_is_none():
    html = render_to_string(
        "dashboard/partials/sidebar.html",
        {"navigation": [], "build_version": "", "build_commit": ""},
    )

    assert "dk-version" not in html
    assert "Logi välja" in html
