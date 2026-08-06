"""Which archive entries are worth spending a detail request on.

Hydration has a budget and the archive has eleven hundred entries, so something
has to decide what gets read first. This is that something, and it is
deliberately cheap and deliberately crude: it works from the listing title and
summary alone, because that is all an unhydrated entry has.

It is a **prefilter, not a matcher**. Its job is to avoid spending the budget on
entries that obviously cannot be about any live legal record; it makes no
decision that reaches a viewer. Anything it shortlists still has to survive the
real archive matcher on its hydrated text.

The archive card carries no year, so this cannot prefer entries by date. It
prefers them by *word overlap with the records that actually need a link* — the
consultation-eligible ones the current matcher did not already match.
"""

from __future__ import annotations

from django.conf import settings

from .text_normalisation import significant_tokens

# How many archive entries one legal record may pull into the shortlist. Small,
# because the budget is shared across every record that still needs a link.
MAX_SHORTLIST_PER_RECORD = 12

# A record and an entry must share at least this many discriminating words
# before the entry is worth a request. One shared uncommon noun is the whole
# signal a listing headline can offer.
MIN_SHARED_SIGNIFICANT_TOKENS = 1


def shortlist_archive_urls(entries, legal_items=None) -> set[str]:
    """URLs worth hydrating first, given who still needs a link.

    Searches the **entire** archive index — every year of it. Consultation
    eligibility is about a record's status, not about when its consultation ran,
    so restricting the search by age would make an eligible record's link depend
    on how long ago the Chamber was asked. That was a real defect and this is
    where it is fixed.

    Age cannot be used here in any case: archive listing cards carry no year, so
    before hydration there is no date to filter on. The shortlist works from
    title and summary alone, which is exactly what an unread entry offers.

    Deterministic throughout: candidates are ranked by how many discriminating
    words they share with the record, ties broken by the archive's own order, so
    two runs over the same inputs shortlist the same pages.

    With no legal records supplied — a backfill before any matching has run —
    this returns an empty set and hydration falls back to recent-window
    coverage, which is the right default for a fresh install.
    """
    if not legal_items:
        return set()

    wanted: set[str] = set()
    # Tokenised once per entry rather than once per (record, entry) pair: the
    # index runs to a thousand entries and the eligible population to dozens.
    entry_tokens = [
        (entry, significant_tokens(f"{entry.title} {entry.listing_summary}")) for entry in entries
    ]

    for item in legal_items:
        record_tokens = significant_tokens(f"{item.topic} {item.act_type}")
        if not record_tokens:
            continue
        scored = []
        for entry, tokens in entry_tokens:
            shared = len(record_tokens & tokens)
            if shared >= MIN_SHARED_SIGNIFICANT_TOKENS:
                scored.append((-shared, entry.source_order, entry.canonical_url))
        scored.sort()
        wanted.update(url for _, _, url in scored[:MAX_SHORTLIST_PER_RECORD])
    return wanted


def eligible_records_needing_a_link(legal_snapshot, current_match_snapshot):
    """Consultation-eligible records the current matcher did not match.

    These are exactly the records an archive link could help, so they are what
    the shortlist is built around.
    """
    from .consultation import consultation_eligible_items
    from .models import LegalWorkItem, MatchDecision

    matched_ids = (
        current_match_snapshot.matches.filter(decision=MatchDecision.MATCHED).values_list(
            "legal_item_id", flat=True
        )
        if current_match_snapshot
        else ()
    )
    return list(
        consultation_eligible_items(LegalWorkItem.objects.filter(snapshot=legal_snapshot))
        .exclude(pk__in=list(matched_ids))
        .order_by("pk")[: settings.KODA_ARCHIVE_MAX_ITEMS]
    )
