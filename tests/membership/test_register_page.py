"""The members list, its controls and the two-source comparison.

The comparison is the part with a rule worth pinning: it produces two labelled,
dated sets and never a reconciled membership total. Several assertions below
exist only to keep that true.

Every company here is invented.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.membership.bootstrap import (
    ensure_member_directory_source,
    ensure_member_register_source,
)
from apps.membership.focus import FOCUS_REGISTER
from apps.membership.models import (
    MemberDirectoryEntry,
    MemberRegisterEntry,
    MemberRegisterSnapshot,
)
from apps.membership.register_selectors import (
    compare_sources,
    get_member_list,
    status_options,
)
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    register_external_reference,
    start_import_run,
)

SNAPSHOT_DATE = dt.date(2026, 8, 13)

MEMBERS = [
    ("Kuutõrvaja Masinaehitus", "99900001", "HARJUMAA", "regular", "Koja liige"),
    ("Vesipapi Logistika", "99900002", "TARTUMAA", "suspended", "Peatatud liige"),
    ("Pilvelõhkuja Tarkvara", "99900003", "PÄRNUMAA", "supporter", "Toetaja liige"),
    ("Udusulg Kaubandus", "99900004", "HARJUMAA", "regular", "Koja liige"),
]


@pytest.fixture
def register(db):
    """One roster snapshot with four invented members."""
    source = ensure_member_register_source()
    artifact = register_external_reference(
        source=source,
        external_reference="roster:member-register:" + "a" * 64,
        sha256="a" * 64,
        size_bytes=64,
        mime_type="text/csv",
    )
    run = complete_import_run(
        start_import_run(
            build_import_run(
                artifact=artifact,
                importer_name="member_register_csv",
                schema_version="1.0",
                dry_run=False,
            )
        )
    )
    snapshot = MemberRegisterSnapshot.objects.create(
        source=source,
        import_run=run,
        snapshot_date=SNAPSHOT_DATE,
        source_sha256="a" * 64,
        source_row_count=len(MEMBERS),
        is_current=True,
    )
    MemberRegisterEntry.objects.bulk_create(
        [
            MemberRegisterEntry(
                snapshot=snapshot,
                name=name,
                legal_form="OÜ",
                member_number=str(index),
                status_key=status_key,
                status_label=status_label,
                registry_code=code,
                county=county,
                city="TALLINN",
                country="EESTI",
                employees=None if index == 2 else 10 + index,
                membership_start=dt.date(2015, 3, 1),
                nace_code="70201",
                nace_label="Äri- ja muu juhtimisalane nõustamine",
                website="www.naidis.invalid" if index != 3 else "",
            )
            for index, (name, code, county, status_key, status_label) in enumerate(MEMBERS, start=1)
        ]
    )
    return snapshot


@pytest.fixture
def directory(db):
    """The directory publishes two of the four, plus one stranger."""
    source = ensure_member_directory_source()
    now = timezone.now()
    MemberDirectoryEntry.objects.bulk_create(
        [
            MemberDirectoryEntry(
                source=source,
                registry_code=code,
                profile_path=f"/et/liige/naidis-{code}",
                first_seen_at=now,
                last_seen_at=now,
                is_published=True,
            )
            for code in ("99900001", "99900002", "99911111")
        ]
    )
    return source


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_list_is_paginated_in_the_database(register):
    page = get_member_list(snapshot=register, page=1, page_size=2)
    assert page.total == 4
    assert len(page.members) == 2
    assert page.page_count == 2
    assert page.has_next and not page.has_previous
    assert (page.first_index, page.last_index) == (1, 2)


@pytest.mark.django_db
def test_search_matches_name_code_county_and_sector(register):
    assert get_member_list(snapshot=register, query="Vesipapi").total == 1
    assert get_member_list(snapshot=register, query="99900003").total == 1
    assert get_member_list(snapshot=register, query="HARJUMAA").total == 2
    assert get_member_list(snapshot=register, query="juhtimisalane").total == 4


@pytest.mark.django_db
def test_an_unknown_status_filter_falls_back_to_all(register):
    """A stale bookmark renders the list, never an error and never an empty page."""
    assert get_member_list(snapshot=register, status="auliige").total == 4
    assert get_member_list(snapshot=register, status="suspended").total == 1


@pytest.mark.django_db
def test_status_options_offer_only_statuses_this_snapshot_holds(register):
    options = status_options(register)
    assert [key for key, _label, _count in options] == ["regular", "suspended", "supporter"]
    assert dict((key, count) for key, _label, count in options)["regular"] == 2


@pytest.mark.django_db
def test_a_member_the_directory_publishes_gets_a_profile_link(register, directory):
    page = get_member_list(snapshot=register, query="Kuutõrvaja")
    member = page.members[0]
    assert member.is_published
    assert member.profile_url == "https://www.koda.ee/et/liige/naidis-99900001"


@pytest.mark.django_db
def test_an_unlisted_member_gets_no_guessed_link(register, directory):
    page = get_member_list(snapshot=register, query="Udusulg")
    member = page.members[0]
    assert not member.is_published
    assert member.profile_url == ""


@pytest.mark.django_db
def test_a_website_without_a_scheme_becomes_a_link_without_being_rewritten(register):
    member = get_member_list(snapshot=register, query="Kuutõrvaja").members[0]
    assert member.website == "www.naidis.invalid"
    assert member.website_url == "https://www.naidis.invalid"


@pytest.mark.django_db
def test_a_member_with_no_website_gets_no_link(register):
    member = get_member_list(snapshot=register, query="Pilvelõhkuja").members[0]
    assert member.website_url == ""


@pytest.mark.django_db
def test_an_unreported_headcount_stays_none_rather_than_zero(register):
    member = get_member_list(snapshot=register, query="Vesipapi").members[0]
    assert member.employees is None


@pytest.mark.django_db
def test_no_register_means_an_empty_page_rather_than_an_error(db):
    page = get_member_list()
    assert page.total == 0
    assert page.members == ()
    assert page.page_count == 1


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_comparison_reports_three_sets_and_no_combined_total(register, directory):
    comparison = compare_sources(snapshot=register)

    assert comparison.matched == 2
    assert comparison.only_in_roster_count == 2
    assert comparison.only_in_directory_count == 1
    assert comparison.roster_total == 4
    assert comparison.directory_total == 3
    # Each source keeps its own date, and neither total is derived from the
    # other. There is deliberately no field holding a reconciled membership
    # number, because no measurement in this application produces one.
    assert comparison.roster_date == SNAPSHOT_DATE
    assert comparison.directory_checked_at is not None
    assert not hasattr(comparison, "total_members")


@pytest.mark.django_db
def test_the_comparison_names_roster_members_and_links_directory_strangers(register, directory):
    comparison = compare_sources(snapshot=register)

    only_roster = {entry.registry_code: entry for entry in comparison.only_in_roster}
    assert only_roster["99900003"].name == "Pilvelõhkuja Tarkvara"
    assert only_roster["99900003"].status_label == "Toetaja liige"

    # A code the roster does not know is shown by its link and nothing else:
    # the name would have to come from scraping a profile page, which this
    # application deliberately does not collect.
    stranger = comparison.only_in_directory[0]
    assert stranger.registry_code == "99911111"
    assert stranger.name == ""
    assert stranger.profile_url.endswith("/et/liige/naidis-99911111")


@pytest.mark.django_db
def test_an_unpublished_directory_row_is_not_counted_as_published(register, directory):
    MemberDirectoryEntry.objects.filter(registry_code="99900001").update(
        is_published=False, unpublished_at=timezone.now()
    )
    comparison = compare_sources(snapshot=register)
    assert comparison.matched == 1
    assert comparison.directory_total == 2


@pytest.mark.django_db
def test_roster_rows_without_a_code_are_reported_rather_than_compared(register, directory):
    MemberRegisterEntry.objects.create(
        snapshot=register,
        name="Koodita Näidis",
        status_key="regular",
        status_label="Koja liige",
        registry_code=None,
    )
    comparison = compare_sources(snapshot=register)
    assert comparison.roster_without_code == 1
    assert comparison.matched == 2


@pytest.mark.django_db
def test_the_comparison_is_absent_when_either_source_is(register):
    """No directory rows means no comparison, not a comparison against zero."""
    assert compare_sources(snapshot=register) is None


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_focus_is_offered_only_once_a_register_exists(viewer_client, register):
    response = viewer_client.get(reverse("membership"))
    assert response.status_code == 200
    assert f"fookus={FOCUS_REGISTER}" in response.content.decode()


@pytest.mark.django_db
def test_the_focus_is_not_offered_without_one(viewer_client, db):
    response = viewer_client.get(reverse("membership"))
    assert f"fookus={FOCUS_REGISTER}" not in response.content.decode()


@pytest.mark.django_db
def test_the_list_focus_renders_members_and_the_snapshot_date(viewer_client, register, directory):
    response = viewer_client.get(reverse("membership"), {"fookus": FOCUS_REGISTER})
    body = response.content.decode()

    assert response.status_code == 200
    assert "Kuutõrvaja Masinaehitus" in body
    assert "13.08.2026" in body
    # The page must say what the list is a reading of. A members list presented
    # without its date reads as current, and this one is a manual export.
    assert "seisuga" in body


@pytest.mark.django_db
def test_the_list_focus_draws_no_chart_javascript(viewer_client, register):
    """The list is a table. A page with no chart ships no chart bundle."""
    response = viewer_client.get(reverse("membership"), {"fookus": FOCUS_REGISTER})
    assert "build/charts.js" not in response.content.decode()


@pytest.mark.django_db
def test_searching_narrows_the_rendered_list(viewer_client, register):
    response = viewer_client.get(
        reverse("membership"), {"fookus": FOCUS_REGISTER, "otsing": "Vesipapi"}
    )
    body = response.content.decode()
    assert "Vesipapi Logistika" in body
    assert "Kuutõrvaja Masinaehitus" not in body


@pytest.mark.django_db
def test_a_search_matching_nothing_says_so(viewer_client, register):
    response = viewer_client.get(
        reverse("membership"), {"fookus": FOCUS_REGISTER, "otsing": "puuduvsõna"}
    )
    assert "Ükski kirje ei vasta otsingule." in response.content.decode()


@pytest.mark.django_db
def test_a_page_number_past_the_end_renders_the_last_page(viewer_client, register):
    response = viewer_client.get(reverse("membership"), {"fookus": FOCUS_REGISTER, "leht": "9999"})
    assert response.status_code == 200
    assert "Kuutõrvaja Masinaehitus" in response.content.decode()


@pytest.mark.django_db
def test_an_unreadable_page_number_renders_the_first_page(viewer_client, register):
    response = viewer_client.get(reverse("membership"), {"fookus": FOCUS_REGISTER, "leht": "eile"})
    assert response.status_code == 200


@pytest.mark.django_db
def test_the_other_focuses_never_render_member_rows(viewer_client, register):
    """A row-level page is one page, not a thing every focus starts doing."""
    for focus in ("ulevaade", "kasv"):
        response = viewer_client.get(reverse("membership"), {"fookus": focus})
        assert "Kuutõrvaja Masinaehitus" not in response.content.decode()
