"""One fixed description of every visibility metric.

What a metric is called, which source owns it, where a reader can go to check
it, how long a reading stays believable and where it sits on the page are all
properties of the *metric*, not of whichever template happens to be rendering
it. Scattering them would let the overview and the Nähtavus page disagree about
the same number, which is precisely the class of bug this dashboard exists to
avoid.

The vocabulary itself lives in `models.VisibilityMetric`, because that is what
PostgreSQL stores. This module decorates it and adds nothing to it: a key that
is not a `VisibilityMetric` member cannot appear here, and `_check_registry()`
refuses to import if one ever does.

## Collected or typed

Two of the seven metrics' worth of sources are read automatically and the rest
are typed. `manual_entry` is what says which, and it is the single fact the
entry form, its preview and its confirmation page all derive from:

- the three **newsletter** figures come from Smaily through the scheduled
  `sync_smaily` command (`apps.visibility.smaily`);
- the **website** figures come from Google Analytics through `sync_ga4`;
- the four **social** figures are still read off each platform's own screen and
  typed in, because none of them offers a read-only aggregate the Chamber can
  reach without an app review.

## The profile links

Four fixed public URLs, held as application configuration. They are **not**
editable form values and there is no model field capable of holding a
user-supplied URL — AGENTS.md forbids any route, form or setting through which
someone could introduce one, and a display link is no exception to that.

They are display links only. Nothing in DashKoda fetches them: no page render,
no command, no scraper. `_check_registry()` asserts each one is HTTPS on an
exact expected host, so a typo becomes an import error rather than a link
pointing somewhere unintended.

The newsletter metrics deliberately have **no** link. The only URL there would
be a Smaily account login, and sending a board member to a login screen is not
provenance.

## Staleness

A social follower count is worth re-reading roughly monthly, so 45 days marks
one clearly missed cycle. The newsletter threshold of 90 days is now a backstop
rather than a cadence: the lists are read daily, so a newsletter figure that is
even a week old means the schedule has stopped. The three newsletters are
separate lists with separate audiences; they are never added together, because a
reader subscribed to two of them would be counted twice. Both are thresholds for
saying "vajab uuendamist" beside a figure — never for hiding it. An old number
is still the last thing anybody counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from urllib.parse import urlsplit

from .models import VisibilityMetric

# --------------------------------------------------------------------------
# Sources
#
# Slugs are constants here rather than settings because nothing outside
# `apps.visibility` reads them: they identify this app's own rows, not a
# deployment-configurable endpoint. Compare the Koda feed slugs, which live in
# settings because `apps/dashboard/freshness.py` also needs them.
# --------------------------------------------------------------------------

# Renamed from `manual-smaily-audience` when the newsletter figures stopped
# being typed. The slug is what the admin shows beside the source, and a slug
# reading `manual-` on an automated feed is exactly the kind of quiet untruth
# this dashboard exists to avoid. Migration `0006` moves the existing row.
SOURCE_SMAILY = "smaily-newsletter-audience"
SOURCE_FACEBOOK = "manual-facebook-followers"
SOURCE_LINKEDIN = "manual-linkedin-followers"
SOURCE_INSTAGRAM = "manual-instagram-followers"
SOURCE_YOUTUBE = "manual-youtube-subscribers"

# Registered so the GA4 seam has somewhere to publish into. No artifact and no
# import run exists for it, and none is created until real data arrives.
SOURCE_GA4 = "ga4-website-traffic"

SOURCE_NAMES: MappingProxyType[str, str] = MappingProxyType(
    {
        SOURCE_SMAILY: "Smaily uudiskirjade auditoorium",
        SOURCE_FACEBOOK: "Facebooki jälgijad",
        SOURCE_LINKEDIN: "LinkedIni jälgijad",
        SOURCE_INSTAGRAM: "Instagrami jälgijad",
        SOURCE_YOUTUBE: "YouTube’i tellijad",
        SOURCE_GA4: "Google Analytics veebistatistika",
    }
)

# Safe, fixed, non-secret artifact reference labels. A profile URL is never one
# of these: `SourceArtifact` refuses a reference containing `@` or `?`, and a
# reference is a provenance label rather than a place to keep a link.
ARTIFACT_REFERENCE_PREFIXES: MappingProxyType[str, str] = MappingProxyType(
    {
        SOURCE_SMAILY: "manual:smaily-audience",
        SOURCE_FACEBOOK: "manual:facebook-followers",
        SOURCE_LINKEDIN: "manual:linkedin-followers",
        SOURCE_INSTAGRAM: "manual:instagram-followers",
        SOURCE_YOUTUBE: "manual:youtube-subscribers",
    }
)

# --------------------------------------------------------------------------
# Fixed public profile links
# --------------------------------------------------------------------------

FACEBOOK_URL = "https://www.facebook.com/Kaubanduskoda"

# The Chamber's canonical LinkedIn page. The numeric form
# `linkedin.com/company/2877448` names the same organisation, but an
# unauthenticated request to it answers `999` — LinkedIn's anti-automation
# status — with no `Location` header, so no redirect to a canonical address can
# be observed from outside. The vanity address below *was* verified directly:
# it resolves to "Estonian Chamber of Commerce and Industry", Tallinn,
# koda.ee. It is therefore the stable public URL this application shows.
LINKEDIN_URL = "https://www.linkedin.com/company/ecci/"

INSTAGRAM_URL = "https://www.instagram.com/kaubanduskoda"
YOUTUBE_URL = "https://www.youtube.com/user/Kaubanduskoda"

# Exact hosts, not suffix matching. `evil-facebook.com` and
# `facebook.com.example.net` both end in something plausible.
EXPECTED_PROFILE_HOSTS: frozenset[str] = frozenset(
    {
        "www.facebook.com",
        "www.linkedin.com",
        "www.instagram.com",
        "www.youtube.com",
    }
)

# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------

SOCIAL_STALE_AFTER_DAYS = 45
NEWSLETTER_STALE_AFTER_DAYS = 90


@dataclass(frozen=True)
class VisibilityMetricSpec:
    """Everything fixed about one metric."""

    key: str
    label: str
    unit: str
    source_slug: str
    source_label: str
    display_order: int
    stale_after_days: int
    #: Empty for the newsletter metrics, which have no public page.
    profile_url: str = ""
    #: False means a metric only a collector may write. The three newsletter
    #: figures are collected from Smaily and are therefore absent from the entry
    #: form: leaving a box beside an automated feed invites somebody to type
    #: over it, and the dashboard would then hold two answers to one question.
    #: The four social figures remain manual.
    manual_entry: bool = True
    #: One sentence a viewer can read to know what was counted.
    definition: str = ""

    @property
    def has_profile_link(self) -> bool:
        return bool(self.profile_url)

    def is_stale_on(self, observation_date: date, *, today: date) -> bool:
        """Whether a reading taken on `observation_date` needs re-reading.

        A future-dated observation cannot exist (the form refuses one), but a
        negative age must not read as stale if one ever did.
        """
        return (today - observation_date).days > self.stale_after_days


NEWSLETTER_METRICS: tuple[str, ...] = (
    VisibilityMetric.NEWSLETTER_ETEATAJA,
    VisibilityMetric.NEWSLETTER_ENEWS,
    VisibilityMetric.NEWSLETTER_EVESTNIK,
)

SOCIAL_METRICS: tuple[str, ...] = (
    VisibilityMetric.FACEBOOK_FOLLOWERS,
    VisibilityMetric.LINKEDIN_FOLLOWERS,
    VisibilityMetric.INSTAGRAM_FOLLOWERS,
    VisibilityMetric.YOUTUBE_SUBSCRIBERS,
)


METRICS: tuple[VisibilityMetricSpec, ...] = (
    VisibilityMetricSpec(
        key=VisibilityMetric.NEWSLETTER_ETEATAJA,
        label="e-Teataja",
        unit="saajat",
        source_slug=SOURCE_SMAILY,
        source_label=SOURCE_NAMES[SOURCE_SMAILY],
        display_order=10,
        stale_after_days=NEWSLETTER_STALE_AFTER_DAYS,
        manual_entry=False,
        definition=(
            "e-Teataja nimekirjade tellijate arv Smailys: liikmete ja "
            "mitteliikmete nimekiri kokku. "
            "Ei ole saadetud ega kohale toimetatud kirjade arv."
        ),
    ),
    VisibilityMetricSpec(
        key=VisibilityMetric.NEWSLETTER_ENEWS,
        label="eNews",
        unit="saajat",
        source_slug=SOURCE_SMAILY,
        source_label=SOURCE_NAMES[SOURCE_SMAILY],
        display_order=20,
        stale_after_days=NEWSLETTER_STALE_AFTER_DAYS,
        manual_entry=False,
        definition=(
            "eNewsi nimekirja tellijate arv Smailys. "
            "Ei ole saadetud ega kohale toimetatud kirjade arv."
        ),
    ),
    VisibilityMetricSpec(
        key=VisibilityMetric.NEWSLETTER_EVESTNIK,
        label="e-Vestnik",
        unit="saajat",
        source_slug=SOURCE_SMAILY,
        source_label=SOURCE_NAMES[SOURCE_SMAILY],
        display_order=30,
        stale_after_days=NEWSLETTER_STALE_AFTER_DAYS,
        manual_entry=False,
        definition=(
            "e-Vestniku nimekirja tellijate arv Smailys. "
            "Ei ole saadetud ega kohale toimetatud kirjade arv."
        ),
    ),
    VisibilityMetricSpec(
        key=VisibilityMetric.FACEBOOK_FOLLOWERS,
        label="Facebooki jälgijad",
        unit="jälgijat",
        source_slug=SOURCE_FACEBOOK,
        source_label=SOURCE_NAMES[SOURCE_FACEBOOK],
        display_order=40,
        stale_after_days=SOCIAL_STALE_AFTER_DAYS,
        profile_url=FACEBOOK_URL,
        definition="Koja Facebooki lehe jälgijate arv lehe enda statistikas.",
    ),
    VisibilityMetricSpec(
        key=VisibilityMetric.LINKEDIN_FOLLOWERS,
        label="LinkedIni jälgijad",
        unit="jälgijat",
        source_slug=SOURCE_LINKEDIN,
        source_label=SOURCE_NAMES[SOURCE_LINKEDIN],
        display_order=50,
        stale_after_days=SOCIAL_STALE_AFTER_DAYS,
        profile_url=LINKEDIN_URL,
        definition="Koja LinkedIni lehe jälgijate arv lehe enda statistikas.",
    ),
    VisibilityMetricSpec(
        key=VisibilityMetric.INSTAGRAM_FOLLOWERS,
        label="Instagrami jälgijad",
        unit="jälgijat",
        source_slug=SOURCE_INSTAGRAM,
        source_label=SOURCE_NAMES[SOURCE_INSTAGRAM],
        display_order=60,
        stale_after_days=SOCIAL_STALE_AFTER_DAYS,
        profile_url=INSTAGRAM_URL,
        definition="Koja Instagrami konto jälgijate arv konto enda statistikas.",
    ),
    VisibilityMetricSpec(
        key=VisibilityMetric.YOUTUBE_SUBSCRIBERS,
        label="YouTube’i tellijad",
        unit="tellijat",
        source_slug=SOURCE_YOUTUBE,
        source_label=SOURCE_NAMES[SOURCE_YOUTUBE],
        display_order=70,
        stale_after_days=SOCIAL_STALE_AFTER_DAYS,
        profile_url=YOUTUBE_URL,
        definition="Koja YouTube’i kanali tellijate arv kanali enda statistikas.",
    ),
)


METRICS_BY_KEY: MappingProxyType[str, VisibilityMetricSpec] = MappingProxyType(
    {spec.key: spec for spec in METRICS}
)

#: Every source a submission can publish into, in display order.
#:
#: Deliberately *not* filtered by `manual_entry`. The two are different
#: questions: `manual_entry` says what the entry form offers a person, and this
#: says what `publish_submission` is able to write. A collected metric still
#: needs the second, because a published record is immutable and a correction to
#: a wrong collected figure has to be a superseding record rather than an edit —
#: which is the same service path a typed figure uses. No ordinary submission
#: carries a newsletter value, because the form has no box for one.
SUBMISSION_SOURCE_SLUGS: tuple[str, ...] = tuple(
    dict.fromkeys(spec.source_slug for spec in METRICS)
)


def manual_metrics(keys: tuple[str, ...]) -> tuple[str, ...]:
    """Those of `keys` a person may still type in, in the order given.

    The entry form, its preview and its confirmation page all iterate the
    registry rather than a list of their own, so a metric that becomes collected
    disappears from all three at once instead of leaving one of them offering a
    box that writes nothing.
    """
    return tuple(key for key in keys if METRICS_BY_KEY[key].manual_entry)


def spec_for(metric: str) -> VisibilityMetricSpec | None:
    return METRICS_BY_KEY.get(metric)


def metrics_for_source(source_slug: str) -> tuple[VisibilityMetricSpec, ...]:
    return tuple(spec for spec in METRICS if spec.source_slug == source_slug)


def ordered_keys() -> tuple[str, ...]:
    return tuple(spec.key for spec in sorted(METRICS, key=lambda spec: spec.display_order))


def _check_registry() -> None:
    """Refuse to import a registry that has drifted from the vocabulary.

    Three things can silently rot here — a metric added to the model and not
    described, a description for a metric that no longer exists, and a profile
    link edited into something that is not the intended host. All three become
    an immediate `ImportError` rather than a wrong page.
    """
    described = {spec.key for spec in METRICS}
    stored = set(VisibilityMetric.values)
    if described != stored:
        missing = sorted(stored - described)
        extra = sorted(described - stored)
        raise RuntimeError(
            f"Visibility registry disagrees with VisibilityMetric: missing={missing} extra={extra}"
        )
    if len(METRICS_BY_KEY) != len(METRICS):
        raise RuntimeError("Visibility registry contains a duplicate metric key.")

    orders = [spec.display_order for spec in METRICS]
    if len(set(orders)) != len(orders):
        raise RuntimeError("Visibility registry contains a duplicate display order.")

    for spec in METRICS:
        if spec.source_slug not in SOURCE_NAMES:
            raise RuntimeError(f"Unknown source slug for metric {spec.key}: {spec.source_slug}")
        if not spec.profile_url:
            continue
        parts = urlsplit(spec.profile_url)
        if parts.scheme != "https":
            raise RuntimeError(f"Profile URL for {spec.key} must be HTTPS.")
        if parts.hostname not in EXPECTED_PROFILE_HOSTS:
            raise RuntimeError(f"Unexpected profile host for {spec.key}: {parts.hostname}")
        if parts.query or parts.fragment or "@" in spec.profile_url:
            raise RuntimeError(f"Profile URL for {spec.key} must be a plain public address.")


_check_registry()
