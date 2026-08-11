"""Idempotent registration of the E-pood commerce data source."""

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

#: Fixed, non-secret slug. Not a setting: there is one Koda.ee shop, and making
#: it configurable would only create a way for two deployments to disagree about
#: which source a stored fact belongs to.
SHOP_SOURCE_SLUG = "koda-commerce-shop"

SOURCE_NAME = "Koda.ee e-poe tellimused"
SOURCE_DESCRIPTION = (
    "Koda.ee Drupal Commerce e-poe koondandmed: tooted, tootelehtede teed ja "
    "lõpetatud tellimuste päevakoonded toote, liikmestaatuse ja makseviisi kaupa. "
    "Praegu käsitsi koostatud isikuandmevaba väljavõte — automaatset kogumist ei "
    "ole. Summad on tellitud väärtus käibemaksuta, mitte raamatupidamislik tulu."
)

#: The manual export is produced by hand, so no staleness threshold is tracked.
#: A date-based warning would fire every day and mean nothing; what the interface
#: shows instead is `ShopSourceState.source_as_of`, which states the truth.
STALE_AFTER_DAYS = None


def ensure_shop_source(*, actor=None, correlation_id=None):
    return ensure_data_source(
        slug=SHOP_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.SPREADSHEET,
        expected_update_frequency=UpdateFrequency.IRREGULAR,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
