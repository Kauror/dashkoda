"""The public sharing-link downloader: URL rules, transport and XLSX proof.

Every byte and every URL here is synthetic. The real sharing link is never
present in this repository, in a fixture or in test output, and these tests
assert that neither the configured URL nor a redirect target can reach an error
message or a log record.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile

import pytest
import requests

from apps.legal_work import public_download
from apps.legal_work.public_download import (
    XLSX_MIME_TYPE,
    PublicDownloadError,
    PublicUrlNotConfigured,
    build_download_url,
    download_public_workbook,
    load_public_url,
)

# A synthetic sharing URL shaped like the real one but pointing nowhere. The
# distinctive marker is what the secret-leak assertions look for.
SECRET_MARKER = "synthetic-not-a-real-share-token"
PUBLIC_URL = (
    f"https://synthetic-tenant-my.sharepoint.com/:x:/g/personal/synthetic/"
    f"{SECRET_MARKER}?e=synthetic"
)
REDIRECT_MARKER = "synthetic-signed-redirect-parameter"
REDIRECT_URL = f"https://synthetic-cdn.sharepoint.com/download?{REDIRECT_MARKER}=1"


# -- fakes --------------------------------------------------------------


def synthetic_xlsx_bytes() -> bytes:
    """A minimal but structurally valid XLSX package."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def zip_without_xlsx_members() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("synthetic.txt", "not a workbook")
    return buffer.getvalue()


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict | None = None,
        body: bytes = b"",
        url: str = PUBLIC_URL,
        error: Exception | None = None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self._body = body
        self._error = error
        self.closed = False

    def iter_content(self, chunk_size: int = 1):
        if self._error is not None:
            raise self._error
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    """Returns a scripted sequence of responses and records requested URLs."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requested: list[str] = []
        self.headers_seen: list[dict] = []
        self.closed = False

    def get(self, url, *, headers=None, timeout=None, allow_redirects=None, stream=None):
        self.requested.append(url)
        self.headers_seen.append(headers or {})
        assert allow_redirects is False, "redirects must be followed explicitly"
        assert stream is True, "the body must be streamed"
        assert isinstance(timeout, tuple) and len(timeout) == 2, (
            "both a connect and a read timeout must be explicit"
        )
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def xlsx_response(**overrides) -> FakeResponse:
    body = overrides.pop("body", synthetic_xlsx_bytes())
    headers = {"Content-Type": XLSX_MIME_TYPE, "Content-Length": str(len(body))}
    headers.update(overrides.pop("headers", {}))
    return FakeResponse(body=body, headers=headers, **overrides)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Retries must not slow the suite down; the delay itself is not the point."""
    monkeypatch.setattr(public_download.time, "sleep", lambda _seconds: None)


@pytest.fixture
def destination(tmp_path):
    return tmp_path / "dashkoda_oigusloome.xlsx"


def fetch(destination, *responses, url: str = PUBLIC_URL):
    session = FakeSession(*responses)
    return download_public_workbook(destination, url=url, session=session), session


# -- configuration ------------------------------------------------------


def test_missing_configuration_fails_naming_only_the_variable(settings):
    settings.OIGUSLOOME_PUBLIC_URL = ""

    with pytest.raises(PublicUrlNotConfigured) as error:
        load_public_url()

    assert "OIGUSLOOME_PUBLIC_URL" in str(error.value)


def test_a_blank_configuration_value_is_treated_as_missing(settings):
    settings.OIGUSLOOME_PUBLIC_URL = "   "

    with pytest.raises(PublicUrlNotConfigured):
        load_public_url()


