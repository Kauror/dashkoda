"""Which records may carry a consultation link, and where that rule is applied.

The rule is two conditions — open, and no opinion sent — and its whole value is
that it is stated once. So these cases check both the definition itself and that
the query form and the in-memory form cannot drift apart, because those are the
two shapes every caller uses.
"""

from __future__ import annotations

import itertools

import pytest

from apps.legal_work.consultation import (
    CONSULTATION_ELIGIBLE,
    consultation_eligible_items,
    is_consultation_eligible,
)
from apps.legal_work.models import LegalWorkItem, SentStatus

pytestmark = pytest.mark.django_db


def make_item(snapshot, *, record_id, is_open, sent_status, row):
    """One row written straight to the table, bypassing the workbook."""
    import datetime as dt

    return LegalWorkItem.objects.create(
        snapshot=snapshot,
        record_id=record_id,
        source_year=2099,
        source_nr=row,
        topic=f"Sünteetiline teema {record_id}",
        act_type="Seaduse eelnõu",
        received_date=dt.date.today(),
        sent_status=sent_status,
        sent_date=dt.date.today() if sent_status == SentStatus.SENT else None,
        is_open=is_open,
        source_row=row,
    )


# -- the definition ---------------------------------------------------------


@pytest.mark.parametrize(
    ("is_open", "sent_status", "eligible"),
    [
        (True, SentStatus.PENDING, True),
        (True, SentStatus.NOT_SENT, True),
        (True, SentStatus.INVALID, True),
        (True, SentStatus.SENT, False),
        (False, SentStatus.PENDING, False),
        (False, SentStatus.NOT_SENT, False),
        (False, SentStatus.SENT, False),
        (False, SentStatus.INVALID, False),
    ],
)
def test_the_rule_over_every_combination(imported_snapshot, is_open, sent_status, eligible):
    item = make_item(
        imported_snapshot,
        record_id=f"SYN-{is_open}-{sent_status}",
        is_open=is_open,
        sent_status=sent_status,
        row=900,
    )

    assert is_consultation_eligible(item) is eligible
    assert LegalWorkItem.objects.filter(CONSULTATION_ELIGIBLE, pk=item.pk).exists() is eligible


def test_the_query_and_the_python_rule_never_disagree(imported_snapshot):
    """Two forms of one rule, so a test compares them over the whole space."""
    created = []
    for index, (is_open, status) in enumerate(itertools.product([True, False], SentStatus.values)):
        created.append(
            make_item(
                imported_snapshot,
                record_id=f"SYN-P{index}",
                is_open=is_open,
                sent_status=status,
                row=800 + index,
            )
        )

    by_query = set(
        consultation_eligible_items(
            LegalWorkItem.objects.filter(pk__in=[item.pk for item in created])
        ).values_list("pk", flat=True)
    )
    by_python = {item.pk for item in created if is_consultation_eligible(item)}

    assert by_query == by_python
    assert by_python, "the fixture must produce at least one eligible record"


def test_a_sent_record_is_not_eligible_even_while_open(imported_snapshot):
    """The case the rule exists for.

    A record can stay open after its opinion has gone out — a later stage, an
    administrative tail. The consultation page is nonetheless finished business,
    and what a reader wants next is the opinion, which DashKoda does not have.
    """
    item = make_item(
        imported_snapshot,
        record_id="SYN-OPEN-SENT",
        is_open=True,
        sent_status=SentStatus.SENT,
        row=910,
    )

    assert item.is_open is True
    assert is_consultation_eligible(item) is False


# -- where it is applied ----------------------------------------------------


def test_the_current_matcher_applies_the_rule():
    """Stated as source inspection: the matcher must not restate the rule."""
    import pathlib

    source = pathlib.Path("apps/legal_work/current_topic_match_sync.py").read_text(encoding="utf-8")

    assert "consultation_eligible_items" in source
    assert "is_open=True" not in source, "the rule must not be restated here"


def test_the_archive_matcher_applies_the_rule():
    import pathlib

    source = pathlib.Path("apps/legal_work/archived_topic_match_sync.py").read_text(
        encoding="utf-8"
    )

    assert "consultation_eligible_items" in source
    assert "is_open=True" not in source


def test_the_current_matcher_version_records_the_population_change():
    """1.1 differs from 1.0 in who is considered, not in how anyone is scored."""
    from apps.legal_work.current_topic_matching import (
        AUTO_MATCH_SCORE,
        MATCHER_VERSION,
        MINIMUM_MARGIN,
        PLAUSIBLE_SCORE,
    )

    assert MATCHER_VERSION.startswith("1.1-")
    # The thresholds the 1.0 snapshots were produced under, unchanged.
    assert str(AUTO_MATCH_SCORE) == "62.00"
    assert str(PLAUSIBLE_SCORE) == "38.00"
    assert str(MINIMUM_MARGIN) == "12.00"
