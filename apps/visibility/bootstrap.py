"""Idempotent registration of the visibility data sources.

One source per platform rather than one "social media" source, for the same
reason `membership` keeps its two sources apart: a Facebook follower and a
YouTube subscriber are counted by different organisations under different
definitions, and a single row that mixed them could never be audited back to
what somebody actually read.

The newsletter lists share one source because they genuinely come from one
system, one login and one reading session.

Every one of these is registered as `SourceType.MANUAL` with
`UpdateFrequency.IRREGULAR`: nothing polls them, and claiming a cadence would
be the first step towards a page saying "synchronised".

`stale_after_days` is deliberately **not** set on the `DataSource` rows. That
field drives the source-level staleness other modules use for a *feed*, and
these have no feed to fall behind. Freshness here is a property of the newest
observation, and the thresholds live in `registry.py` beside the metric they
describe.
"""

from apps.sources.models import SourceType, UpdateFrequency
from apps.sources.services import ensure_data_source

from .registry import (
    SOURCE_FACEBOOK,
    SOURCE_GA4,
    SOURCE_INSTAGRAM,
    SOURCE_LINKEDIN,
    SOURCE_NAMES,
    SOURCE_SMAILY,
    SOURCE_YOUTUBE,
)

_MANUAL_DESCRIPTION_SUFFIX = (
    "Väärtused sisestab koja töötaja käsitsi platvormi enda statistika põhjal. "
    "DashKoda ei päri platvormi API-t, ei kasuta ühtegi juurdepääsuvõtit ega "
    "salvesta ühtegi üksikut jälgijat, tellijat ega e-posti aadressi."
)

SOURCE_DESCRIPTIONS = {
    SOURCE_SMAILY: (
        "Koja kahe uudiskirja nimekirja aktiivsete saajate arv Smailys: liikmete "
        "nimekiri, mitteliikmete nimekiri ja nende kattuvus. Ei ole saadetud kirjade, "
        "avamiste ega klikkide arv. " + _MANUAL_DESCRIPTION_SUFFIX
    ),
    SOURCE_FACEBOOK: "Koja Facebooki lehe jälgijate arv. " + _MANUAL_DESCRIPTION_SUFFIX,
    SOURCE_LINKEDIN: "Koja LinkedIni lehe jälgijate arv. " + _MANUAL_DESCRIPTION_SUFFIX,
    SOURCE_INSTAGRAM: "Koja Instagrami konto jälgijate arv. " + _MANUAL_DESCRIPTION_SUFFIX,
    SOURCE_YOUTUBE: "Koja YouTube’i kanali tellijate arv. " + _MANUAL_DESCRIPTION_SUFFIX,
    SOURCE_GA4: (
        "Koja kodulehe külastusstatistika Google Analytics 4-st. Allikas on "
        "registreeritud, kuid **ei ole ühendatud**: ühtegi päringut ei tehta, ühtegi "
        "mandaati ei ole ja ühtegi vaatlust ei ole avaldatud."
    ),
}


def _ensure(slug: str, *, source_type: str, actor=None, correlation_id=None):
    return ensure_data_source(
        slug=slug,
        actor=actor,
        correlation_id=correlation_id,
        name=SOURCE_NAMES[slug],
        source_type=source_type,
        expected_update_frequency=UpdateFrequency.IRREGULAR,
        stale_after_days=None,
        description=SOURCE_DESCRIPTIONS[slug],
    )


def ensure_smaily_source(*, actor=None, correlation_id=None):
    return _ensure(
        SOURCE_SMAILY, source_type=SourceType.MANUAL, actor=actor, correlation_id=correlation_id
    )


def ensure_facebook_source(*, actor=None, correlation_id=None):
    return _ensure(
        SOURCE_FACEBOOK, source_type=SourceType.MANUAL, actor=actor, correlation_id=correlation_id
    )


def ensure_linkedin_source(*, actor=None, correlation_id=None):
    return _ensure(
        SOURCE_LINKEDIN, source_type=SourceType.MANUAL, actor=actor, correlation_id=correlation_id
    )


def ensure_instagram_source(*, actor=None, correlation_id=None):
    return _ensure(
        SOURCE_INSTAGRAM, source_type=SourceType.MANUAL, actor=actor, correlation_id=correlation_id
    )


def ensure_youtube_source(*, actor=None, correlation_id=None):
    return _ensure(
        SOURCE_YOUTUBE, source_type=SourceType.MANUAL, actor=actor, correlation_id=correlation_id
    )


def ensure_ga4_source(*, actor=None, correlation_id=None):
    """Register the GA4 source without connecting anything.

    Registering it early costs nothing and makes the unconnected state a fact in
    the database rather than a gap. No artifact and no import run is created:
    `build_import_run` would need a content identity, and there is no content.
    """
    return _ensure(
        SOURCE_GA4, source_type=SourceType.WEBSITE, actor=actor, correlation_id=correlation_id
    )


_ENSURE_BY_SLUG = {
    SOURCE_SMAILY: ensure_smaily_source,
    SOURCE_FACEBOOK: ensure_facebook_source,
    SOURCE_LINKEDIN: ensure_linkedin_source,
    SOURCE_INSTAGRAM: ensure_instagram_source,
    SOURCE_YOUTUBE: ensure_youtube_source,
    SOURCE_GA4: ensure_ga4_source,
}


def ensure_visibility_source(slug: str, *, actor=None, correlation_id=None):
    """Register one source by slug. Raises on an unregistered slug."""
    try:
        ensure = _ENSURE_BY_SLUG[slug]
    except KeyError:
        raise ValueError(f"Unknown visibility source slug: {slug}") from None
    return ensure(actor=actor, correlation_id=correlation_id)


def ensure_manual_visibility_sources(*, actor=None, correlation_id=None) -> dict:
    """Every source a manual submission can write into, keyed by slug."""
    from .registry import MANUAL_SOURCE_SLUGS

    return {
        slug: ensure_visibility_source(slug, actor=actor, correlation_id=correlation_id)
        for slug in MANUAL_SOURCE_SLUGS
    }
