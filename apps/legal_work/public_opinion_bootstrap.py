"""Idempotent registration of the public Koda.ee opinion data source.

Its own `DataSource`, separate from the private opinion inbox and from every
other Koda.ee feed. The private source records what the Chamber filed; this one
records what Koda.ee published, and the two must stay independently observable
— a broken crawl must never read as a broken inbox, or the other way round.
"""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Koda.ee avalikud arvamused"
SOURCE_DESCRIPTION = (
    "Koda.ee avalikult avaldatud arvamusartiklid ja nende juurde kuuluvad "
    "arvamuskirjade PDF-id, kogutud Meie arvamus ja uudiste loenditest. "
    "Failid hoitakse samas hallatud hoidlas kui privaatsed arvamusdokumendid, "
    "sisupõhise võtme all; sama sisuga fail on üks dokument mõlema allika "
    "päritoluga. Kogumine on väljuv ja kirjutuskaitstud."
)
# The Chamber publishes opinions when it sends them, not on a schedule; a
# quiet fortnight is ordinary. The value exists because the model requires
# one; the dashboard freshness counter does not read this source.
STALE_AFTER_DAYS = 90


def ensure_public_opinion_source(*, actor=None, correlation_id=None):
    """Return the public opinion `DataSource`, creating it once if needed."""
    return ensure_data_source(
        slug=settings.KODA_OPINIONS_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.WEBSITE,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
