"""Idempotent registration of the public event-pages data source."""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Koda.ee sündmuste lehed"
SOURCE_DESCRIPTION = (
    "Koda.ee avalike sündmuste lehtede püsikataloog. Kogutakse ainult lehe aadress "
    "ja seda kirjeldavad väljad, et sündmuste programmile saaks lisada avaliku "
    "viite. Sündmuse nimi, kuupäev, liik ja muud programmi väljad tulevad "
    "jätkuvalt üksnes sündmuste programmi töövihikust."
)
# Discovery adds pages as the site publishes them and never removes one, so a
# quiet week is not a fault. The window is wide enough that only a genuinely
# stopped job shows up as stale.
STALE_AFTER_DAYS = 14


def ensure_event_pages_source(*, actor=None, correlation_id=None):
    return ensure_data_source(
        slug=settings.KODA_EVENT_PAGES_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.WEBSITE,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
