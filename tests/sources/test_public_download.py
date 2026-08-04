"""The shared public sharing-link downloader: URL rules, transport, XLSX proof.

One hardened implementation in ``apps.sources.public_download`` serves both the
legal-work and the event-programme feed through thin wrappers. Every test here
runs against **both** wrappers, so a behaviour cannot hold for one feed and
silently not for the other, and the delegation tests at the bottom prove that a
future fix to the shared module is automatically a fix to each feed.

Every byte and every URL here is synthetic. No real sharing link is present in
this repository, in a fixture or in test output, and these tests assert that
neither the configured URL nor a redirect target can reach an error message or
a log record.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass
from types import ModuleType

import pytest
import requests

from apps.event_programme import public_download as event_programme_download
from apps.legal_work import public_download as legal_work_download
from apps.sources import public_download as shared
from apps.sources.public_download import XLSX_MIME_TYPE, PublicDownloadError

# A synthetic sharing URL shaped like the real one but pointing nowhere. The
# distinctive marker is what the secret-leak assertions look for.
SECRET_MARKER = "synthetic-not-a-real-share-token"
PUBLIC_URL = (
    f"https://synthetic-tenant-my.sharepoint.com/:x:/g/personal/synthetic/"
    f"{SECRET_MARKER}?e=synthetic"
)
REDIRECT_MARKER = "synthetic-signed-redirect-parameter"
REDIRECT_URL = f"https://synthetic-cdn.sharepoint.com/download?{REDIRECT_MARKER}=1"


@dataclass(frozen=True)
class Feed:
    """One domain wrapper and the configuration names it must use."""

    module: ModuleType
    url_setting: str
    max_bytes_setting: str
    logger_name: str
    filename: str


FEEDS = {
    "legal_work": Feed(
        module=legal_work_download,
        url_setting="OIGUSLOOME_PUBLIC_URL",
        max_bytes_setting="LEGAL_WORK_MAX_DOWNLOAD_BYTES",
        logger_name="dashkoda.legal_work.public_download",
        filename="dashkoda_oigusloome.xlsx",
    ),
    "event_programme": Feed(
        module=event_programme_download,
        url_setting="EVENT_PROGRAMME_PUBLIC_URL",
        max_bytes_setting="EVENT_PROGRAMME_MAX_DOWNLOAD_BYTES",
        logger_name="dashkoda.event_programme.public_download",
        filename="dashkoda_events.xlsx",
    ),
}


@pytest.fixture(params=sorted(FEEDS))
def feed(request) -> Feed:
    return FEEDS[request.param]


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
    monkeypatch.setattr(shared.time, "sleep", lambda _seconds: None)


@pytest.fixture
def destination(tmp_path, feed):
    return tmp_path / feed.filename


def fetch(feed, destination, *responses, url: str = PUBLIC_URL):
    session = FakeSession(*responses)
    return feed.module.download_public_workbook(destination, url=url, session=session), session


# -- configuration ------------------------------------------------------


def test_missing_configuration_fails_naming_only_the_variable(settings, feed):
    setattr(settings, feed.url_setting, "")

    with pytest.raises(feed.module.PublicUrlNotConfigured) as error:
        feed.module.load_public_url()

    assert feed.url_setting in str(error.value)


def test_a_blank_configuration_value_is_treated_as_missing(settings, feed):
    setattr(settings, feed.url_setting, "   ")

    with pytest.raises(feed.module.PublicUrlNotConfigured):
        feed.module.load_public_url()


def test_a_configured_url_is_returned_unchanged(settings, feed):
    setattr(settings, feed.url_setting, f"  {PUBLIC_URL}  ")

    assert feed.module.load_public_url() == PUBLIC_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://synthetic-tenant-my.sharepoint.com/:x:/g/personal/synthetic/abc",
        "ftp://synthetic-tenant-my.sharepoint.com/abc",
        "file:///etc/passwd",
    ],
)
def test_non_https_configuration_is_refused(settings, feed, url):
    setattr(settings, feed.url_setting, url)

    with pytest.raises(feed.module.PublicUrlNotConfigured):
        feed.module.load_public_url()


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
def test_local_hosts_and_ip_literals_are_refused(settings, feed, url):
    setattr(settings, feed.url_setting, url)

    with pytest.raises(feed.module.PublicUrlNotConfigured):
        feed.module.load_public_url()


@pytest.mark.parametrize(
    "url",
    [
        "https:///no-host",
        "not-a-url-at-all",
        "https://synthetic.example.invalid/workbook.xlsx",
        "https://sharepoint.com.attacker.invalid/workbook.xlsx",
    ],
)
def test_malformed_or_foreign_hosts_are_refused(settings, feed, url):
    setattr(settings, feed.url_setting, url)

    with pytest.raises(feed.module.PublicUrlNotConfigured):
        feed.module.load_public_url()


# -- the download parameter ---------------------------------------------


def test_the_download_parameter_is_added_and_other_parameters_survive(feed):
    result = feed.module.build_download_url(PUBLIC_URL)

    assert "download=1" in result
    assert "e=synthetic" in result


def test_an_existing_download_parameter_is_replaced_not_duplicated(feed):
    result = feed.module.build_download_url(f"{PUBLIC_URL}&download=0")

    assert result.count("download=") == 1
    assert "download=1" in result


def test_a_url_without_a_query_gains_the_parameter(feed):
    result = feed.module.build_download_url(
        "https://synthetic-tenant-my.sharepoint.com/:x:/g/personal/a/b"
    )

    assert result.endswith("?download=1")


def test_the_request_asks_for_the_download_form(feed, destination):
    _download, session = fetch(feed, destination, xlsx_response())

    assert "download=1" in session.requested[0]


# -- transport ----------------------------------------------------------


def test_a_valid_xlsx_mime_response_is_accepted(feed, destination):
    download, _session = fetch(feed, destination, xlsx_response())

    assert download.size_bytes == len(synthetic_xlsx_bytes())
    assert download.sha256 == hashlib.sha256(synthetic_xlsx_bytes()).hexdigest()
    assert download.content_type == XLSX_MIME_TYPE
    assert download.final_host == "synthetic-tenant-my.sharepoint.com"
    assert destination.is_file()


def test_an_octet_stream_xlsx_response_is_accepted(feed, destination):
    download, _session = fetch(
        feed, destination, xlsx_response(headers={"Content-Type": "application/octet-stream"})
    )

    assert download.content_type == "application/octet-stream"
    assert destination.is_file()


def test_a_missing_content_type_still_passes_on_structure_alone(feed, destination):
    body = synthetic_xlsx_bytes()
    response = FakeResponse(body=body, headers={})

    download, _session = fetch(feed, destination, response)

    assert download.content_type == ""
    assert download.size_bytes == len(body)


def test_an_https_redirect_is_followed(feed, destination):
    redirect = FakeResponse(status_code=302, headers={"Location": REDIRECT_URL})

    download, session = fetch(feed, destination, redirect, xlsx_response(url=REDIRECT_URL))

    assert len(session.requested) == 2
    assert download.final_host == "synthetic-cdn.sharepoint.com"


def test_a_redirect_to_plain_http_is_refused(feed, destination):
    redirect = FakeResponse(
        status_code=302, headers={"Location": "http://synthetic-cdn.sharepoint.com/download"}
    )

    with pytest.raises(PublicDownloadError, match="HTTPS"):
        fetch(feed, destination, redirect)

    assert not destination.exists()


def test_a_redirect_to_a_local_target_is_refused(feed, destination):
    redirect = FakeResponse(status_code=302, headers={"Location": "https://127.0.0.1/download"})

    with pytest.raises(PublicDownloadError):
        fetch(feed, destination, redirect)


def test_a_redirect_without_a_location_is_refused(feed, destination):
    with pytest.raises(PublicDownloadError, match="sihtkohta"):
        fetch(feed, destination, FakeResponse(status_code=302, headers={}))


def test_the_redirect_limit_is_enforced(feed, destination):
    hops = [
        FakeResponse(status_code=302, headers={"Location": REDIRECT_URL})
        for _ in range(shared.MAX_REDIRECTS + 1)
    ]

    with pytest.raises(PublicDownloadError, match="ümbersuunamisi"):
        fetch(feed, destination, *hops)


def test_a_timeout_is_reported_after_bounded_retries(feed, destination):
    attempts = [requests.Timeout("synthetic timeout")] * shared.MAX_ATTEMPTS

    with pytest.raises(PublicDownloadError, match="aegus"):
        fetch(feed, destination, *attempts)


def test_a_connection_error_is_reported_without_a_traceback(feed, destination):
    attempts = [requests.ConnectionError("synthetic refusal")] * shared.MAX_ATTEMPTS

    with pytest.raises(PublicDownloadError, match="ConnectionError"):
        fetch(feed, destination, *attempts)


def test_a_transient_failure_followed_by_success_is_retried(feed, destination):
    download, session = fetch(feed, destination, FakeResponse(status_code=503), xlsx_response())

    assert len(session.requested) == 2
    assert download.size_bytes > 0


def test_a_404_is_reported_as_an_unavailable_link(feed, destination):
    with pytest.raises(PublicDownloadError, match="404"):
        fetch(feed, destination, FakeResponse(status_code=404))


def test_a_403_is_reported_as_revoked_access(feed, destination):
    with pytest.raises(PublicDownloadError, match="403"):
        fetch(feed, destination, FakeResponse(status_code=403))


def test_throttling_is_retried_and_then_reported(feed, destination):
    responses = [
        FakeResponse(status_code=429, headers={"Retry-After": "1"})
        for _ in range(shared.MAX_ATTEMPTS)
    ]

    with pytest.raises(PublicDownloadError, match="429"):
        fetch(feed, destination, *responses)


def test_persistent_server_errors_are_reported_after_the_attempt_limit(feed, destination):
    responses = [FakeResponse(status_code=500) for _ in range(shared.MAX_ATTEMPTS)]

    with pytest.raises(PublicDownloadError, match="500"):
        fetch(feed, destination, *responses)


def test_no_authorization_header_or_cookie_is_sent(feed, destination):
    _download, session = fetch(feed, destination, xlsx_response())

    sent = {name.lower() for headers in session.headers_seen for name in headers}
    assert "authorization" not in sent
    assert "cookie" not in sent


def test_a_session_created_here_is_closed_so_no_cookie_jar_survives(feed, destination, settings):
    setattr(settings, feed.url_setting, PUBLIC_URL)
    created = []

    class Recorder(FakeSession):
        def __init__(self):
            super().__init__(xlsx_response())
            created.append(self)

    original = shared.requests.Session
    shared.requests.Session = Recorder
    try:
        feed.module.download_public_workbook(destination)
    finally:
        shared.requests.Session = original

    assert created and created[0].closed is True


# -- rejected responses -------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "text/plain", "application/json", "application/xml"],
)
def test_textual_responses_are_refused(feed, destination, content_type):
    body = b"<html>synthetic viewer page</html>"
    response = FakeResponse(body=body, headers={"Content-Type": f"{content_type}; charset=utf-8"})

    with pytest.raises(PublicDownloadError):
        fetch(feed, destination, response)

    assert not destination.exists()


def test_an_html_body_labelled_octet_stream_is_still_refused(feed, destination):
    """Content-Type is a signal; the XLSX structure is the proof."""
    response = FakeResponse(
        body=b"<html>synthetic viewer page</html>",
        headers={"Content-Type": "application/octet-stream"},
    )

    with pytest.raises(PublicDownloadError, match="ZIP"):
        fetch(feed, destination, response)

    assert not destination.exists()


def test_an_empty_response_is_refused(feed, destination):
    with pytest.raises(PublicDownloadError, match="tühi"):
        fetch(feed, destination, FakeResponse(body=b"", headers={"Content-Type": XLSX_MIME_TYPE}))


def test_an_oversized_declared_length_is_refused_before_downloading(feed, destination, settings):
    setattr(settings, feed.max_bytes_setting, 128)
    response = xlsx_response(headers={"Content-Length": "999999"})

    with pytest.raises(PublicDownloadError, match="liiga suur"):
        fetch(feed, destination, response)

    assert not destination.exists()


def test_an_undeclared_oversized_body_is_stopped_while_streaming(feed, destination, settings):
    setattr(settings, feed.max_bytes_setting, 32)
    body = synthetic_xlsx_bytes()
    assert len(body) > 32
    response = FakeResponse(body=body, headers={"Content-Type": XLSX_MIME_TYPE})

    with pytest.raises(PublicDownloadError, match="ületab"):
        fetch(feed, destination, response)

    assert not destination.exists()


def test_a_malformed_zip_is_refused(feed, destination):
    body = b"PK\x03\x04" + b"synthetic garbage that is not a zip directory"
    response = FakeResponse(body=body, headers={"Content-Type": XLSX_MIME_TYPE})

    with pytest.raises(PublicDownloadError, match="vigane"):
        fetch(feed, destination, response)

    assert not destination.exists()


def test_a_valid_zip_that_is_not_a_workbook_is_refused(feed, destination):
    response = FakeResponse(
        body=zip_without_xlsx_members(), headers={"Content-Type": XLSX_MIME_TYPE}
    )

    with pytest.raises(PublicDownloadError, match="xl/workbook.xml"):
        fetch(feed, destination, response)

    assert not destination.exists()


def test_a_broken_stream_removes_the_partial_file(feed, destination):
    response = FakeResponse(
        body=synthetic_xlsx_bytes(),
        headers={"Content-Type": XLSX_MIME_TYPE},
        error=requests.ConnectionError("synthetic mid-stream failure"),
    )

    with pytest.raises(PublicDownloadError):
        fetch(feed, destination, response)

    assert not destination.exists()


# -- secrecy ------------------------------------------------------------


def test_neither_the_source_nor_the_redirect_url_appears_in_an_error(feed, destination):
    # A retryable failure behind a redirect, so both URLs are in play. Each
    # attempt restarts the chain, hence one pair of responses per attempt.
    responses = []
    for _attempt in range(shared.MAX_ATTEMPTS):
        responses.append(FakeResponse(status_code=302, headers={"Location": REDIRECT_URL}))
        responses.append(FakeResponse(status_code=500, url=REDIRECT_URL))

    with pytest.raises(PublicDownloadError) as error:
        fetch(feed, destination, *responses)

    message = str(error.value)
    assert SECRET_MARKER not in message
    assert REDIRECT_MARKER not in message


def test_neither_url_reaches_the_log_on_a_redirected_download(feed, destination, caplog):
    redirect = FakeResponse(status_code=302, headers={"Location": REDIRECT_URL})

    with caplog.at_level(logging.DEBUG, logger=feed.logger_name):
        fetch(feed, destination, redirect, xlsx_response(url=REDIRECT_URL))

    assert caplog.text, "the completed download must be logged at all"
    assert SECRET_MARKER not in caplog.text
    assert REDIRECT_MARKER not in caplog.text


def test_neither_url_reaches_the_log_on_a_failure(feed, destination, caplog):
    with caplog.at_level(logging.DEBUG, logger=feed.logger_name):
        with pytest.raises(PublicDownloadError):
            fetch(feed, destination, FakeResponse(status_code=404))

    assert SECRET_MARKER not in caplog.text


def test_the_result_object_exposes_only_non_secret_facts(feed, destination):
    download, _session = fetch(feed, destination, xlsx_response())

    assert set(vars(download)) == {
        "path",
        "size_bytes",
        "sha256",
        "content_type",
        "final_host",
    }
    assert "?" not in download.final_host


# -- delegation ---------------------------------------------------------
#
# The wrappers must stay thin. If either feed grew its own transport or
# validation logic again, a future security fix to the shared module would
# silently stop applying to that feed — which is exactly the drift the
# extraction exists to prevent, so it is asserted here.


def test_both_wrappers_share_one_exception_hierarchy():
    assert legal_work_download.PublicDownloadError is shared.PublicDownloadError
    assert event_programme_download.PublicDownloadError is shared.PublicDownloadError
    assert legal_work_download.PublicUrlNotConfigured is shared.PublicUrlNotConfigured
    assert event_programme_download.PublicUrlNotConfigured is shared.PublicUrlNotConfigured
    assert legal_work_download.PublicDownload is shared.PublicDownload
    assert event_programme_download.PublicDownload is shared.PublicDownload


def test_each_wrapper_delegates_the_download_to_the_shared_module(feed, destination, monkeypatch):
    calls = []

    def recording_download(source, target, *, url=None, session=None):
        calls.append((source, target, url, session))
        return shared.PublicDownload(
            path=target, size_bytes=1, sha256="0" * 64, content_type=XLSX_MIME_TYPE, final_host="x"
        )

    monkeypatch.setattr(shared, "download_public_workbook", recording_download)

    feed.module.download_public_workbook(destination, url=PUBLIC_URL, session=None)

    assert calls == [(feed.module.WORKBOOK_SOURCE, destination, PUBLIC_URL, None)]


def test_each_wrapper_names_its_own_configuration(feed):
    assert feed.module.WORKBOOK_SOURCE.url_setting == feed.url_setting
    assert feed.module.WORKBOOK_SOURCE.max_bytes_setting == feed.max_bytes_setting
    assert feed.module.WORKBOOK_SOURCE.logger.name == feed.logger_name


def test_the_two_feeds_use_distinct_configuration():
    legal = legal_work_download.WORKBOOK_SOURCE
    events = event_programme_download.WORKBOOK_SOURCE
    assert legal.url_setting != events.url_setting
    assert legal.max_bytes_setting != events.max_bytes_setting
    assert legal.log_prefix != events.log_prefix
