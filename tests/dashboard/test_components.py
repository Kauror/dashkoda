"""Component contracts.

These render the shared partials with clearly synthetic values so the component
API is covered. The real dashboard page never renders them; `test_overview`
asserts that.
"""

from django.template.loader import render_to_string

from apps.dashboard.connections import Connection, ConnectionState, planned
from apps.dashboard.navigation import NavItem
from apps.dashboard.overview import SourcedFigure
from apps.dashboard.sparkline import build_sparkline


def render(name: str, context: dict) -> str:
    return render_to_string(f"dashboard/components/{name}.html", context)


def test_status_badge_states_the_meaning_in_text():
    html = render("status_badge", {"label": "Kontrollitud", "variant": "success"})

    assert "Kontrollitud" in html
    assert "dk-badge-success" in html


def test_status_badge_defaults_to_the_neutral_variant():
    assert "dk-badge-neutral" in render("status_badge", {"label": "Lisamisel"})


def test_section_header_links_its_heading_to_the_section():
    html = render(
        "section_header",
        {
            "title": "Näidisplokk",
            "heading_id": "section-demo",
            "description": "Selgitav rida.",
            "badge_label": "Testandmed",
            "badge_variant": "warning",
        },
    )

    assert 'id="section-demo"' in html
    assert "Näidisplokk" in html
    assert "Selgitav rida." in html
    assert "Testandmed" in html


def test_kpi_card_supports_the_full_future_api():
    html = render(
        "kpi_card",
        {
            "label": "Näidisnäitaja",
            "value": "1 234",
            "unit": "liiget",
            "change": "12",
            "change_direction": "up",
            "comparison_period": "vs eelmine kvartal",
            "status": "success",
            "status_label": "Kontrollitud",
            "as_of": "31.12.2025",
            "secondary": "111 € / 222 €",
            "meter_pct": 42.5,
        },
    )

    assert "1 234" in html
    assert "liiget" in html
    assert "↑" in html
    assert "vs eelmine kvartal" in html
    assert "Kontrollitud" in html
    assert "Seisuga" in html
    assert "31.12.2025" in html
    assert "111 € / 222 €" in html
    # The proportion is geometry, never a width inside a style attribute: the
    # Content Security Policy is style-src 'self'.
    assert 'width="42.50"' in html
    assert 'style="' not in html


def test_kpi_card_shows_a_reported_zero_rather_than_the_empty_state():
    """Zero is a measurement. Only an absent value is an empty state."""
    html = render("kpi_card", {"label": "Näidisnäitaja", "value": 0})

    assert "Kontrollitud andmed puuduvad." not in html
    assert ">0<" in html.replace(" ", "").replace("\n", "")


def test_kpi_card_omits_the_meter_when_there_is_no_proportion():
    html = render("kpi_card", {"label": "Näidisnäitaja", "value": "5"})

    assert "<svg" not in html


def test_kpi_card_without_a_value_states_that_data_is_missing():
    html = render("kpi_card", {"label": "Näidisnäitaja"})

    assert "Kontrollitud andmed puuduvad." in html
    assert "—" in html


def test_kpi_card_renders_a_secondary_line_beside_a_value():
    html = render("kpi_card", {"label": "Näidisnäitaja", "value": "5", "secondary": "Tugiliin"})

    assert "Tugiliin" in html


def test_kpi_card_ignores_a_secondary_line_on_a_details_only_cell():
    """A supporting line needs a figure to support.

    A details-only cell has no hero value, so `secondary` cannot be drawn. The
    overview passed one here for two milestones and it never reached a page —
    silently, because a string that renders nowhere looks exactly like a string
    that renders. The contract is pinned so the next caller puts it in `details`.
    """
    html = render(
        "kpi_card",
        {
            "label": "Näidisnäitaja",
            "details": [{"label": "esimene", "value": 1}],
            "secondary": "Nähtamatu tugiliin",
        },
    )

    assert "esimene" in html
    assert "Nähtamatu tugiliin" not in html


