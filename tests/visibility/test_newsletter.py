"""The newsletter union rule.

The Chamber runs two lists and some people are on both. Adding the two counts
and calling the sum "unique recipients" would double-count exactly those people,
so the union is computed only when the overlap is known — and when it is not,
the page says so instead of guessing.
"""

from __future__ import annotations

import pytest

from apps.visibility.manual import VisibilitySubmission, build_preview
from apps.visibility.models import VisibilityMetric, VisibilityObservation
from apps.visibility.selectors import get_newsletter_summary

pytestmark = pytest.mark.django_db


def _preview(today, **values):
    return build_preview(VisibilitySubmission(observation_date=today, values=values))


# -- the union ----------------------------------------------------------


def test_unique_audience_is_member_plus_nonmember_minus_overlap(submit):
    submit(
        newsletter_member_recipients=1200,
        newsletter_nonmember_recipients=800,
        newsletter_overlap_recipients=150,
    )

    assert get_newsletter_summary().unique_recipients == 1850


def test_a_zero_overlap_is_a_real_answer_and_the_lists_simply_add(submit):
    submit(
        newsletter_member_recipients=1200,
        newsletter_nonmember_recipients=800,
        newsletter_overlap_recipients=0,
    )

    summary = get_newsletter_summary()
    assert summary.overlap_known is True
    assert summary.overlap.value == 0
    assert summary.unique_recipients == 2000


def test_a_missing_overlap_is_not_a_zero_and_yields_no_union(submit):
    submit(newsletter_member_recipients=1200, newsletter_nonmember_recipients=800)

    summary = get_newsletter_summary()
    assert summary.overlap_known is False
    assert summary.overlap.value is None
    assert summary.unique_recipients is None
    assert summary.missing_overlap_message == "Nimekirjade kattuvus ei ole sisestatud."


def test_the_two_lists_are_stored_as_separate_metrics(submit):
    submit(newsletter_member_recipients=1200, newsletter_nonmember_recipients=800)

    stored = {
        row.metric: row.value
        for row in VisibilityObservation.objects.filter(
            metric__in=[
                VisibilityMetric.NEWSLETTER_MEMBER_RECIPIENTS,
                VisibilityMetric.NEWSLETTER_NONMEMBER_RECIPIENTS,
            ]
        )
    }
    assert stored == {
        VisibilityMetric.NEWSLETTER_MEMBER_RECIPIENTS: 1200,
        VisibilityMetric.NEWSLETTER_NONMEMBER_RECIPIENTS: 800,
    }


def test_the_union_is_never_stored_as_a_metric_of_its_own(submit):
    """Persisting it would create a fourth number able to disagree with three."""
    submit(
        newsletter_member_recipients=1200,
        newsletter_nonmember_recipients=800,
        newsletter_overlap_recipients=150,
    )

    assert VisibilityObservation.objects.count() == 3
    assert "unique" not in " ".join(VisibilityMetric.values)


def test_a_union_is_dated_by_its_oldest_ingredient(submit, today, days_ago):
    """A union is only as current as the stalest figure that went into it."""
    submit(observation_date=days_ago(40), newsletter_overlap_recipients=150)
    submit(
        observation_date=today,
        newsletter_member_recipients=1200,
        newsletter_nonmember_recipients=800,
    )

    summary = get_newsletter_summary()
    assert summary.unique_recipients == 1850
    assert summary.as_of == days_ago(40)
    assert summary.readings_share_a_date is False


# -- validation ---------------------------------------------------------


def test_an_overlap_larger_than_the_member_list_is_refused(today):
    preview = _preview(
        today,
        newsletter_member_recipients=100,
        newsletter_nonmember_recipients=900,
        newsletter_overlap_recipients=101,
    )

    assert preview.can_publish is False
    assert any("liikmete" in error.lower() for error in preview.errors)


def test_an_overlap_larger_than_the_nonmember_list_is_refused(today):
    preview = _preview(
        today,
        newsletter_member_recipients=900,
        newsletter_nonmember_recipients=100,
        newsletter_overlap_recipients=101,
    )

    assert preview.can_publish is False
    assert any("mitteliikmete" in error.lower() for error in preview.errors)


def test_an_overlap_equal_to_a_list_is_allowed(today):
    """Every member-list recipient also being on the other list is possible."""
    preview = _preview(
        today,
        newsletter_member_recipients=100,
        newsletter_nonmember_recipients=900,
        newsletter_overlap_recipients=100,
    )

    assert preview.can_publish is True
    assert preview.newsletter.unique_recipients == 900


def test_the_overlap_is_checked_against_a_stored_list_it_was_not_submitted_with(submit, today):
    """Entering only a corrected overlap must still be checked against the lists.

    The list values are not in this submission, so the check uses the readings
    that will still be current after it — otherwise a nonsensical overlap could
    be entered simply by omitting the fields it contradicts.
    """
    submit(newsletter_member_recipients=100, newsletter_nonmember_recipients=900)

    preview = _preview(today, newsletter_overlap_recipients=500)

    assert preview.can_publish is False


def test_a_missing_overlap_warns_but_does_not_block(today):
    preview = _preview(
        today, newsletter_member_recipients=1200, newsletter_nonmember_recipients=800
    )

    assert preview.can_publish is True
    assert preview.newsletter.unique_recipients is None
    assert any("kattuvus" in warning.lower() for warning in preview.warnings)


def test_the_labels_never_describe_sends_opens_or_clicks():
    """These count list membership. Naming them anything else would be a claim
    no source has made."""
    from apps.visibility.registry import NEWSLETTER_METRICS, spec_for

    forbidden = ("saadetud", "avamis", "klikk", "kohale toimetatud")
    for metric in NEWSLETTER_METRICS:
        spec = spec_for(metric)
        assert "saajad" in spec.label.lower() or "saajad" in spec.label
        for word in forbidden:
            assert word not in spec.label.lower()
