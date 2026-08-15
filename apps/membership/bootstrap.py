"""Idempotent registration of the membership data sources.

They are registered separately and stay separate. The public directory count,
the Chamber's internal board-report history and the roster composition answer
different questions, and nothing in the application treats one as a
continuation of another. The first two are membership *totals* that must never
be merged; the third is not a total at all.

The 2026-08 member register adds two more, still separate: the roster's own
rows (a manual import of the CRM export) and the row-level identities the
public directory publishes. Neither is a membership total either — they exist
so the members-list page can list and the comparison can compare, and their
counts are always labelled with their own source and date.
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


COMPOSITION_SOURCE_NAME = "Liikmeskonna koosseis (liikmete nimekiri)"
COMPOSITION_SOURCE_DESCRIPTION = (
    "Koja liikmete nimekirjast tuletatud koondnäitajad: suurusklassid, maakonnad, "
    "tegevusalad, liikmestaaž ja liitumisaastad. Salvestatakse ainult kokkuvõtlikud "
    "arvud — ühtegi ettevõtte nime, registrikoodi, aadressi ega kontakti ei "
    "salvestata ega logita. See ei ole liikmete arvu näitaja ega ole võrreldav "
    "Koda.ee avaliku liikmekataloogi ega juhatuse aruannete arvudega."
)


def ensure_membership_composition_source(*, actor=None, correlation_id=None):
    """Register the roster-composition source.

    No staleness threshold. The roster is exported by hand when someone needs
    one, so there is no schedule to fall behind and marking it stale would
    report a fault that does not exist.
    """
    return ensure_data_source(
        slug=settings.MEMBERSHIP_COMPOSITION_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=COMPOSITION_SOURCE_NAME,
        source_type=SourceType.DOCUMENT,
        authority_tier=AuthorityTier.PRIMARY,
        expected_update_frequency=UpdateFrequency.IRREGULAR,
        stale_after_days=None,
        description=COMPOSITION_SOURCE_DESCRIPTION,
    )


REGISTER_SOURCE_NAME = "Liikmete nimekiri (CRM-i eksport)"
REGISTER_SOURCE_DESCRIPTION = (
    "Koja liikmete nimekiri, imporditud käsitsi CRM-i ekspordist: nimi, vorm, "
    "liikmenumber, staatus, registrikood, maakond, asula, riik, töötajate arv, "
    "liitumiskuupäev, tegevusala ja veebileht. Aadresse, telefone, e-posti "
    "aadresse, juhi nime ega kommentaare ei salvestata — neil ei ole veergu. "
    "Nimekiri kirjeldab ekspordi kuupäeva seisu ja vananeb kuni järgmise "
    "impordini. Kirjete arv ei ole liikmete arvu näitaja."
)


def ensure_member_register_source(*, actor=None, correlation_id=None):
    """Register the manual member-register source.

    No staleness threshold, same reason as the composition source: the export
    is made by hand, there is no schedule to fall behind, and the page states
    the snapshot date instead of pretending to be current.
    """
    return ensure_data_source(
        slug=settings.MEMBERSHIP_REGISTER_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=REGISTER_SOURCE_NAME,
        source_type=SourceType.DOCUMENT,
        authority_tier=AuthorityTier.PRIMARY,
        expected_update_frequency=UpdateFrequency.IRREGULAR,
        stale_after_days=None,
        description=REGISTER_SOURCE_DESCRIPTION,
    )


DIRECTORY_SOURCE_NAME = "Koda.ee avaliku kataloogi kirjed"
DIRECTORY_SOURCE_DESCRIPTION = (
    "Koda.ee avalikus liikmekataloogis avaldatud profiilide registrikoodid ja "
    "profiililingid, kogutud samast loendist kui liikmete arv. Kirjete tase on "
    "vajalik nimekirja ja kataloogi võrdlemiseks kirjehaaval; nimesid, "
    "kontakte ega muid profiiliandmeid ei koguta. See on sama avaldamise "
    "mõõde mis kataloogi kirjete arv, mitte kolmas liikmete arvu määratlus."
)
# Same tolerance as the count: checked daily, one missed run is tolerable.
DIRECTORY_STALE_AFTER_DAYS = 2


def ensure_member_directory_source(*, actor=None, correlation_id=None):
    """Register the row-level public directory source.

    Separate from the count source on purpose: the count is a settled,
    aggregate-only series with its own guarantees, and a failure or a schema
    change on the row level must never be able to touch it.
    """
    return ensure_data_source(
        slug=settings.KODA_MEMBER_DIRECTORY_SOURCE_SLUG,
        actor=actor,
        correlation_id=correlation_id,
        name=DIRECTORY_SOURCE_NAME,
        source_type=SourceType.REGISTRY,
        expected_update_frequency=UpdateFrequency.DAILY,
        stale_after_days=DIRECTORY_STALE_AFTER_DAYS,
        description=DIRECTORY_SOURCE_DESCRIPTION,
    )