def test_kpi_card_links_a_detail_row_that_has_somewhere_to_go():
    """The label carries the link; the count stays beside it.

    A `<dl>` may group a `<dt>` and its `<dd>` in a `<div>` and in nothing else,
    so there is no valid element to make the whole row one link.
    """
    html = render(
        "kpi_card",
        {
            "label": "Näidisnäitaja",
            "details": [
                {"label": "teemasid töös", "value": 17, "url": "/oigusloome/#section-open"},
                {"label": "ilma sihtkohata", "value": 4},
            ],
        },
    )

    # `dk-link-quiet`, not `dk-link`: these are labels under a figure, and the
    # cue is a dotted rule rather than colour, so the link survives a reader who
    # never hovers and one who cannot separate the hues.
    assert '<a href="/oigusloome/#section-open" class="dk-link-quiet">teemasid töös</a>' in html
    # A count with no section listing exactly those rows stays plain text rather
    # than linking somewhere approximate.
    assert html.count("<a ") == 1
    assert "ilma sihtkohata" in html
    assert "17" in html
    assert "4" in html


def test_legal_topic_is_plain_text_while_no_record_carries_an_address():
    """The state today, pinned so it is a decision and not a silent gap.

    `Tööd eelnõudega.xlsx` has no address column and is read-only to this
    application, so no legal record has a public URL. The row renders as text
    rather than as a link to nowhere.
    """
    html = render("legal_topic", {"item": {"topic": "Reisijate pakett"}})

    assert "Reisijate pakett" in html
    assert "<a " not in html


def test_legal_topic_links_the_moment_a_record_carries_an_address():
    """The other half of the contract, so the day a source supplies an address
    the card needs no change and this test is what says so."""
    html = render(
        "legal_topic",
        {
            "item": {
                "topic": "Reisijate pakett",
                "public_url": "https://www.koda.ee/et/arvamus",
            }
        },
    )

    assert 'href="https://www.koda.ee/et/arvamus"' in html
    assert 'rel="noopener noreferrer"' in html
    # The destination is announced to a screen reader, not only implied by
    # colour, and `relative` anchors that note inside the truncation.
    assert "avaneb uuel lehel" in html
    assert "relative" in html


def test_kpi_card_marks_a_falling_value_in_text_as_well_as_colour():
    html = render(
        "kpi_card",
        {"label": "Näidisnäitaja", "value": "10", "change": "3", "change_direction": "down"},
    )

    assert "↓" in html


def test_freshness_row_shows_the_as_of_date_and_nothing_else():
    """The row was trimmed to the date the board asked to keep.

    The source name, the update cadence and the connection badge that used to
    sit beside it are gone; an absent date still falls back to an em dash rather
    than to a blank."""
    html = render("freshness_row", {"as_of": "31.12.2025"})

    assert "Seisuga" in html
    assert "31.12.2025" in html
    assert "Allikas" not in html
    assert "dk-badge" not in html
    assert "—" in render("freshness_row", {})


def test_empty_state_shows_the_reason():
    html = render(
        "empty_state",
        {"message": "Andmeallikas ei ole veel ühendatud.", "detail": "Lisatakse hiljem."},
    )

    assert "Andmeallikas ei ole veel ühendatud." in html
    assert "Lisatakse hiljem." in html


def test_error_state_is_announced_and_carries_no_technical_detail():
    html = render(
        "error_state",
        {"title": "Vaadet ei õnnestunud laadida.", "message": "Proovi lehte värskendada."},
    )

    assert 'role="alert"' in html
    assert "Proovi lehte värskendada." in html


def test_list_row_without_a_url_is_not_a_link():
    html = render("list_row", {"title": "Näidisrida", "meta": "Testandmed"})

    assert "Näidisrida" in html
    assert "<a " not in html


def test_list_row_with_a_url_is_a_link():
    html = render("list_row", {"title": "Näidisrida", "url": "/"})

    assert '<a href="/"' in html


