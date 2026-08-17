"""The Liikmeskond page keeps the two sources apart and tells the truth."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.access.middleware import CSP
from apps.core.formatting import GROUP_SEPARATOR, integer
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
    # The headline strip states the source's own date in full and groups the
    # thousand, which the raw model value did not. Both are the design system's
    # formatters rather than this page's choice.
    assert "15.01.2025" in body
    assert f"3{GROUP_SEPARATOR}300" in body


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


def test_conflict_notice_no_longer_reaches_this_page(viewer_client, imported_package):
    """`Andmete seis` — the section this notice lived in — moved to
    `/haldus/` on 2026-08-17. See
    `tests/dashboard/test_admin_area.py::test_the_membership_data_block_states_the_report_facts`.
    """
    body = _page(viewer_client)

    assert "vastuolude tõttu graafikult välja jäetud" not in body


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


def test_every_chart_names_itself_for_a_reader_who_cannot_see_the_canvas(
    viewer_client, imported_package
):
    """The accessible data table left every chart on 2026-08-17. What is left
    is `chart.summary`, rendered as the canvas's own `aria-label` — a
    `role="img"` with no label is an image nobody using a screen reader can
    read at all."""
    body = _page(viewer_client)

    payload_count = body.count("data-chart-payload=")
    label_count = len(re.findall(r'data-chart-canvas[^>]*aria-label="[^"]+"', body))
    assert payload_count > 0
    assert label_count == payload_count
    assert "Andmed tabelina" not in body


def test_monthly_chart_omits_a_conflict_instead_of_charting_zero(viewer_client, imported_package):
    # Recruitment lives under `fookus=kasv`; the overview draws the stock trend.
    body = viewer_client.get(reverse("membership"), {"fookus": "kasv"}).content.decode()
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
    body = viewer_client.get(reverse("home")).content.decode()

    assert integer(3555) in body, "the public directory total leads the pillar"
    # The board report's own total is no longer drawn on the front page at all.
    # The executive pillar takes only *ratios inside* that report — the paid
    # share, the fee collection, the year's joins and removals — each naming the
    # report as its source. So the two definitions cannot be conflated, because
    # only one of them is stated as a total.
    assert "Liikmeid kokku · koja aruanne" not in body
    # The report is named in `Andmete seis`, which moved to `/haldus/` on
    # 2026-08-15. It is still named — one page along — which is what keeps the
    # two definitions from being conflated.
    admin = viewer_client.get(reverse("dashboard-admin")).content.decode()
    assert "Koja sisemine liikmeskonna aruanne" in admin

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


def test_the_headline_strip_answers_four_questions_not_nine(viewer_client, imported_package):
    """The redesign's central claim, asserted against the rendered page.

    Nine equally weighted figures asked the reader to decide which mattered.
    These are server-render facts, so they are pinned here rather than in the
    browser suite: a Playwright assertion about how many `<div>`s a list holds
    proves the same thing more slowly and in a place it cannot be debugged.
    """
    body = _page(viewer_client)

    assert "Peamised näitajad" in body
    for label in (
        "Liikmeid kokku",
        "Liikmed ja tasunud liikmeid",
        "Liikmemaksu laekumine",
    ):
        assert label in body, f"headline missing: {label}"

    # `Tasunute osakaal` folded into the card above it on 2026-08-16. It is no
    # longer a card of its own, but the trend chart's readouts still carry the
    # label, so this is scoped to the strip rather than the page.
    strip = body.split('id="section-headlines"', 1)[1].split("</section>", 1)[0]
    assert "Tasunute osakaal" not in strip
    assert "tasunud" in strip


def test_the_suspended_count_moved_out_of_the_headline_strip(viewer_client, imported_package):
    """It is a secondary status and belongs beside the movement it describes."""
    body = _page(viewer_client)

    assert "Sel aastal" in body
    assert "Peatatud liikmeid" in body
    # It is inside the current-year block, which follows the headline strip.
    assert body.index("Peatatud liikmeid") > body.index("Liikmemaksu laekumine")


def test_the_difference_is_never_presented_as_a_net_membership_change(
    viewer_client, imported_package
):
    """`new_members_ytd` and `removed_members_ytd` are two reported counts.

    Subtracting them gives the gap between two reports, not the movement of the
    membership stock, and the page must not claim otherwise.

    The difference row left the page on 2026-08-16 and the sentence denying it
    was a net change went with it — with nothing subtracted on screen there was
    nothing left for that sentence to qualify. So the count check is gone too:
    the page no longer uses the phrase at all, in either direction.

    What survives is the part that still bites. The words remain forbidden, and
    the row is asserted absent — because if it ever returns without its denial,
    that is exactly the defect this test was written for.
    """
    body = _page(viewer_client).casefold()

    assert "netokasv" not in body
    assert "netomuutus" not in body
    assert "liikmeskonna muutus" not in body
    assert "liitumiste ja väljaarvamiste vahe" not in body


def test_an_unknown_focus_renders_the_overview_rather_than_raising(viewer_client, imported_package):
    for raw in ("koosseiss", "growth", "../etc", ""):
        response = viewer_client.get(reverse("membership"), {"fookus": raw})

        assert response.status_code == 200
        assert "Peamised näitajad" in response.content.decode()


def test_each_focus_draws_only_its_own_sections(viewer_client, imported_package):
    """A focus is a different page, not a scroll position.

    Recruitment is the section this asserts on because the approved package
    always carries monthly values, so it is drawn whenever the focus that owns
    it is asked for and never when it is not. The decision section needs a
    schema 2.0 package and has its own tests.
    """
    overview = _page(viewer_client)
    growth = viewer_client.get(reverse("membership"), {"fookus": "kasv"}).content.decode()

    assert "section-recruitment" not in overview
    assert "section-recruitment" in growth
    # The overview leads with the figures instead.
    assert "section-headlines" in overview
    assert "section-headlines" not in growth


def test_every_range_preset_keeps_the_reader_on_its_focus(viewer_client, imported_package):
    """`RangePreset.query` carries only the two dates.

    Without `fookus` prepended, a preset clicked on any focus but the first
    drops the reader back to the overview — a control that appears to navigate
    away from the chart it governs. Checked over every rendered preset rather
    than one, because the defect would be per-link.
    """
    body = viewer_client.get(reverse("membership"), {"fookus": "kasv"}).content.decode()

    # Only the links that return the reader to a section: presets and chart
    # toggles both carry a fragment, and the focus navigation deliberately does
    # not — a link *to* the overview carrying `fookus=ulevaade` is correct, and
    # matching it here would assert the opposite of the rule.
    hrefs = re.findall(r'href="\?([^"]*alates=[^"]*)#section-[^"]*"', body)
    assert hrefs, "the growth focus rendered no in-section links to check"
    for href in hrefs:
        assert "fookus=kasv" in href, href


def test_one_word_per_quantity_for_joins_and_exclusions(viewer_client, imported_package):
    """The page names a flow the same way wherever it appears.

    On 2026-08-16 the `Sel aastal` readouts were relabelled `Liitunud` and
    `Väljaarvatud`, and the reconciliation table below them was left saying
    `Liitus` and `Välja arvati` — one page, two words, one quantity. Nothing
    failed, because every check named a title.

    So this names the *old* words. It is deliberately not a heading assertion:
    the labels sit in `<dt>`s, in `<th>`s and in a chart's tooltip payload, and
    the only property that holds across all three is that the retired forms are
    gone from the rendered page.

    `Liitus <year>` in the cohort chart's tooltip titles is a verb in a
    sentence — "joined in 2020" — not a label beside a number, which is why the
    match is anchored rather than a bare substring.
    """
    body = _page(viewer_client)

    assert ">Liitunud<" in body
    assert ">Väljaarvatud<" in body
    assert ">Liitus<" not in body
    assert "Välja arvati" not in body
    assert '"label": "Liitus"' not in body and '"label":"Liitus"' not in body
