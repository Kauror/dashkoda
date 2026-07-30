"""Idempotent registration of the public member-directory data source."""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Koda.ee avalik liikmeloend"
SOURCE_DESCRIPTION = (
    "Koda.ee avaliku liikmekataloogi kirjete arv. Näitab, mitu liikmeprofiili on "
    "veebilehel avaldatud. See ei ole raamatupidamislik, arveldatav ega CRM-i "
    "põhine liikmearv — ükski avalik allikas sellist määratlust ei anna. "
    "Üksikuid liikmeid, nimesid ega registrikoode ei salvestata."
)
# Two days: the directory is checked daily, so one missed run is tolerable and
# two means something is wrong.
STALE_AFTER_DAYS = 2


def ensure_membership_source(*, actor=None, correlation_id=None):
    return ensure_data_source(
        slug=settings.KODA_MEMBERS_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.REGISTRY,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