def test_a_configured_url_is_returned_unchanged(settings):
    settings.OIGUSLOOME_PUBLIC_URL = f"  {PUBLIC_URL}  "

    assert load_public_url() == PUBLIC_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://synthetic-tenant-my.sharepoint.com/:x:/g/personal/synthetic/abc",
        "ftp://synthetic-tenant-my.sharepoint.com/abc",
        "file:///etc/passwd",
    ],
)
def test_non_https_configuration_is_refused(settings, url):
    settings.OIGUSLOOME_PUBLIC_URL = url

    with pytest.raises(PublicUrlNotConfigured):
        load_public_url()


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/workbook.xlsx",
        "https://127.0.0.1/workbook.xlsx",
        "https://10.0.0.5/workbook.xlsx",
        "https://[::1]/workbook.xlsx",
        "https://93.184.216.34/workbook.xlsx",
    ],
)
def test_local_hosts_and_ip_literals_are_refused(settings, url):
    settings.OIGUSLOOME_PUBLIC_URL = url

    with pytest.raises(PublicUrlNotConfigured):
        load_public_url()


@pytest.mark.parametrize(
    "url",
    [
        "https:///no-host",
        "not-a-url-at-all",
        "https://synthetic.example.invalid/workbook.xlsx",
        "https://sharepoint.com.attacker.invalid/workbook.xlsx",
    ],
)
def test_malformed_or_foreign_hosts_are_refused(settings, url):
    settings.OIGUSLOOME_PUBLIC_URL = url

    with pytest.raises(PublicUrlNotConfigured):
        load_public_url()


# -- the download parameter ---------------------------------------------


def test_the_download_parameter_is_added_and_other_parameters_survive():
    result = build_download_url(PUBLIC_URL)

    assert "download=1" in result
    assert "e=synthetic" in result


def test_an_existing_download_parameter_is_replaced_not_duplicated():
    result = build_download_url(f"{PUBLIC_URL}&download=0")

    assert result.count("download=") == 1
    assert "download=1" in result


def test_a_url_without_a_query_gains_the_parameter():
    result = build_download_url("https://synthetic-tenant-my.sharepoint.com/:x:/g/personal/a/b")

    assert result.endswith("?download=1")


def test_the_request_asks_for_the_download_form(destination):
    _download, session = fetch(destination, xlsx_response())

    assert "download=1" in session.requested[0]


# -- transport ----------------------------------------------------------


def test_a_valid_xlsx_mime_response_is_accepted(destination):
    download, _session = fetch(destination, xlsx_response())

    assert download.size_bytes == len(synthetic_xlsx_bytes())
    assert download.sha256 == hashlib.sha256(synthetic_xlsx_bytes()).hexdigest()
    assert download.content_type == XLSX_MIME_TYPE
    assert download.final_host == "synthetic-tenant-my.sharepoint.com"
    assert destination.is_file()


def test_an_octet_stream_xlsx_response_is_accepted(destination):
    download, _session = fetch(
        destination, xlsx_response(headers={"Content-Type": "application/octet-stream"})
    )

    assert download.content_type == "application/octet-stream"
    assert destination.is_file()


def test_a_missing_content_type_still_passes_on_structure_alone(destination):
    body = synthetic_xlsx_bytes()
    response = FakeResponse(body=body, headers={})

    download, _session = fetch(destination, response)

    assert download.content_type == ""
    assert download.size_bytes == len(body)


def test_an_https_redirect_is_followed(destination):
    redirect = FakeResponse(status_code=302, headers={"Location": REDIRECT_URL})

    download, session = fetch(destination, redirect, xlsx_response(url=REDIRECT_URL))

    assert len(session.requested) == 2
    assert download.final_host == "synthetic-cdn.sharepoint.com"


def test_a_redirect_to_plain_http_is_refused(destination):
    redirect = FakeResponse(
        status_code=302, headers={"Location": "http://synthetic-cdn.sharepoint.com/download"}
    )

    with pytest.raises(PublicDownloadError, match="HTTPS"):
        fetch(destination, redirect)

    assert not destination.exists()


def test_a_redirect_to_a_local_target_is_refused(destination):
    redirect = FakeResponse(status_code=302, headers={"Location": "https://127.0.0.1/download"})

    with pytest.raises(PublicDownloadError):
        fetch(destination, redirect)


