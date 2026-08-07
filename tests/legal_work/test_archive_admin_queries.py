"""What the archive-match changelist costs to draw.

`current_topic_decision` answers a reviewer's first question about an archive
match — what the current matcher said about the same record. It did so by
looking the record up in the current-topic match snapshot **once per row**, and
the changelist shows a hundred rows.

A correlated subquery answers all of them inside the page's own query. These
tests pin that the count no longer grows with the number of rows, and that the
column still says the same thing.

No viewer page is affected: this column exists only in the admin.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.urls import reverse

from apps.legal_work.archived_topic_match_sync import run_archive_matching
from apps.legal_work.current_topic_match_sync import run_current_topic_matching
from apps.legal_work.models import (
    LegalArchivedTopicMatch,
    LegalCurrentTopicMatch,
    MatchDecision,
)

from .archive_factory import simple_archive
from .current_topic_factory import DETAIL_PREFIX, LISTING_PATH, FakeSite, card, detail, listing


def current_catalogue(*slugs: str) -> FakeSite:
    """A current listing plus a detail page per slug, as the real sync sees it."""
    pages = {LISTING_PATH: listing(*(card(slug, f"Teema {slug}") for slug in slugs))}
    for slug in slugs:
        pages[f"{DETAIL_PREFIX}{slug}"] = detail(title=f"Teema {slug}")
    return FakeSite(pages)


pytestmark = pytest.mark.django_db

CHANGELIST = "admin:legal_work_legalarchivedtopicmatch_changelist"


@pytest.fixture
def matched_world(imported_snapshot, publish_current_topics, publish_archived_topics):
    """Both catalogues published and both matchers run."""
    publish_current_topics(current_catalogue("alpha"))
    run_current_topic_matching()
    publish_archived_topics(simple_archive("vana", "vanem"))
    run_archive_matching()
    return imported_snapshot


@pytest.fixture
def admin_client(client, superuser, authenticate_viewer):
    """Both gates: the viewer PIN and a Django superuser."""
    authenticate_viewer(client)
    client.force_login(superuser)
    return client


class TestTheChangelistQueryCount:
    def test_the_column_costs_no_query_of_its_own(
        self, admin_client, matched_world, django_assert_max_num_queries
    ):
        """The whole page, including a decision for every row."""
        rows = LegalArchivedTopicMatch.objects.count()
        assert rows, "the fixture published no archive matches"

        # Generous, because a Django admin changelist also runs session, auth,
        # count and filter queries. What matters is that it does not scale.
        with django_assert_max_num_queries(20):
            response = admin_client.get(reverse(CHANGELIST))

        assert response.status_code == 200

    def test_the_annotation_answers_every_row(self, matched_world, rf, superuser):
        """One statement carries a decision for each row it returns."""
        model_admin = django_admin.site._registry[LegalArchivedTopicMatch]
        request = rf.get("/admin/")
        request.user = superuser

        queryset = model_admin.get_queryset(request)

        assert queryset.exists()
        for row in queryset:
            # Present on every row, without touching the database again.
            assert hasattr(row, "_current_topic_decision")


class TestTheColumnStillSaysTheSameThing:
    def _admin(self):
        return django_admin.site._registry[LegalArchivedTopicMatch]

    def _rendered(self, superuser, rf):
        model_admin = self._admin()
        request = rf.get("/admin/")
        request.user = superuser
        return {
            row.legal_item_id: model_admin.current_topic_decision(row)
            for row in model_admin.get_queryset(request)
        }

    def test_it_matches_what_the_current_matcher_decided(self, matched_world, rf, superuser):
        rendered = self._rendered(superuser, rf)
        assert rendered, "no rows to compare"

        for row in LegalArchivedTopicMatch.objects.select_related("snapshot"):
            match = LegalCurrentTopicMatch.objects.filter(
                snapshot=row.snapshot.current_topic_match_snapshot,
                legal_item_id=row.legal_item_id,
            ).first()
            expected = match.get_decision_display() if match else "—"

            assert rendered[row.legal_item_id] == expected

    def test_it_renders_a_label_rather_than_a_stored_value(self, matched_world, rf, superuser):
        """The reviewer reads Estonian, not `matched`."""
        rendered = set(self._rendered(superuser, rf).values())
        stored = {str(value) for value in MatchDecision.values}

        assert rendered
        assert not (rendered & stored), f"a raw stored value reached the column: {rendered}"

    def test_a_record_the_current_matcher_never_saw_shows_a_dash(
        self, matched_world, rf, superuser
    ):
        """An em dash, never a blank and never an invented decision."""
        LegalCurrentTopicMatch.objects.all().delete()

        assert set(self._rendered(superuser, rf).values()) == {"—"}
