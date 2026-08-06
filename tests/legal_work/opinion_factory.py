"""Synthetic opinion documents and source containers for the tests.

Every PDF here is built byte by byte from a template rather than copied from the
real catalogue. That is not squeamishness: the real documents are private
correspondence, they must never enter Git, and a test that depends on a 103 MB
archive is a test that cannot run in CI.

The generator produces genuinely valid PDFs — pypdf parses them, counts their
pages and extracts their text — so the tests exercise the real validator and the
real extractor rather than a stub. It can also produce the specific broken
shapes the catalogue has to survive: encrypted, structurally invalid, and
carrying an active action.
"""

from __future__ import annotations

import io
import zipfile

# A Latin-1 escape for the content stream. Estonian letters outside it are
# written through the standard WinAnsi encoding, which is what Helvetica uses.
_ESCAPES = str.maketrans({"(": r"\(", ")": r"\)", "\\": r"\\"})


def _escape(text: str) -> str:
    return text.translate(_ESCAPES)


def _content_stream(lines: list[str]) -> bytes:
    """A page's text, one line per `Td`, in a form pypdf can read back."""
    parts = ["BT", "/F1 11 Tf", "14 TL", "40 780 Td"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1", "replace")


def make_pdf(
    pages: list[list[str]] | None = None,
    *,
    version: str = "1.7",
    with_javascript: bool = False,
    with_launch_action: bool = False,
    with_link_annotation: bool = False,
    broken: bool = False,
    no_text: bool = False,
) -> bytes:
    """Build a valid single- or multi-page PDF containing the given lines."""
    if pages is None:
        pages = [["Test"]]
    if no_text:
        pages = [[] for _ in pages]

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    page_ids: list[int] = []
    content_ids: list[int] = []
    for lines in pages:
        stream = _content_stream(lines)
        content_ids.append(
            add(
                b"<< /Length "
                + str(len(stream)).encode()
                + b" >>\nstream\n"
                + stream
                + b"\nendstream"
            )
        )
        page_ids.append(0)  # placeholder, filled once the pages node exists

    pages_id = len(objects) + len(pages) + 1

    for index, content_id in enumerate(content_ids):
        annots = b""
        if with_link_annotation:
            annots = (
                b" /Annots [ << /Type /Annot /Subtype /Link /Rect [0 0 10 10] "
                b"/A << /S /URI /URI (https://www.koda.ee/) >> >> ]"
            )
        if with_launch_action:
            annots = (
                b" /Annots [ << /Type /Annot /Subtype /Link /Rect [0 0 10 10] "
                b"/A << /S /Launch /F (calc.exe) >> >> ]"
            )
        page_ids[index] = add(
            b"<< /Type /Page /Parent " + str(pages_id).encode() + b" 0 R "
            b"/MediaBox [0 0 595 842] /Resources << /Font << /F1 "
            + str(font_id).encode()
            + b" 0 R >> >> /Contents "
            + str(content_id).encode()
            + b" 0 R"
            + annots
            + b" >>"
        )

    kids = b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
    actual_pages_id = add(
        b"<< /Type /Pages /Kids [ " + kids + b" ] /Count " + str(len(page_ids)).encode() + b" >>"
    )

    catalog_body = b"<< /Type /Catalog /Pages " + str(actual_pages_id).encode() + b" 0 R"
    if with_javascript:
        js_id = add(b"<< /S /JavaScript /JS (app.alert\\(1\\);) >>")
        names_id = add(b"<< /JavaScript << /Names [ (a) " + str(js_id).encode() + b" 0 R ] >> >>")
        catalog_body += b" /Names " + str(names_id).encode() + b" 0 R"
    catalog_body += b" >>"
    catalog_id = add(catalog_body)

    out = bytearray()
    out += f"%PDF-{version}\n".encode()
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root "
        + str(catalog_id).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref_at).encode()
        + b"\n%%EOF\n"
    )

    if broken:
        # Keep the signature, destroy the structure. This is the shape a
        # truncated copy takes, and it must quarantine rather than crash.
        return bytes(out[: len(out) // 3])
    return bytes(out)


def make_encrypted_pdf() -> bytes:
    """A PDF that declares encryption. Refused rather than opened."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("synthetic-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def opinion_pdf(
    *,
    recipient: str = "Rahandusministeerium",
    our_date: str = "06.01.2026",
    our_reference: str = "4/1",
    their_date: str = "17.12.2025",
    their_reference: str = "1.1-10/4927-5",
    subject: str = "Arvamuse esitamine maksukorralduse seaduse eelnou kohta",
    body: str = "Eesti Kaubandus-Toostuskoda tanab voimaluse eest avaldada arvamust.",
    pages: int = 1,
) -> bytes:
    """A document shaped like a real Chamber opinion letter.

    The two-column reference block is flattened exactly as extraction flattens
    it, because that is the shape the header parser has to cope with.
    """
    first = [
        "ESTONIAN CHAMBER OF COMMERCE AND INDUSTRY",
        "TOOM-KOOLI 17, 10130 TALLINN / REG NO 80004733 / WWW.KODA.EE",
        f"{recipient} Teie {their_date}",
        f"nr {their_reference}",
        "Suur-Ameerika 1",
        f"10122 Tallinn Meie {our_date} nr {our_reference}",
        subject,
        "Lugupeetud minister",
        body,
    ]
    rest = [
        [f"Taiendav lehekulg {n} sisuga, mis on piisavalt pikk lugemiseks."]
        for n in range(2, pages + 1)
    ]
    return make_pdf([first, *rest])


def build_zip(entries: dict[str, bytes], *, path=None) -> bytes:
    """A ZIP of `name -> bytes`, written with UTF-8 names and fixed timestamps."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.flag_bits |= 0x800
            archive.writestr(info, payload)
    data = buffer.getvalue()
    if path is not None:
        path.write_bytes(data)
    return data


def build_hostile_zip(kind: str, *, path=None) -> bytes:
    """A ZIP breaking exactly one container rule, for the rejection tests."""
    buffer = io.BytesIO()
    payload = make_pdf()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if kind == "traversal":
            archive.writestr("../escaped.pdf", payload)
        elif kind == "absolute":
            archive.writestr("/etc/escaped.pdf", payload)
        elif kind == "drive":
            archive.writestr("C:/escaped.pdf", payload)
        elif kind == "symlink":
            info = zipfile.ZipInfo("link.pdf")
            info.external_attr = (0xA1FF) << 16
            archive.writestr(info, "/etc/passwd")
        elif kind == "nested":
            archive.writestr("inner.zip", build_zip({"a.pdf": payload}))
        elif kind == "duplicate":
            archive.writestr("same.pdf", payload)
            archive.writestr("same.pdf", payload)
        elif kind == "bomb":
            info = zipfile.ZipInfo("bomb.pdf", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, b"\0" * (5 * 1024 * 1024))
        else:  # pragma: no cover - a typo in a test is a test bug
            raise ValueError(f"unknown hostile zip kind: {kind}")
    data = buffer.getvalue()
    if path is not None:
        path.write_bytes(data)
    return data
