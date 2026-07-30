"""Idempotent registration of the public news-feed data source."""

from django.conf import settings

from apps.sources.models import DataSource, SourceType, UpdateFrequency
from apps.sources.services import create_data_source

SOURCE_NAME = "Koda.ee uudiste RSS"
SOURCE_DESCRIPTION = (
    "Koda.ee avalik uudiste RSS-voog. Salvestatakse pealkiri, viide, avaldamise "
    "aeg, rubriik kui see on olemas, ja puhastatud lühikokkuvõte. Artiklite "
    "täisteksti ega HTML-i ei salvestata."
)
STALE_AFTER_DAYS = 3


def ensure_news_source(*, actor=None, correlation_id=None) -> DataSource:
    slug = settings.KODA_NEWS_SOURCE_SLUG
    existing = DataSource.objects.filter(slug=slug).first()
    if existing is not None:
        return existing
    return create_data_source(
        actor=actor,
        correlation_id=correlation_id,
        slug=slug,
        name=SOURCE_NAME,
        source_type=SourceType.WEBSITE,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
