"""Which newsletter a completed campaign belongs to.

Smaily has a `tags` field that would answer this exactly. On the Chamber's
account it is empty on **every** campaign — all two hundred read from the live
API carry `tags: []` — so it cannot be used, and pretending otherwise would
classify nothing while looking like it worked.

The subject line is written for readers and drifts. What does not drift is the
**template name**, because the Chamber names a template after the newsletter and
the date it went out:

    e-Teataja 4.08 mitteliikmed          →  e-Teataja, non-members
    e-Teataja 30.07.26 liikmed           →  e-Teataja, members
    e-Vestnik 25.06.26                   →  e-Vestnik
    E-News 07.05.26                      →  eNews
    Ürituste kalender 04.08.26           →  not a newsletter
    EEN 16.06.26 tööstus                 →  not a newsletter

Counted over the two hundred most recent completed campaigns: 72 e-Teataja, 9
e-Vestnik, 8 e-News, and 111 that are not any of the three — event calendars,
Enterprise Europe Network mailings, invitations and one-off letters. Those 111
are stored and left unclassified rather than forced into a newsletter, because a
campaign that is not an issue of e-Teataja must not appear in e-Teataja's
open rate.

## Members and non-members

e-Teataja goes out as two campaigns per issue, one to each list. The audience is
read from the same template name, and **`mitteliikmed` is tested before
`liikmed`** — it contains it, and the obvious ordering would file every
non-member send under members.

## Why classification is stored, not derived at read time

A template can be renamed or deleted after the fact. The classification is
resolved once, when the campaign is first catalogued, and written down; the
alternative is a historical chart that changes shape because somebody tidied up
a template list. Re-classification is possible but deliberate — it is not
something a page render does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

from .models import VisibilityMetric

#: Audience of one send, for the newsletter that has two.
AUDIENCE_MEMBERS = "liikmed"
AUDIENCE_NON_MEMBERS = "mitteliikmed"
AUDIENCE_UNKNOWN = ""


@dataclass(frozen=True)
class CampaignFamily:
    """One newsletter, and how to recognise a send of it.

    `pattern` is anchored at the start of the template name and requires a word
    boundary after the token, so `e-News` does not also match a template called
    `e-Newsletter kokkuvõte` — a real risk on an account where anybody can name
    a template.
    """

    metric: str
    label: str
    pattern: re.Pattern[str]


def _family_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(token)}\b", re.IGNORECASE)


FAMILIES: tuple[CampaignFamily, ...] = (
    CampaignFamily(
        metric=VisibilityMetric.NEWSLETTER_ETEATAJA,
        label="e-Teataja",
        pattern=_family_pattern("e-teataja"),
    ),
    CampaignFamily(
        metric=VisibilityMetric.NEWSLETTER_ENEWS,
        label="eNews",
        pattern=_family_pattern("e-news"),
    ),
    CampaignFamily(
        metric=VisibilityMetric.NEWSLETTER_EVESTNIK,
        label="e-Vestnik",
        pattern=_family_pattern("e-vestnik"),
    ),
)

FAMILIES_BY_METRIC: MappingProxyType[str, CampaignFamily] = MappingProxyType(
    {family.metric: family for family in FAMILIES}
)

#: `mitteliikmed` first: it contains `liikmed`, and the obvious ordering would
#: file every non-member send under members.
_AUDIENCE_TOKENS: tuple[tuple[str, str], ...] = (
    (AUDIENCE_NON_MEMBERS, AUDIENCE_NON_MEMBERS),
    (AUDIENCE_MEMBERS, AUDIENCE_MEMBERS),
)


@dataclass(frozen=True)
class Classification:
    """What a campaign was recognised as. Both fields may be empty."""

    metric: str = ""
    audience: str = AUDIENCE_UNKNOWN

    @property
    def is_newsletter(self) -> bool:
        return bool(self.metric)


def classify(template_name: str, *, subject: str = "") -> Classification:
    """Recognise a newsletter send from its template name.

    The subject is a **fallback only**, for a campaign whose template was
    deleted before it was first catalogued. It is matched with the same anchored
    patterns rather than a loose search, because a subject reading "Kutse
    e-Teataja lugejatele" is an invitation *to* e-Teataja readers and is not an
    issue of it.
    """
    for candidate in (template_name, subject):
        if not candidate:
            continue
        for family in FAMILIES:
            if family.pattern.search(candidate):
                return Classification(
                    metric=family.metric,
                    audience=_audience(candidate),
                )
    return Classification()


def _audience(template_name: str) -> str:
    lowered = template_name.casefold()
    for token, audience in _AUDIENCE_TOKENS:
        if token in lowered:
            return audience
    return AUDIENCE_UNKNOWN


def label_for(metric: str) -> str:
    family = FAMILIES_BY_METRIC.get(metric)
    return family.label if family is not None else ""


def _check_registry() -> None:
    """Refuse to import a classifier that has drifted from the vocabulary."""
    newsletter_metrics = {
        VisibilityMetric.NEWSLETTER_ETEATAJA,
        VisibilityMetric.NEWSLETTER_ENEWS,
        VisibilityMetric.NEWSLETTER_EVESTNIK,
    }
    described = {family.metric for family in FAMILIES}
    if described != newsletter_metrics:
        missing = sorted(newsletter_metrics - described)
        extra = sorted(described - newsletter_metrics)
        raise RuntimeError(
            f"Smaily campaign families disagree with VisibilityMetric: "
            f"missing={missing} extra={extra}"
        )
    if len(FAMILIES_BY_METRIC) != len(FAMILIES):
        raise RuntimeError("Smaily campaign families contain a duplicate metric.")


_check_registry()


__all__ = [
    "AUDIENCE_MEMBERS",
    "AUDIENCE_NON_MEMBERS",
    "AUDIENCE_UNKNOWN",
    "FAMILIES",
    "FAMILIES_BY_METRIC",
    "CampaignFamily",
    "Classification",
    "classify",
    "label_for",
]
