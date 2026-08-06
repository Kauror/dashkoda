"""Idempotent registration of the Chamber opinion-document data source.

Its own `DataSource`, separate from every Koda.ee feed. This one is unlike the
others in kind: it is not collected from a website, it is read from a private
directory the Chamber controls, and its documents are correspondence rather
than published pages.

It is deliberately **not** a dashboard freshness indicator. The Chamber files
opinions when it sends them, not on a schedule, so a week with no new document
is an ordinary week and must not read as a stale feed.
"""

from django.conf import settings

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

SOURCE_NAME = "Kaubanduskoja arvamuste dokumendid"
SOURCE_DESCRIPTION = (
    "Kaubanduskoja väljasaadetud arvamuskirjad PDF-failidena, loetud "
    "privaatsest lähtekaustast. Failid ise hoitakse hallatud privaathoidlas "
    "sisupõhise võtme all; PostgreSQL-i salvestatakse ainult normaliseeritud "
    "tekst ja metaandmed. Ei ole avalik allikas, ei ole töölaua näitaja ega "
    "mõjuta andmete värskuse loendurit."
)
# Documents arrive irregularly, so staleness is not a useful signal here. The
# value exists because the model requires one; nothing reads it for freshness.
STALE_AFTER_DAYS = 90


def ensure_opinion_source(*, actor=None, correlation_id=None):
    """Return the opinion-document `DataSource`, creating it once if needed."""
    return ensure_data_source(
        slug=settings.LEGAL_OPINION_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAME,
        source_type=SourceType.DOCUMENT,
        expected_update_frequency=UpdateFrequency.IRREGULAR,
        stale_after_days=STALE_AFTER_DAYS,
        description=SOURCE_DESCRIPTION,
    )
