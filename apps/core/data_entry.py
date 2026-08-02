"""Which parts of DashKoda a staff user types data into.

Two workflows now write domain data from a browser, and there will be more. Each
one is a purpose-built form living in its own app, because what makes a board
report valid has nothing to do with what makes a follower count valid. What they
share is the *boundary*: `/admin/`, guarded by the viewer PIN and then by Django
staff authentication, with every view wrapped in `admin.site.admin_view`.

This module is the index of those workflows and nothing else. It holds no
authentication, no permission rule and no data — adding a third module means
appending one entry here, not designing a second admin.

Entries carry **URL names**, not imports. `apps.core` therefore stays free of
any dependency on `membership` or `visibility`: the hub can list a workflow it
knows nothing about, and a module can be removed without breaking this file at
import time.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class DataEntryLink:
    """One action offered for a module."""

    url_name: str
    label: str
    description: str = ""

    @property
    def url(self) -> str | None:
        """The resolved address, or `None` when the route does not exist.

        A hub that raised on a missing route would take the whole admin down
        because one app was removed. It shows what it can reach instead.
        """
        try:
            return reverse(self.url_name)
        except NoReverseMatch:
            return None


@dataclass(frozen=True)
class DataEntryModule:
    """One manual-entry workflow, described for the hub."""

    key: str
    title: str
    description: str
    links: tuple[DataEntryLink, ...]

    @property
    def available_links(self) -> tuple[DataEntryLink, ...]:
        return tuple(link for link in self.links if link.url is not None)

    @property
    def is_available(self) -> bool:
        return bool(self.available_links)


DATA_ENTRY_MODULES: tuple[DataEntryModule, ...] = (
    DataEntryModule(
        key="membership",
        title="Liikmeskonna aruanne",
        description=(
            "Juhatusele esitatud liikmeskonna aruande näitajad: liikmete arv, tasunud "
            "liikmed, liikmemaksu laekumine, uued ja lahkunud liikmed. Iga aruanne "
            "sisestatakse ühe korra ja parandus loob uue versiooni."
        ),
        links=(
            DataEntryLink(
                url_name="membership-admin-report-new",
                label="Lisa liikmeskonna aruanne",
                description="Kaheastmeline vorm: kontrolli, seejärel kinnita.",
            ),
            DataEntryLink(
                url_name="admin:membership_internalmembershipobservation_changelist",
                label="Vaata sisestatud aruandeid",
                description="Kõik vaatlused koos parandustega, uuemad ees.",
            ),
        ),
    ),
    DataEntryModule(
        key="visibility",
        title="Kanalite statistika",
        description=(
            "Uudiskirjade ja sotsiaalmeedia auditooriumi suurus: e-Teataja, eNewsi ja "
            "e-Vestniku aktiivsed saajad ning Facebooki, LinkedIni, Instagrami ja "
            "YouTube’i jälgijad. Väärtused loetakse platvormi enda statistikast — "
            "DashKoda ei päri ühtegi platvormi."
        ),
        links=(
            DataEntryLink(
                url_name="visibility-admin-entry-new",
                label="Lisa kanalite näitajad",
                description="Kaheastmeline vorm: kontrolli, seejärel kinnita.",
            ),
            DataEntryLink(
                url_name="visibility-admin-entry-list",
                label="Vaata sisestuste ajalugu",
                description="Kõik sisestused koos parandustega, uuemad ees.",
            ),
        ),
    ),
)


def available_modules() -> tuple[DataEntryModule, ...]:
    return tuple(module for module in DATA_ENTRY_MODULES if module.is_available)
