"""Idempotent registration of the legal-work data sources.

Two, and they are independent: the canonical workbook the dashboard reports
from, and the public Koda.ee `Hetkel käsil` catalogue collected to enrich it.
A failed collection of the second must never mark the first stale, which is why
each has its own source, its own feed state and its own schedule.
"""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Õigusloome töölaud"
SOURCE_DESCRIPTION = (
    "Juristide tööfailist ette valmistatud kontrollitud Exceli andmeallikas. "
    "DashKoda ei muuda seda faili ega loe juristide operatiivset tööfaili."
)
# Two days: the workbook is refreshed daily, so one missed run is tolerable and
# two means something is wrong.
STALE_AFTER_DAYS = 2


def ensure_legal_work_source(*, actor=None, correlation_id=None):
    """Return the legal-work `DataSource`, creating it once if needed."""
    return ensure_data_source(
        slug=settings.LEGAL_WORK_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.SPREADSHEET,
        # The Chamber's definitive authority order is still an open decision
        # gate, so this keeps the model's neutral defaults rather than
        # inventing a ranking here.
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )


CURRENT_TOPICS_SOURCE_NAME = "Koda.ee hetkel käsil"
CURRENT_TOPICS_SOURCE_DESCRIPTION = (
    "Koda.ee avalik „Hetkel käsil“ loend ja sellelt otse viidatud alamlehed. "
    "Salvestatakse ainult normaliseeritud tekst: pealkiri, kokkuvõte, lehe "
    "sisu, avaldamise kuupäev, tagasiside tähtaeg ja nimetatud asutus. "
    "Kasutatakse üksnes õigusloome kirjete rikastamiseks; see ei ole "
    "töölaua näitaja ega mõjuta andmete värskuse loendurit."
)
# Three days: the listing changes a few times a week, so a single missed run is
# not news and the threshold is deliberately looser than the workbook's.
CURRENT_TOPICS_STALE_AFTER_DAYS = 3


def ensure_current_topics_source(*, actor=None, correlation_id=None):
    """Return the current-topic `DataSource`, creating it once if needed."""
    return ensure_data_source(
        slug=settings.KODA_CURRENT_TOPICS_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=CURRENT_TOPICS_SOURCE_NAME,
        source_type=SourceType.WEBSITE,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=CURRENT_TOPICS_STALE_AFTER_DAYS,
        description=CURRENT_TOPICS_SOURCE_DESCRIPTION,
    )
