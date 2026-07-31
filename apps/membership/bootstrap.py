"""Idempotent registration of the two membership data sources.

They are registered separately and stay separate. The public directory count and
the Chamber's internal board-report history answer different questions, and
nothing in the application treats one as a continuation of the other.
"""

from django.conf import settings

from apps.sources.models import AuthorityTier, SourceType, UpdateFrequency
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


INTERNAL_SOURCE_NAME = "Liikmeskonna sisemised juhatuse aruanded"
INTERNAL_SOURCE_DESCRIPTION = (
    "Koja enda juhatusele esitatud liikmeskonna aruannete ajalugu ja käsitsi "
    "sisestatud uued aruanded. See ei ole sama näitaja mis Koda.ee avalik "
    "liikmekataloog: aruanded loendavad liikmeskonda koja enda määratluse järgi "
    "ning kahte rida ei ühendata omavahel üheks aegreaks. Üksikute liikmete "
    "nimesid, registrikoode ega maksestaatust ei salvestata."
)


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


def ensure_internal_membership_source(*, actor=None, correlation_id=None):
    """Register the internal board-report source.

    No staleness threshold is set. Reports arrive when the board meets, and
    there is no automated collection to fall behind — flagging the source as
    stale would report a fault that does not exist.
    """
    return ensure_data_source(
        slug=settings.MEMBERSHIP_INTERNAL_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=INTERNAL_SOURCE_NAME,
        source_type=SourceType.DOCUMENT,
        authority_tier=AuthorityTier.PRIMARY,
        expected_update_frequency=UpdateFrequency.MONTHLY,
        stale_after_days=None,
        description=INTERNAL_SOURCE_DESCRIPTION,
    )
