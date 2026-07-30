"""The staff-only board-report entry form.

One purpose-built form rather than model admin editing. The reason is not
cosmetic: an internal observation is immutable once published, its child rows
have to be written in the same transaction, and a correction must supersede
rather than update. A generic change form offers none of that and would offer
"delete" as well.

Every field except the date may be left blank. Older board reports genuinely
omit figures, and a form that demands them invites someone to type a number
nobody reported. Blank and `0` stay distinct all the way through: a blank month
is absent from the submitted grid, while a `0` is an entered value that means
nobody joined.

Validation lives in `manual.build_preview`, not here, so the browser and the
server apply exactly the same rules and the form cannot be bypassed by posting
straight to the confirmation step.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms

from .manual import ManualReport
from .models import (
    InternalMembershipObservation,
    QualityStatus,
    RemovalReasonKey,
    SizeBand,
)
from .quality import MetricFacts

MONTHS: tuple[tuple[int, str], ...] = (
    (1, "Jaanuar"),
    (2, "Veebruar"),
    (3, "Märts"),
    (4, "Aprill"),
    (5, "Mai"),
    (6, "Juuni"),
    (7, "Juuli"),
    (8, "August"),
    (9, "September"),
    (10, "Oktoober"),
    (11, "November"),
    (12, "Detsember"),
)

# The three categories the board reports use. `other` is entered separately with
# its own label so it is never folded into one of these.
KNOWN_REASON_KEYS: tuple[str, ...] = (
    RemovalReasonKey.DISSOLVED,
    RemovalReasonKey.VOLUNTARY_NO_VALUE,
    RemovalReasonKey.VOLUNTARY_FINANCIAL,
)

MONTH_PREFIX = "month_"
JOINED_PREFIX = "joined_"
REMOVED_PREFIX = "removed_"
REASON_PREFIX = "reason_"


def _count_field(label: str) -> forms.IntegerField:
    """A non-negative count that may be left blank.

    `min_value=0` is what refuses a negative; blank stays blank rather than
    becoming zero.
    """
    return forms.IntegerField(required=False, min_value=0, label=label)


def _money_field(label: str) -> forms.DecimalField:
    return forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=14,
        decimal_places=2,
        label=label,
    )


class InternalMembershipReportForm(forms.Form):
    """Sections A–E of one board report."""

    # Section A — report identity.
    observation_date = forms.DateField(label="Vaatluse kuupäev")
    reported_year = forms.IntegerField(
        required=False, min_value=1900, max_value=2200, label="Aruandeaasta"
    )
    document_title = forms.CharField(
        required=False, max_length=200, label="Dokumendi pealkiri või failinimi"
    )
    source_note = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Märkus",
    )
    supersedes = forms.ModelChoiceField(
        queryset=InternalMembershipObservation.objects.none(),
        required=False,
        label="Parandatav vaatlus",
    )
    confirm_date_change = forms.BooleanField(
        required=False,
        label="Kinnitan, et parandus salvestatakse teisele kuupäevale",
    )

    # Section B — main reported facts.
    total_members = _count_field("Liikmeid kokku")
    paid_members = _count_field("Tasunud liikmeid")
    membership_fees_received_eur = _money_field("Laekunud liikmemaks (EUR)")
    membership_fee_budget_eur = _money_field("Liikmemaksu aastaeelarve (EUR)")
    # Matches the model's four places. A correction prefills the figure the
    # original reported, and some historical reports state four — a form that
    # only accepted two would refuse to re-save a value it had just shown.
    membership_fee_collection_pct_reported = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=8,
        decimal_places=4,
        label="Raporteeritud laekumise protsent",
    )
    new_members_ytd = _count_field("Uusi liikmeid aasta algusest")
    suspended_members = _count_field("Peatatud liikmeid")
    removed_members_ytd = _count_field("Väljaarvatuid aasta algusest")

    # Section C — monthly new members.
    monthly_year = forms.IntegerField(
        required=False, min_value=1900, max_value=2200, label="Kuude aruandeaasta"
    )

    # Section D/E completeness ticks. Totals are only cross-checked when the
    # user states the table is complete, because a partial table is normal.
    size_table_complete = forms.BooleanField(
        required=False, label="Suurusklasside tabel on täielik"
    )
    other_reason_label = forms.CharField(
        required=False, max_length=200, label="Muu põhjus (kirjeldus)"
    )
    other_reason_count = _count_field("Muu põhjus (liikmeid)")
    reasons_complete = forms.BooleanField(required=False, label="Põhjuste tabel on täielik")

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source

        # Only published, non-superseded observations can be corrected, and only
        # from this source. Offering anything else would let the form point a
        # correction at a row it must not touch.
        if source is not None:
            self.fields["supersedes"].queryset = InternalMembershipObservation.objects.filter(
                source=source
            ).exclude(quality_status=QualityStatus.SUPERSEDED)

        for month, label in MONTHS:
            self.fields[f"{MONTH_PREFIX}{month}"] = _count_field(label)
        for band in SizeBand.values:
            band_label = SizeBand(band).label
            self.fields[f"{JOINED_PREFIX}{band}"] = _count_field(f"Liitunud: {band_label}")
            self.fields[f"{REMOVED_PREFIX}{band}"] = _count_field(f"Lahkunud: {band_label}")
        for key in KNOWN_REASON_KEYS:
            self.fields[f"{REASON_PREFIX}{key}"] = _count_field(RemovalReasonKey(key).label)

    # ------------------------------------------------------------------
    # Grouping helpers for the template. Keeping these here means the
    # template iterates named groups instead of guessing at field names.
    # ------------------------------------------------------------------

    #: Section A, in the order the form presents it.
    IDENTITY_FIELDS = (
        "observation_date",
        "reported_year",
        "document_title",
        "source_note",
        "supersedes",
        "confirm_date_change",
    )

    #: Section B.
    FACT_FIELDS = (
        "total_members",
        "paid_members",
        "membership_fees_received_eur",
        "membership_fee_budget_eur",
        "membership_fee_collection_pct_reported",
        "new_members_ytd",
        "suspended_members",
        "removed_members_ytd",
    )

    @property
    def identity_fields(self):
        return [self[name] for name in self.IDENTITY_FIELDS]

    @property
    def fact_fields(self):
        return [self[name] for name in self.FACT_FIELDS]

    @property
    def month_fields(self):
        return [(label, self[f"{MONTH_PREFIX}{month}"]) for month, label in MONTHS]

    @property
    def size_band_rows(self):
        return [
            (
                SizeBand(band).label,
                self[f"{JOINED_PREFIX}{band}"],
                self[f"{REMOVED_PREFIX}{band}"],
                band == SizeBand.SUPPORTER,
            )
            for band in SizeBand.values
        ]

    @property
    def reason_fields(self):
        return [self[f"{REASON_PREFIX}{key}"] for key in KNOWN_REASON_KEYS]

    def _entered(self, prefix: str, keys) -> dict:
        """Collect only the fields that were actually filled in.

        A key missing from the result was left blank. A key present with `0` was
        entered as zero. Collapsing the two would destroy the distinction the
        rest of the application depends on.
        """
        collected = {}
        for key in keys:
            value = self.cleaned_data.get(f"{prefix}{key}")
            if value is not None:
                collected[key] = value
        return collected

    def to_report(self) -> ManualReport:
        """Turn a valid submission into the domain object. No database writes."""
        data = self.cleaned_data
        observation_date = data["observation_date"]
        supersedes = data.get("supersedes")

        monthly = {
            month: data[f"{MONTH_PREFIX}{month}"]
            for month, _label in MONTHS
            if data.get(f"{MONTH_PREFIX}{month}") is not None
        }

        return ManualReport(
            observation_date=observation_date,
            reported_year=data.get("reported_year") or observation_date.year,
            document_title=data.get("document_title") or "",
            source_note=data.get("source_note") or "",
            facts=MetricFacts(
                total_members=data.get("total_members"),
                paid_members=data.get("paid_members"),
                membership_fees_received_eur=data.get("membership_fees_received_eur"),
                membership_fee_budget_eur=data.get("membership_fee_budget_eur"),
                membership_fee_collection_pct_reported=data.get(
                    "membership_fee_collection_pct_reported"
                ),
                new_members_ytd=data.get("new_members_ytd"),
                suspended_members=data.get("suspended_members"),
                removed_members_ytd=data.get("removed_members_ytd"),
            ),
            monthly_year=data.get("monthly_year") or (observation_date.year if monthly else None),
            monthly_new_members=monthly,
            joined_by_band=self._entered(JOINED_PREFIX, SizeBand.values),
            removed_by_band=self._entered(REMOVED_PREFIX, SizeBand.values),
            size_table_complete=bool(data.get("size_table_complete")),
            removal_reasons=self._entered(REASON_PREFIX, KNOWN_REASON_KEYS),
            other_reason_label=data.get("other_reason_label") or "",
            other_reason_count=data.get("other_reason_count"),
            reasons_complete=bool(data.get("reasons_complete")),
            supersedes_id=supersedes.pk if supersedes else None,
            confirm_date_change=bool(data.get("confirm_date_change")),
        )


def initial_from_observation(observation: InternalMembershipObservation) -> dict:
    """Prefill a correction form from the observation being replaced.

    The child rows are prefilled too, so a correction that only changes one
    figure does not silently drop the distribution the original carried.
    """
    initial = {
        "observation_date": observation.observation_date,
        "reported_year": observation.reported_year,
        "document_title": (
            observation.source_document.document_title if observation.source_document else ""
        ),
        "source_note": observation.source_note,
        "supersedes": observation.pk,
        "total_members": observation.total_members,
        "paid_members": observation.paid_members,
        "membership_fees_received_eur": observation.membership_fees_received_eur,
        "membership_fee_budget_eur": observation.membership_fee_budget_eur,
        "membership_fee_collection_pct_reported": (
            observation.membership_fee_collection_pct_reported
        ),
        "new_members_ytd": observation.new_members_ytd,
        "suspended_members": observation.suspended_members,
        "removed_members_ytd": observation.removed_members_ytd,
    }
    for movement in observation.size_movements.all():
        prefix = JOINED_PREFIX if movement.direction == "joined" else REMOVED_PREFIX
        initial[f"{prefix}{movement.size_band_key}"] = movement.member_count
    for reason in observation.removal_reasons.all():
        if reason.reason_key in KNOWN_REASON_KEYS:
            initial[f"{REASON_PREFIX}{reason.reason_key}"] = reason.member_count
        elif reason.reason_key == RemovalReasonKey.OTHER:
            initial["other_reason_label"] = reason.reason_label_raw
            initial["other_reason_count"] = reason.member_count
    return initial
