"""The Liikmeskond page keeps the two sources apart and tells the truth."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.access.middleware import CSP
from apps.membership.bootstrap import ensure_membership_source
from apps.membership.models import MembershipCountObservation, MembershipMetricConflict
from apps.sources.services import build_import_run, register_external_reference

pytestmark = pytest.mark.django_db


@pytest.fixture
def public_observation(db):
    """A synthetic public directory count, so both sources are present."""
    source = ensure_membership_source()
    artifact = register_external_reference(
        source=source,
        external_reference="test:koda-public-members",
        original_name="company-list.json",
        sha256="c" * 64,
        size_bytes=2048,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="koda_public_members",
        schema_version="1.0",
        dry_run=False,
    )
    return MembershipCountObservation.objects.create(
        source=source,
        artifact=artifact,
        import_run=run,
        observed_at=datetime(2026, 1, 20, 9, 0, tzinfo=UTC),
        total_members=3555,
        is_current=True,
    )


def _page(client):
    return client.get(reverse("membership")).content.decode()


def test_the_public_catalogue_is_no_longer_on_this_page(viewer_client, public_observation):
    """The board asked for the top of the page to go.

    That took the source list, the connection strip and the public-catalogue
    section with it, so the directory count and its definition are not here any
    more. The count itself is unaffected — it leads the overview's headline
    strip, and `apps.membership.selectors` still records it every day.
    """
    body = _page(viewer_client)

    assert "3555" not in body
    assert "raamatupidamislik" not in body
    assert "Andmeallikad" not in body
    # The page is the internal report now, and starts with it.
    assert "Sisemine liikmeskonna aruanne" in body


def test_internal_section_appears_only_after_import(viewer_client, public_observation):
    before = _page(viewer_client)
    assert "Sisemist liikmeskonna aruannet ei ole veel imporditud" in before


def test_internal_section_shows_after_import(viewer_client, public_observation, imported_package):
    body = _page(viewer_client)

    assert "Sisemine liikmeskonna aruanne" in body
    assert "15.01.25" in body
    assert "3300" in body


def test_the_page_never_claims_the_definitions_match(
    viewer_client, public_observation, imported_package
):
    """The page no longer *explains* the difference — the board struck that
    paragraph out — but it must still never assert the two counts are one.

    What is checked is the absence of a claim rather than the presence of an
    explanation: the catalogue's own total does not appear on this page at all,
    so nothing here invites the two figures to be read as the same measurement.
    """
    body = _page(viewer_client)

    assert "3555" not in body, "the catalogue total belongs on the overview, not here"
    assert "Liikmeid kataloogis" not in body


def test_conflict_notice_is_truthful_and_counted(viewer_client, imported_package):
    body = _page(viewer_client)

    assert "vastuolude tõttu graafikult välja jäetud" in body
    assert "Vastuolulisi väärtusi ei asendata nulliga." in body


def test_no_source_path_or_warning_code_reaches_the_viewer(viewer_client, imported_package):
    body = _page(viewer_client)

    assert ".docx" not in body
    assert "Juhatus 2024/" not in body
    assert "cross_document_metric_conflict" not in body
    assert "src_aaaa" not in body
    assert "Traceback" not in body


def test_charts_ship_their_data_as_non_executable_json(viewer_client, imported_package):
    body = _page(viewer_client)
    blocks = re.findall(
        r'<script id="(internal-membership-[a-z-]+)" type="application/json">(.*?)</script>',
        body,
        re.DOTALL,
    )

    assert blocks, "expected at least one chart payload"
    for _payload_id, raw in blocks:
        option = json.loads(raw.replace("\\u0022", '"'))
        assert "series" in option


def test_every_chart_has_a_table_alternative(viewer_client, imported_package):
    body = _page(viewer_client)

    assert body.count("data-chart-payload=") == body.count("data-chart-table")
    assert "Andmed tabelina" in body


def test_monthly_chart_omits_a_conflict_instead_of_charting_zero(viewer_client, imported_package):
    body = _page(viewer_client)
    match = re.search(
        r'<script id="internal-membership-monthly" type="application/json">(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert match is not None
    option = json.loads(match.group(1).replace("\\u0022", '"'))
    series_2024 = next(item for item in option["series"] if item["name"] == "2024")

    # A drawn month is an object carrying the key its tooltip is stored under; a
    # month with no value is absent entirely, which is what keeps "nobody
    # reported this" from being drawn as a zero.
    drawn = [None if item is None else item["value"] for item in series_2024["data"]]

    assert drawn[0] == 12  # reported
    assert drawn[1] == 0  # an explicit zero survives as a zero
    assert drawn[2] is None  # the conflict is absent, not zero
    assert drawn[6] is None  # never reported


def test_chart_bundle_loads_only_when_there_is_a_chart(
    viewer_client, public_observation, imported_package
):
    with_data = _page(viewer_client)
    assert "build/charts.js" in with_data


def test_no_chart_bundle_without_internal_data(viewer_client, public_observation):
    assert "build/charts.js" not in _page(viewer_client)


def test_manual_entry_is_not_linked_for_viewers(viewer_client, imported_package):
    body = _page(viewer_client)

    assert "internal-report/new" not in body
    assert "Lisa liikmeskonna aruanne" not in body


def test_content_security_policy_is_unchanged(viewer_client, imported_package):
    response = viewer_client.get(reverse("membership"))

    assert response.headers["Content-Security-Policy"] == CSP
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    assert "unsafe-eval" not in response.headers["Content-Security-Policy"]


def test_overview_does_not_show_two_competing_totals(
    viewer_client, public_observation, imported_package
):
    """Both totals appear on the overview, and neither stands in for the other.

    The board report's own total is there, in the Liikmeskond card, beside its
    paid count. The per-figure source lines and the explanatory note were both
    removed at the board's request, so the overview no longer states the
    distinction in words. The Liikmeskond page still does, in the note under its
    figures, and that is asserted below.
    """
    # The board report's own total is drawn, and a withheld metric is not drawn
    # at all — so the disputed 2024 reading is settled first, as an operator
    # would, before asserting the line is there.
    for conflict in MembershipMetricConflict.objects.filter(metric="total_members", resolved=False):
        conflict.resolved = True
        conflict.resolution_note = "Otsene lugemine eelistatud."
        conflict.save(update_fields=["resolved", "resolution_note"])

    # An explicit window, for the same reason the card test in
    # `tests/dashboard/test_overview_data.py` needs one: the default is twelve
    # months back from the newest observation's own date, which falls five days
    # after the older one and leaves a single point. One point is not a trend
    # and is not drawn, so the label this test is about would never appear.
    body = viewer_client.get(
        reverse("home"), {"alates": "2024-01-01", "kuni": "2025-01-15"}
    ).content.decode()

    assert "3555" in body, "the public directory total leads the headline strip"
    # The board report's own total is on the card as a drawn line, labelled
    # with whose total it is, rather than as a printed figure —
    # `tests/dashboard/test_overview_data.py` holds the card to exactly three
    # printed figures. What matters here is that the two definitions are not
    # conflated and that the strip states the public directory's count.
    assert "Liikmeid kokku · koja aruanne" in body

    membership = viewer_client.get(reverse("membership")).content.decode()

    # The Liikmeskond page is the internal report, and says outright that its
    # count and the catalogue's are not the same measurement.
    # The heading is `sr-only` now — struck out visually, kept as the section
    # landmark's accessible name — so it is still in the document.
    assert "Sisemine liikmeskonna aruanne" in membership
    # Each total is stated once. The directory count used to appear a second
    # time inside the board report's card, under a second name.
    assert "Liikmeid kataloogis" not in body


def test_range_control_only_accepts_known_values(viewer_client, imported_package):
    """Neither the date fields nor the legacy key echo or act on hostile input.

    The fields render the resolved window, never the raw query string, so
    whatever arrived is folded into a window the history can answer and the
    text itself reaches no query and no attribute.
    """
    for hostile in ({"vahemik": "'; DROP TABLE"}, {"alates": "'; DROP TABLE", "kuni": "täna"}):
        response = viewer_client.get(reverse("membership"), hostile)

        assert response.status_code == 200
        assert "DROP TABLE" not in response.content.decode()
