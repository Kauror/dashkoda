"""Idempotent registration of the Koda.ee archive data source.

Its own `DataSource`, separate from the current-topic catalogue's. The two are
collected on different schedules with different failure modes, and a shared
source row would mean one feed's outage reading as the other's.
"""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Koda.ee hetkel käsil arhiiv"
SOURCE_DESCRIPTION = (
    "Koda.ee avalik „Hetkel käsil“ arhiiv ja sellelt viidatud alamlehed. "
    "Kasutatakse varuallikana: kui konsultatsioonileht ei ole enam jooksvas "
    "loendis, võib arhiivikirje anda õigusloome kirjele viite. Salvestatakse "
    "ainult normaliseeritud tekst. Ei ole töölaua näitaja ega mõjuta andmete "
    "värskuse loendurit."
)
# Four days: the archive changes only when a consultation closes, so a missed
# run is not news. Deliberately looser than the current catalogue's three.
STALE_AFTER_DAYS = 4


def ensure_archive_source(*, actor=None, correlation_id=None):
    """Return the archive `DataSource`, creating it once if needed."""
    return ensure_data_source(
        slug=settings.KODA_ARCHIVE_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.WEBSITE,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
