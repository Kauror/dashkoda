"""Idempotent registration of the one event-programme data source."""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Sündmuste programm"
SOURCE_DESCRIPTION = (
    "Koja teenusekoodide operatiivsest töövihikust ette valmistatud kontrollitud "
    "Exceli andmeallikas. DashKoda ei muuda seda faili ega loe operatiivset töövihikut."
)
# Two days: the export is refreshed every morning at 06:30, so one missed run is
# tolerable and two means the flow or the script needs attention.
STALE_AFTER_DAYS = 2


def ensure_event_programme_source(*, actor=None, correlation_id=None):
    """Return the event-programme `DataSource`, creating it once if needed."""
    return ensure_data_source(
        slug=settings.EVENT_PROGRAMME_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.SPREADSHEET,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