def test_table_wrapper_renders_synthetic_rows_inside_a_scroll_container():
    html = render(
        "table_wrapper",
        {
            "caption": "Näidistabel",
            "columns": ["Periood", "Väärtus"],
            "rows": [["2025 K1", "111"], ["2025 K2", "222"]],
        },
    )

    assert "overflow-x-auto" in html
    assert "<caption" in html
    assert 'scope="col"' in html
    assert "2025 K2" in html


def test_table_wrapper_without_rows_shows_the_empty_state():
    html = render(
        "table_wrapper",
        {"caption": "Näidistabel", "columns": ["Periood"], "empty_message": "Andmeid ei ole."},
    )

    assert "Andmeid ei ole." in html
    assert "<table" not in html


def test_skeleton_announces_that_content_is_loading():
    html = render("skeleton", {"label": "Näidisandmeid laaditakse."})

    assert 'aria-busy="true"' in html
    assert "Näidisandmeid laaditakse." in html
    assert 'aria-hidden="true"' in html


def test_callout_uses_the_requested_variant():
    html = render(
        "callout",
        {"title": "Näidisteade", "message": "Selgitus.", "variant": "warning"},
    )

    assert "dk-callout-warning" in html
    assert "Näidisteade" in html


def test_nav_item_renders_a_planned_module_as_inert_text():
    html = render(
        "nav_item",
        {"item": NavItem(key="membership", label="Liikmeskond"), "active_nav": "overview"},
    )

    assert "<a " not in html
    assert 'aria-disabled="true"' in html
    assert "Lisamisel" in html


def test_nav_item_marks_the_active_module():
    html = render(
        "nav_item",
        {
            "item": NavItem(key="overview", label="Ülevaade", url_name="home"),
            "active_nav": "overview",
        },
    )

    assert 'aria-current="page"' in html
    assert "dk-nav-item-active" in html


def test_nav_item_nests_children_and_keeps_unrouted_ones_inert():
    html = render(
        "nav_item",
        {
            "item": NavItem(
                key="projects",
                label="Projektid",
                children=(NavItem(key="projects-active", label="Käimasolevad"),),
            ),
            "active_nav": "overview",
        },
    )

    assert "dk-nav-sublist" in html
    assert "Käimasolevad" in html
    assert "<a " not in html, "an unrouted child must never become a link"
    assert html.count('aria-disabled="true"') == 2


def test_planned_module_names_the_source_as_unconnected():
    html = render(
        "planned_module",
        {"connection": planned("Näidiskanal", promise="Sünteetiline allikakirjeldus.")},
    )

    assert "Näidiskanal" in html
    assert "Lisamisel" in html
    assert "Andmeallikas ei ole veel ühendatud." in html
    assert "Sünteetiline allikakirjeldus." in html


def test_sparkline_figure_draws_a_line_and_keeps_the_table_beside_it():
    from datetime import date

    series = ((date(2025, 1, 1), 10), (date(2025, 2, 1), 12), (date(2025, 3, 1), 11))
    html = render(
        "sparkline_figure",
        {
            "figure": SourcedFigure(
                label="Näidisnäitaja",
                connection=Connection(
                    "Sünteetiline testallikas", ConnectionState.CONNECTED, "iga päev"
                ),
                value=11,
                unit="ühikut",
                sparkline=build_sparkline(series),
                series=series,
            )
        },
    )

    assert "<polyline" in html
    assert 'style="' not in html
    # The table is not a fallback; it stays in the document beside the drawing.
    assert "<table" in html
    assert "1.02.25" in html
    assert "Sünteetiline testallikas · iga päev" in html


