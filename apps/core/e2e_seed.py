"""Synthetic vocabulary shared by the domain seed builders.

What lives here is what more than one domain needs in order to describe the same
kind of defect: the deliberately over-long Estonian strings that force
truncation, wrapping and horizontal scrolling, and the two timestamp freezes
that make a generated Office package byte-identical between runs.

What does not live here is any domain's content. Each app owns its own seed in
its own `e2e_seed.py`, and `seed_e2e_data` orchestrates them.
"""

from __future__ import annotations

import re
from pathlib import Path

# One long Estonian sentence, reused where a title has to be long enough to
# truncate. Long enough to overflow a narrow card, and unmistakably synthetic.
LONG_TITLE = (
    "Sünteetiline väga pikk pealkiri, mis on kirjutatud ainult selleks, "
    "et kontrollida kärpimist, murdmist ja horisontaalset kerimist kõige "
    "kitsamas vaates, ning see ei kirjelda ühtegi tegelikku Koja tegevust"
)
LONG_TOPIC = (
    "Sünteetiline õigusloome teema, mille pealkiri on tahtlikult äärmiselt pikk, "
    "et kontrollida tabeliveeru kärpimist ja seda, kas pikk seotud pealkiri koos "
    "peidetud lisamärkusega ajab lehe horisontaalselt kerima; ükski sõna siin ei "
    "puuduta tegelikku õigusloomet ega Koja seisukohti"
)
LONG_LOCATION = "Sünteetiline konverentsikeskus, sünteetiline suur saal, sünteetiline aadress 123"
LONG_CATEGORY = "Sünteetiline pikk kategooria nimetus"
# An XLSX carries the current time in two independent places, and both have to
# be frozen or the seed publishes a fresh snapshot on every run: the ZIP member
# headers, and the `dcterms:created` / `dcterms:modified` fields openpyxl writes
# into `docProps/core.xml`. Freezing only the first looks like it works, because
# two builds inside the same second still hash identically — it fails as soon as
# they straddle a second boundary. Both values are far in the future and
# obviously synthetic.
FIXED_ZIP_TIMESTAMP = (2099, 1, 1, 0, 0, 0)
FIXED_DOCUMENT_TIMESTAMP = "2099-01-01T00:00:00Z"


CORE_PROPERTIES_MEMBER = "docProps/core.xml"
_DOCUMENT_TIMESTAMP_PATTERN = re.compile(
    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)"
)


def freeze_package_timestamps(path: Path) -> None:
    """Rewrite the package so identical content produces identical bytes.

    Both timestamps are handled here rather than on the workbook object,
    because openpyxl re-stamps ``dcterms:modified`` with the current time while
    saving — assigning it beforehand looks like it works and silently does not.
    Doing it in one rewrite pass keeps a single mechanism for a single job.
    """
    import zipfile

    with zipfile.ZipFile(path) as source:
        members = [(info, source.read(info.filename)) for info in source.infolist()]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for info, payload in members:
            if info.filename == CORE_PROPERTIES_MEMBER:
                payload = _DOCUMENT_TIMESTAMP_PATTERN.sub(
                    rb"\g<1>" + FIXED_DOCUMENT_TIMESTAMP.encode("ascii") + rb"\g<2>",
                    payload,
                )
            frozen = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_TIMESTAMP)
            frozen.compress_type = zipfile.ZIP_DEFLATED
            frozen.external_attr = info.external_attr
            target.writestr(frozen, payload)
