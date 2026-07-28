from django import forms
from django.core.validators import RegexValidator


class ViewerLoginForm(forms.Form):
    pin = forms.CharField(
        label="PIN-kood",
        max_length=4,
        min_length=4,
        strip=True,
        validators=[
            RegexValidator(
                regex=r"^\d{4}$",
                message="Sisesta neljakohaline PIN-kood.",
            )
        ],
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "inputmode": "numeric",
                "pattern": "[0-9]{4}",
            },
            render_value=False,
        ),
    )