def test_trend_chart_draws_both_series_and_tells_them_apart_without_colour():
    from datetime import date

    from apps.dashboard.sparkline import TrendSource, build_trend_chart

    chart = build_trend_chart(
        (
            TrendSource(
                label="Liikmeid kokku",
                style="solid",
                source="Sünteetiline kataloog · iga päev",
                series=((date(2025, 11, 1), 3300), (date(2026, 1, 1), 3412)),
            ),
            TrendSource(
                label="Tasunud liikmeid",
                style="dashed",
                source="Sünteetiline aruanne · kord kuus",
                series=((date(2025, 11, 1), 2600), (date(2026, 1, 1), 2798)),
            ),
        )
    )

    html = render("trend_chart", {"chart": chart})

    assert html.count("<polyline") == 2
    # Pattern as well as colour, so the lines survive greyscale and a reader who
    # cannot separate the two hues.
    assert "stroke-brand" in html
    assert "stroke-success" in html
    assert 'stroke-dasharray="4 3"' in html
    # Geometry and dashes are attributes: the CSP forbids an inline style.
    assert 'style="' not in html
    # Each line is named, and so is where it came from.
    assert "Liikmeid kokku" in html
    assert "Tasunud liikmeid" in html
    assert "Sünteetiline kataloog · iga päev" in html
    assert "Sünteetiline aruanne · kord kuus" in html
    # The axis names its months and states the year where it turns.
    assert "nov" in html
    assert "2026" in html
    assert "viimased 3 kuud · nov 2025 – jaan 2026" in html
    # The table is not a fallback; one per line, never merged onto one date
    # column, because two sources report on their own days.
    assert html.count("<table") == 2
    assert "3412" in html
    assert "2798" in html


def test_trend_chart_makes_every_observation_hoverable_without_a_script():
    from datetime import date

    from apps.dashboard.sparkline import TrendSource, build_trend_chart

    chart = build_trend_chart(
        (
            TrendSource(
                label="Liikmeid kokku",
                style="solid",
                source="Sünteetiline aruanne · kord kuus",
                series=((date(2025, 11, 1), 3300), (date(2026, 1, 1), 3412)),
            ),
            TrendSource(
                label="Tasunud liikmeid",
                style="dashed",
                source="Sünteetiline aruanne · kord kuus",
                series=((date(2025, 11, 1), 2600), (date(2026, 1, 1), 2798)),
            ),
        )
    )

    html = render("trend_chart", {"chart": chart})

    # One dot per observation per line, drawn as a zero-length path with a round
    # cap: a <circle> would be squashed into an ellipse by the stretched viewBox.
    assert html.count('l0,0"') == 4
    assert html.count('vector-effect="non-scaling-stroke"') == 6
    # One hit strip per date, each reading out the whole observation.
    assert html.count("<rect") == 2
    assert "<title>1.11.25 · Liikmeid kokku 3300 · Tasunud liikmeid 2600</title>" in html
    assert "<title>1.01.26 · Liikmeid kokku 3412 · Tasunud liikmeid 2798</title>" in html
    # The strip is what the pointer meets; everything drawn over it steps aside.
    assert html.count('pointer-events="none"') == 6
    assert html.count('pointer-events="all"') == 2
    # No script, no inline style: the tooltip is the browser's own, from <title>.
    assert "<script" not in html
    assert 'style="' not in html
    # Coordinates are written by `stringformat`, which is not localised. Django's
    # `floatformat` renders in Estonian, and `12,34` in a `d` attribute is not a
    # coordinate — it is two of them.
    assert '<path d="M0.00,' in html
    assert '<rect x="0.00"' in html


def test_sparkline_figure_draws_nothing_when_one_point_cannot_show_a_trend():
    from datetime import date

    html = render(
        "sparkline_figure",
        {
            "figure": SourcedFigure(
                label="Näidisnäitaja",
                connection=Connection("Sünteetiline testallikas", ConnectionState.CONNECTED),
                value=10,
                sparkline=build_sparkline(((date(2025, 1, 1), 10),)),
                series=((date(2025, 1, 1), 10),),
            )
        },
    )

    assert "<polyline" not in html
    assert "vähemalt kahte vaatlust" in html