def test_a_redirect_without_a_location_is_refused(destination):
    with pytest.raises(PublicDownloadError, match="sihtkohta"):
        fetch(destination, FakeResponse(status_code=302, headers={}))


def test_the_redirect_limit_is_enforced(destination):
    hops = [
        FakeResponse(status_code=302, headers={"Location": REDIRECT_URL})
        for _ in range(public_download.MAX_REDIRECTS + 1)
    ]

    with pytest.raises(PublicDownloadError, match="ümbersuunamisi"):
        fetch(destination, *hops)


def test_a_timeout_is_reported_after_bounded_retries(destination):
    attempts = [requests.Timeout("synthetic timeout")] * public_download.MAX_ATTEMPTS

    with pytest.raises(PublicDownloadError, match="aegus"):
        fetch(destination, *attempts)


def test_a_connection_error_is_reported_without_a_traceback(destination):
    attempts = [requests.ConnectionError("synthetic refusal")] * public_download.MAX_ATTEMPTS

    with pytest.raises(PublicDownloadError, match="ConnectionError"):
        fetch(destination, *attempts)


def test_a_transient_failure_followed_by_success_is_retried(destination):
    download, session = fetch(destination, FakeResponse(status_code=503), xlsx_response())

    assert len(session.requested) == 2
    assert download.size_bytes > 0


def test_a_404_is_reported_as_an_unavailable_link(destination):
    with pytest.raises(PublicDownloadError, match="404"):
        fetch(destination, FakeResponse(status_code=404))


def test_a_403_is_reported_as_revoked_access(destination):
    with pytest.raises(PublicDownloadError, match="403"):
        fetch(destination, FakeResponse(status_code=403))


def test_throttling_is_retried_and_then_reported(destination):
    responses = [
        FakeResponse(status_code=429, headers={"Retry-After": "1"})
        for _ in range(public_download.MAX_ATTEMPTS)
    ]

    with pytest.raises(PublicDownloadError, match="429"):
        fetch(destination, *responses)


def test_persistent_server_errors_are_reported_after_the_attempt_limit(destination):
    responses = [FakeResponse(status_code=500) for _ in range(public_download.MAX_ATTEMPTS)]

    with pytest.raises(PublicDownloadError, match="500"):
        fetch(destination, *responses)


def test_no_authorization_header_or_cookie_is_sent(destination):
    _download, session = fetch(destination, xlsx_response())

    sent = {name.lower() for headers in session.headers_seen for name in headers}
    assert "authorization" not in sent
    assert "cookie" not in sent


def test_a_session_created_here_is_closed_so_no_cookie_jar_survives(destination, settings):
    settings.OIGUSLOOME_PUBLIC_URL = PUBLIC_URL
    created = []

    class Recorder(FakeSession):
        def __init__(self):
            super().__init__(xlsx_response())
            created.append(self)

    original = public_download.requests.Session
    public_download.requests.Session = Recorder
    try:
        download_public_workbook(destination)
    finally:
        public_download.requests.Session = original

    assert created and created[0].closed is True


# -- rejected responses -------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "text/plain", "application/json", "application/xml"],
)
def test_textual_responses_are_refused(destination, content_type):
    body = b"<html>synthetic viewer page</html>"
    response = FakeResponse(body=body, headers={"Content-Type": f"{content_type}; charset=utf-8"})

    with pytest.raises(PublicDownloadError):
        fetch(destination, response)

    assert not destination.exists()


def test_an_html_body_labelled_octet_stream_is_still_refused(destination):
    """Content-Type is a signal; the XLSX structure is the proof."""
    response = FakeResponse(
        body=b"<html>synthetic viewer page</html>",
        headers={"Content-Type": "application/octet-stream"},
    )

    with pytest.raises(PublicDownloadError, match="ZIP"):
        fetch(destination, response)

    assert not destination.exists()


