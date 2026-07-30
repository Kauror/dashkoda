"""Idempotent registration of the one legal-work data source."""

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
