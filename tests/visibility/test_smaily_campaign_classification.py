"""Recognising which newsletter a campaign is an issue of.

No database. The template names below are the shapes read from the live account
on 2026-08-10, with the dates kept because the date is what makes the token
matching non-trivial.

Two failures would be invisible on a finished dashboard and both are pinned
here: filing a non-member send under members (because `mitteliikmed` contains
`liikmed`), and sweeping an unrelated mailing into a newsletter's open rate.
"""

from __future__ import annotations

from apps.visibility.models import VisibilityMetric
from apps.visibility.smaily_campaigns import (
    AUDIENCE_MEMBERS,
    AUDIENCE_NON_MEMBERS,
    AUDIENCE_UNKNOWN,
    classify,
    label_for,
)

ETEATAJA = VisibilityMetric.NEWSLETTER_ETEATAJA
ENEWS = VisibilityMetric.NEWSLETTER_ENEWS
EVESTNIK = VisibilityMetric.NEWSLETTER_EVESTNIK


# -- the three newsletters --------------------------------------------------


def test_an_eteataja_send_to_members_is_recognised():
    result = classify("e-Teataja 30.07.26 liikmed")
    assert result.metric == ETEATAJA
    assert result.audience == AUDIENCE_MEMBERS


def test_an_eteataja_send_to_non_members_is_not_filed_under_members():
    """`mitteliikmed` contains `liikmed`.

    The obvious ordering would file every non-member send under members, and
    the two halves of e-Teataja would then look like one list sent twice.
    """
    result = classify("e-Teataja 4.08 mitteliikmed")
    assert result.metric == ETEATAJA
    assert result.audience == AUDIENCE_NON_MEMBERS


def test_enews_and_evestnik_are_recognised():
    assert classify("E-News 07.05.26").metric == ENEWS
    assert classify("e-Vestnik 25.06.26").metric == EVESTNIK


def test_recognition_is_case_insensitive_and_tolerates_leading_space():
    assert classify("  E-TEATAJA 1.01.26").metric == ETEATAJA


def test_a_send_with_no_audience_marker_leaves_the_audience_unknown():
    result = classify("e-Vestnik 25.06.26")
    assert result.audience == AUDIENCE_UNKNOWN


# -- what must not be swept in ----------------------------------------------


def test_the_other_mailings_are_left_unclassified():
    """The account sends far more than the three newsletters.

    111 of the 200 most recent completed campaigns are event calendars,
    Enterprise Europe Network mailings and one-off letters. None of them may
    reach a newsletter's open rate.
    """
    for template in (
        "Ürituste kalender 04.08.26",
        "EEN 16.06.26 tööstus, materjal, taristu",
        "Konkurentsivõime edetabeli info mitteliikmetele",
        "Kevadpidu 22.05.26",
        "Uute liikmete kutse 15.04.26",
        "Tootja kohustused 31.07.26",
        "Jäätmeseaduse muudatused 20.07.26",
    ):
        result = classify(template)
        assert not result.is_newsletter, template
        assert result.metric == ""


def test_a_token_must_be_a_whole_word():
    """`e-News` must not match a template that merely starts with those letters."""
    assert not classify("e-Newsletter kokkuvõte 01.01.26").is_newsletter


def test_a_newsletter_named_only_inside_a_sentence_is_not_an_issue_of_it():
    """ "Kutse e-Teataja lugejatele" is an invitation *to* readers, not an issue.

    The subject fallback uses the same anchored pattern as the template name for
    exactly this reason.
    """
    assert not classify("", subject="Kutse e-Teataja lugejatele").is_newsletter


def test_an_empty_template_and_subject_classify_as_nothing():
    assert not classify("").is_newsletter
    assert not classify("", subject="").is_newsletter


# -- the subject fallback ---------------------------------------------------


def test_the_subject_is_used_when_a_template_was_deleted():
    """A campaign whose template is gone can still be recognised from its own
    subject line, which is how Smaily writes these."""
    result = classify("", subject="E-Teataja: Riigipiiri kaitserajatiste alused")
    assert result.metric == ETEATAJA


def test_the_template_wins_over_the_subject():
    """The template is the reliable field; the subject is a fallback only."""
    result = classify("e-Vestnik 25.06.26", subject="E-Teataja: midagi muud")
    assert result.metric == EVESTNIK


# -- labels -----------------------------------------------------------------


def test_every_newsletter_has_a_label_and_nothing_else_does():
    assert label_for(ETEATAJA) == "e-Teataja"
    assert label_for(ENEWS) == "eNews"
    assert label_for(EVESTNIK) == "e-Vestnik"
    assert label_for(VisibilityMetric.FACEBOOK_FOLLOWERS) == ""