def test_an_empty_response_is_refused(destination):
    with pytest.raises(PublicDownloadError, match="tühi"):
        fetch(destination, FakeResponse(body=b"", headers={"Content-Type": XLSX_MIME_TYPE}))


def test_an_oversized_declared_length_is_refused_before_downloading(destination, settings):
    settings.LEGAL_WORK_MAX_DOWNLOAD_BYTES = 128
    response = xlsx_response(headers={"Content-Length": "999999"})

    with pytest.raises(PublicDownloadError, match="liiga suur"):
        fetch(destination, response)

    assert not destination.exists()


def test_an_undeclared_oversized_body_is_stopped_while_streaming(destination, settings):
    settings.LEGAL_WORK_MAX_DOWNLOAD_BYTES = 32
    body = synthetic_xlsx_bytes()
    assert len(body) > 32
    response = FakeResponse(body=body, headers={"Content-Type": XLSX_MIME_TYPE})

    with pytest.raises(PublicDownloadError, match="ületab"):
        fetch(destination, response)

    assert not destination.exists()


def test_a_malformed_zip_is_refused(destination):
    body = b"PK\x03\x04" + b"synthetic garbage that is not a zip directory"
    response = FakeResponse(body=body, headers={"Content-Type": XLSX_MIME_TYPE})

    with pytest.raises(PublicDownloadError, match="vigane"):
        fetch(destination, response)

    assert not destination.exists()


def test_a_valid_zip_that_is_not_a_workbook_is_refused(destination):
    response = FakeResponse(
        body=zip_without_xlsx_members(), headers={"Content-Type": XLSX_MIME_TYPE}
    )

    with pytest.raises(PublicDownloadError, match="xl/workbook.xml"):
        fetch(destination, response)

    assert not destination.exists()


def test_a_broken_stream_removes_the_partial_file(destination):
    response = FakeResponse(
        body=synthetic_xlsx_bytes(),
        headers={"Content-Type": XLSX_MIME_TYPE},
        error=requests.ConnectionError("synthetic mid-stream failure"),
    )

    with pytest.raises(PublicDownloadError):
        fetch(destination, response)

    assert not destination.exists()


# -- secrecy ------------------------------------------------------------


def test_neither_the_source_nor_the_redirect_url_appears_in_an_error(destination):
    # A retryable failure behind a redirect, so both URLs are in play. Each
    # attempt restarts the chain, hence one pair of responses per attempt.
    responses = []
    for _attempt in range(public_download.MAX_ATTEMPTS):
        responses.append(FakeResponse(status_code=302, headers={"Location": REDIRECT_URL}))
        responses.append(FakeResponse(status_code=500, url=REDIRECT_URL))

    with pytest.raises(PublicDownloadError) as error:
        fetch(destination, *responses)

    message = str(error.value)
    assert SECRET_MARKER not in message
    assert REDIRECT_MARKER not in message


def test_neither_url_reaches_the_log_on_a_redirected_download(destination, caplog):
    redirect = FakeResponse(status_code=302, headers={"Location": REDIRECT_URL})

    with caplog.at_level(logging.DEBUG, logger="dashkoda.legal_work.public_download"):
        fetch(destination, redirect, xlsx_response(url=REDIRECT_URL))

    assert caplog.text, "the completed download must be logged at all"
    assert SECRET_MARKER not in caplog.text
    assert REDIRECT_MARKER not in caplog.text


def test_neither_url_reaches_the_log_on_a_failure(destination, caplog):
    with caplog.at_level(logging.DEBUG, logger="dashkoda.legal_work.public_download"):
        with pytest.raises(PublicDownloadError):
            fetch(destination, FakeResponse(status_code=404))

    assert SECRET_MARKER not in caplog.text


def test_the_result_object_exposes_only_non_secret_facts(destination):
    download, _session = fetch(destination, xlsx_response())

    assert set(vars(download)) == {
        "path",
        "size_bytes",
        "sha256",
        "content_type",
        "final_host",
    }
    assert "?" not in download.final_host
