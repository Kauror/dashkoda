"""The three newsletters, and the total that is never computed.

The Chamber sends e-Teataja, eNews and e-Vestnik, each to its own list. Nobody
has counted how many people are on more than one of them, so there is no
arithmetic to do here: adding the three would claim an overlap of zero, which is
a measurement no source has made. Each list is reported under its own name.

This replaced a member / non-member / overlap model of a single newsletter,
which derived a unique audience from those three. The tests below are what stops
that arithmetic coming back.
"""

from __future__ import annotations

import pytest

from apps.visibility.models import VisibilityMetric, VisibilityObservation
from apps.visibility.page import build_channel_band
from apps.visibility.registry import NEWSLETTER_METRICS, spec_for
from apps.visibility.selectors import get_newsletter_summary

pytestmark = pytest.mark.django_db


def newsletter_slot():
    return next(slot for slot in build_channel_band() if slot.label == "Uudiskirjad")


# -- each list on its own -----------------------------------------------


def test_each_newsletter_is_stored_under_its_own_metric(submit):
    submit(newsletter_eteataja=20622, newsletter_enews=750, newsletter_evestnik=525)

    stored = {row.metric: row.value for row in VisibilityObservation.objects.all()}

    assert stored == {
        VisibilityMetric.NEWSLETTER_ETEATAJA: 20622,
        VisibilityMetric.NEWSLETTER_ENEWS: 750,
        VisibilityMetric.NEWSLETTER_EVESTNIK: 525,
    }


def test_the_lists_are_never_added_together(submit):
    """No total, anywhere: not on the summary, not on the card.

    21 897 is the sum of the three. It may not appear, because it would be the
    audience only if nobody subscribed to two newsletters — and nobody has
    checked whether anyone does.
    """
    submit(newsletter_eteataja=20622, newsletter_enews=750, newsletter_evestnik=525)

    summary = get_newsletter_summary()
    slot = newsletter_slot()

    assert not hasattr(summary, "unique_recipients")
    assert slot.value is None, "the card lists the newsletters and leads with none of them"
    assert [detail.value for detail in slot.details] == [20622, 750, 525]


def test_a_list_nobody_entered_contributes_no_row_rather_than_a_zero(submit):
    """A zero would say the newsletter has no subscribers, which nobody counted."""
    submit(newsletter_eteataja=20622)

    slot = newsletter_slot()

    assert [detail.label for detail in slot.details] == ["e-Teataja"]
    assert [detail.value for detail in slot.details] == [20622]
    # The unentered lists are named as unentered, not drawn as empty figures.
    assert "eNews" in slot.secondary
    assert "e-Vestnik" in slot.secondary


def test_nothing_entered_at_all_leaves_the_card_empty():
    slot = newsletter_slot()

    assert slot.value is None
    assert slot.details == ()
    assert slot.has_value is False


def test_the_card_is_dated_by_its_oldest_entered_list(submit, today, days_ago):
    """A card is only as current as the stalest list on it."""
    submit(observation_date=days_ago(40), newsletter_evestnik=525)
    submit(observation_date=today, newsletter_eteataja=20622, newsletter_enews=750)

    summary = get_newsletter_summary()

    assert summary.as_of == days_ago(40)
    assert summary.readings_share_a_date is False


# -- how the metrics are described --------------------------------------


def test_every_newsletter_has_a_registry_entry_of_its_own():
    assert len(NEWSLETTER_METRICS) == 3
    assert [spec_for(metric).label for metric in NEWSLETTER_METRICS] == [
        "e-Teataja",
        "eNews",
        "e-Vestnik",
    ]


def test_the_definitions_never_describe_sends_opens_or_clicks():
    """These count list membership.

    Naming them anything else would be a claim no source has made: Smaily
    reports who is on a list, not who received, opened or clicked anything.

    The word "aktiivsete" is deliberately **not** required. `list.php` returns a
    `subscribers_count` and does not document whether unsubscribed addresses are
    excluded from it, so calling the figure "active recipients" would assert
    something the audit of the account did not establish.
    """
    for metric in NEWSLETTER_METRICS:
        spec = spec_for(metric)
        assert spec.unit == "saajat"
        assert "smailys" in spec.definition.lower()
        for word in ("avamis", "klikk"):
            assert word not in spec.definition.lower()


def test_the_retired_overlap_vocabulary_is_gone():
    """The union rule cannot come back through a stray metric key."""
    values = " ".join(VisibilityMetric.values)

    assert "overlap" not in values
    assert "unique" not in values
    assert "member_recipients" not in values
