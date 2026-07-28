"""Component contracts.

These render the shared partials with clearly synthetic values so the component
API is covered. The real dashboard page never renders them; `test_overview`
asserts that.
"""

from django.template.loader import render_to_string

from apps.dashboard.navigation import NavItem


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
            "source": "Sünteetiline testallikas",
            "as_of": "31.12.2025",
            "freshness": "success",
            "freshness_label": "Värske",
        },
    )

    assert "1 234" in html
    assert "liiget" in html
    assert "↑" in html
    assert "vs eelmine kvartal" in html
    assert "Kontrollitud" in html
    assert "Sünteetiline testallikas" in html
    assert "31.12.2025" in html
    assert "Värske" in html


def test_kpi_card_without_a_value_states_that_data_is_missing():
    html = render("kpi_card", {"label": "Näidisnäitaja"})

    assert "Kontrollitud andmed puuduvad." in html
    assert "ühendamata" in html
    assert "—" in html


def test_kpi_card_marks_a_falling_value_in_text_as_well_as_colour():
    html = render(
        "kpi_card",
        {"label": "Näidisnäitaja", "value": "10", "change": "3", "change_direction": "down"},
    )

    assert "↓" in html


def test_freshness_row_falls_back_to_an_explicit_unknown_state():
    html = render("freshness_row", {})

    assert "ühendamata" in html
    assert "—" in html


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
