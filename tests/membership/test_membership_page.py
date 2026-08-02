"""The Liikmeskond page keeps the two sources apart and tells the truth."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.access.middleware import CSP
from apps.membership.bootstrap import ensure_membership_source
from apps.membership.models import MembershipCountObservation
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


def test_public_definition_survives_the_new_section(viewer_client, public_observation):
    body = _page(viewer_client)

    assert "Avalik liikmekataloog" in body
    assert "3555" in body
    assert "raamatupidamislik" in body
    assert "CRM-i lepingutel põhinev liikmearv" in body


def test_internal_section_appears_only_after_import(viewer_client, public_observation):
    before = _page(viewer_client)
    assert "Sisemist liikmeskonna aruannet ei ole veel imporditud" in before


def test_internal_section_shows_after_import(viewer_client, public_observation, imported_package):
    body = _page(viewer_client)

    assert "Sisemine liikmeskonna aruanne" in body
    assert "15.01.2025" in body
    # The internal total and the public total both appear, each under its own
    # heading, and neither is presented as the other.
    assert "3300" in body
    assert "3555" in body


def test_the_page_never_claims_the_definitions_match(
    viewer_client, public_observation, imported_package
):
    body = _page(viewer_client)

    assert "ei ole sama näitaja" in body
    assert "Erinevus on ootuspärane, mitte viga." in body


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

    assert series_2024["data"][0] == 12  # reported
    assert series_2024["data"][1] == 0  # an explicit zero survives as a zero
    assert series_2024["data"][2] is None  # the conflict is absent, not zero
    assert series_2024["data"][6] is None  # never reported


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
    """Both totals appear, each named and dated, and neither is the other.

    The board report's own total is on the overview: it is the solid line of the
    Liikmeskond chart, and the paid share stated beside it is the gap between
    the two lines. What must never happen is either total appearing without the
    source and cadence that say which count it is — that is what would let a
    reader set one against the other.
    """
    body = viewer_client.get(reverse("home")).content.decode()

    assert "3555" in body, "the public directory total leads the headline strip"
    assert "3300" in body, "the board report's own total is charted in its card"
    assert "Koda.ee liikmekataloog" in body
    assert "Sisemine liikmeskonna aruanne" in body
    # Each figure states how current it is, because one is recounted daily and
    # the other is reported once a month.
    assert "iga päev" in body
    assert "kord kuus" in body
    assert "Neid ei liideta ega esitata ühe näitajana." in body
    # Each total is stated once. The directory count used to appear a second
    # time inside the board report's card, under a second name.
    assert "Liikmeid kataloogis" not in body


def test_range_control_only_accepts_known_values(viewer_client, imported_package):
    response = viewer_client.get(reverse("membership"), {"vahemik": "'; DROP TABLE"})

    assert response.status_code == 200
    assert "DROP TABLE" not in response.content.decode()
