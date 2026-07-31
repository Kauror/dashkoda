"""The staff-only audience-figure entry form.

One purpose-built form rather than model admin editing, for the same reasons the
membership report has one: a published observation is immutable, a correction
must supersede rather than update, and every metric in a submission has to be
written in one transaction. A generic change form offers none of that, and would
offer "delete" as well.

The fields are **derived from the registry**, not listed again here. Adding a
metric to `registry.METRICS` therefore adds it to this form, to the preview and
to the confirmation page at once, and cannot add it to only two of the three.

Every metric field is optional. Nobody has every figure to hand on every day,
and a form that demanded them would invite someone to type a number they did not
read. Blank and `0` stay distinct all the way through: a metric absent from the
submission was not entered, while a `0` is a reading that says nobody is there.

Validation lives in `manual.build_preview`, not here, so the browser and a direct
POST to the confirmation step apply exactly the same rules and the two-stage flow
cannot be bypassed.
"""

from __future__ import annotations

from django import forms
from django.utils import timezone

from .manual import VisibilitySubmission
from .models import MAX_NOTE_LENGTH, VisibilityEntryBatch
from .registry import METRICS, NEWSLETTER_METRICS, SOCIAL_METRICS, spec_for

METRIC_PREFIX = "metric_"

# Thousands separators a person legitimately produces by copying a figure out of
# a platform's own interface. Estonian formatting groups with a space, and the
# platforms emit several width variants of one.
#
# Only whitespace is removed. `12,230` and `12.230` are **not** normalised: a
# comma or a period could be a decimal mark in some locale, and quietly guessing
# which would be exactly the silent coercion this form must not do. They stay
# invalid, and the user is told.
# Codepoints rather than literal characters: an invisible separator in source is
# unreviewable, and four of these render identically to an ordinary space.
THOUSANDS_SEPARATORS = (
    " ",  # U+0020 space
    chr(0x00A0),  # no-break space
    chr(0x202F),  # narrow no-break space
    chr(0x2009),  # thin space
    chr(0x2007),  # figure space
)


class CountField(forms.IntegerField):
    """A non-negative whole number that may be left blank.

    `min_value=0` refuses a negative. Blank stays blank rather than becoming
    zero, which is the distinction the rest of the application depends on.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("min_value", 0)
        super().__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, str):
            for separator in THOUSANDS_SEPARATORS:
                value = value.replace(separator, "")
        return super().to_python(value)


class VisibilityEntryForm(forms.Form):
    """One observation date, an optional note, and up to seven figures."""

    observation_date = forms.DateField(label="Vaatluse kuupäev")
    note = forms.CharField(
        required=False,
        max_length=MAX_NOTE_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Märkus",
        help_text=(
            "Vabatahtlik, kuni 500 tähemärki. Ära kirjuta siia isikuandmeid ega "
            "platvormide paroole."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for spec in METRICS:
            if not spec.manual_entry:
                continue
            self.fields[f"{METRIC_PREFIX}{spec.key}"] = CountField(
                label=spec.label, help_text=spec.definition
            )
        # A date the user has not chosen yet defaults to today, because that is
        # what "I just read this off the screen" means. It is still an ordinary
        # editable field: back-dating a reading someone took last week is normal.
        self.fields["observation_date"].initial = self.initial.get(
            "observation_date", timezone.localdate()
        )

    # ------------------------------------------------------------------
    # Grouping for the template, so it iterates named sections instead of
    # guessing at field names.
    # ------------------------------------------------------------------

    def _rows(self, keys):
        return [(spec_for(key), self[f"{METRIC_PREFIX}{key}"]) for key in keys]

    @property
    def newsletter_rows(self):
        return self._rows(NEWSLETTER_METRICS)

    @property
    def social_rows(self):
        return self._rows(SOCIAL_METRICS)

    def to_submission(self) -> VisibilitySubmission:
        """Turn a valid submission into the domain object. Writes nothing.

        Only the metrics that were actually filled in reach `values`. Collapsing
        a blank into a `0` here would destroy the distinction in the one place
        nothing downstream could recover it.
        """
        values = {}
        for spec in METRICS:
            if not spec.manual_entry:
                continue
            value = self.cleaned_data.get(f"{METRIC_PREFIX}{spec.key}")
            if value is not None:
                values[spec.key] = int(value)

        return VisibilitySubmission(
            observation_date=self.cleaned_data["observation_date"],
            values=values,
            note=self.cleaned_data.get("note") or "",
        )


def initial_from_batch(batch: VisibilityEntryBatch) -> dict:
    """Prefill a correction form from the submission being revised.

    Every figure the original carried is prefilled, so a correction that changes
    one number does not silently drop the six beside it — re-submitting an
    unchanged value is a no-op, but omitting it would leave the metric with no
    current reading for that date at all.
    """
    initial = {
        "observation_date": batch.observation_date,
        "note": batch.note,
    }
    for observation in batch.observations.all():
        initial[f"{METRIC_PREFIX}{observation.metric}"] = observation.value
    return initial
