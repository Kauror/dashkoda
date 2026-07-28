"""Microsoft Graph collector.

Every test drives a fake transport. Nothing here ever reaches Microsoft, and no
real tenant, client or secret exists in this repository.
"""

import base64
import datetime as dt

import pytest
import requests

from apps.legal_work import graph as graph_module
from apps.legal_work.graph import (
    GraphClient,
    GraphError,
    GraphNotConfigured,
    GraphSettings,
    encode_sharing_url,
    load_graph_settings,
)

SYNTHETIC_TOKEN = "synthetic-access-token-value"


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Retries must not make the suite slow."""
    monkeypatch.setattr(graph_module.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def fake_msal(monkeypatch):
    """Return a synthetic token without contacting the identity provider."""

    class Application:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def acquire_token_for_client(self, scopes):
            return {"access_token": SYNTHETIC_TOKEN}

    monkeypatch.setattr(
        GraphClient,
        "_acquire_token",
        lambda self: SYNTHETIC_TOKEN,
    )
    return Application


def settings_for(**overrides) -> GraphSettings:
    values = {
        "tenant_id": "synthetic-tenant",
        "client_id": "synthetic-client",
        "client_secret": "synthetic-secret",
        "drive_id": "synthetic-drive",
        "item_id": "synthetic-item",
        "timeout_seconds": 1.0,
        "max_attempts": 3,
        "max_download_bytes": 1024,
    }
    values.update(overrides)
    return GraphSettings(**values)


class FakeResponse:
    def __init__(self, status_code=200, *, payload=None, headers=None, body=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    """Records requests so tests can assert on headers and URLs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self._next()

    def get(self, url, **kwargs):
        self.requests.append({"method": "GET", "url": url, **kwargs})
        return self._next()

    def _next(self):
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


ITEM_PAYLOAD = {
    "id": "synthetic-item",
    "name": "dashkoda_oigusloome.xlsx",
    "size": 4,
    "cTag": "synthetic-ctag",
    "lastModifiedDateTime": "2099-03-01T06:00:00Z",
    "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "parentReference": {"driveId": "synthetic-drive"},
}


# -- configuration ------------------------------------------------------


def test_missing_configuration_is_reported_with_the_missing_names(settings):
    settings.MS_GRAPH_CLIENT_SECRET = ""
    settings.OIGUSLOOME_ITEM_ID = ""

    with pytest.raises(GraphNotConfigured) as error:
        load_graph_settings()

    message = str(error.value)
    assert "MS_GRAPH_CLIENT_SECRET" in message
    assert "OIGUSLOOME_ITEM_ID" in message


def test_resolver_does_not_require_the_item_id_yet(settings):
    settings.OIGUSLOOME_ITEM_ID = ""

    config = load_graph_settings(require_item=False)

    assert config.tenant_id == "synthetic-tenant"


# -- sharing URL --------------------------------------------------------


def test_sharing_url_is_encoded_exactly_as_microsoft_documents():
    url = "https://example.invalid/:x:/g/personal/synthetic/Abc+Def/Ghi?e=1"

    token = encode_sharing_url(url)

    assert token.startswith("u!")
    assert "=" not in token
    assert "/" not in token[2:]
    assert "+" not in token
    restored = token[2:].replace("_", "/").replace("-", "+")
    padded = restored + "=" * (-len(restored) % 4)
    assert base64.b64decode(padded).decode() == url


# -- metadata -----------------------------------------------------------


def test_metadata_returns_non_secret_fields_only():
    session = FakeSession([FakeResponse(payload=ITEM_PAYLOAD)])
    client = GraphClient(settings_for(), session=session)

    remote = client.get_item_metadata()

    assert remote.item_id == "synthetic-item"
    assert remote.etag == "synthetic-ctag"
    assert remote.size_bytes == 4
    assert remote.modified_at == dt.datetime(2099, 3, 1, 6, 0, tzinfo=dt.UTC)


def test_an_unchanged_remote_file_is_recognised_by_etag():
    session = FakeSession([FakeResponse(payload=ITEM_PAYLOAD)])
    remote = GraphClient(settings_for(), session=session).get_item_metadata()

    assert remote.matches(etag="synthetic-ctag", modified_at=None, size_bytes=None) is True
    assert remote.matches(etag="different", modified_at=None, size_bytes=None) is False


def test_a_folder_is_refused():
    payload = {key: value for key, value in ITEM_PAYLOAD.items() if key != "file"}
    session = FakeSession([FakeResponse(payload=payload)])

    with pytest.raises(GraphError, match="ei ole fail"):
        GraphClient(settings_for(), session=session).get_item_metadata()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "keeldus"), (403, "keeldus"), (404, "ei leidnud")],
)
def test_authorization_and_not_found_errors_are_readable(status, expected):
    session = FakeSession([FakeResponse(status)])

    with pytest.raises(GraphError, match=expected):
        GraphClient(settings_for(), session=session).get_item_metadata()


# -- retries ------------------------------------------------------------


def test_throttling_is_retried_and_honours_retry_after():
    session = FakeSession(
        [
            FakeResponse(429, headers={"Retry-After": "1"}),
            FakeResponse(payload=ITEM_PAYLOAD),
        ]
    )

    remote = GraphClient(settings_for(), session=session).get_item_metadata()

    assert remote.item_id == "synthetic-item"
    assert len(session.requests) == 2


def test_server_errors_are_retried_but_bounded():
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])

    with pytest.raises(GraphError, match="503"):
        GraphClient(settings_for(max_attempts=3), session=session).get_item_metadata()

    assert len(session.requests) == 3


def test_a_timeout_is_reported_without_internal_detail():
    session = FakeSession([requests.Timeout("boom"), requests.Timeout("boom")])

    with pytest.raises(GraphError, match="aegus"):
        GraphClient(settings_for(max_attempts=2), session=session).get_item_metadata()


# -- download -----------------------------------------------------------


def test_download_follows_the_redirect_without_forwarding_the_bearer_token(tmp_path):
    signed_url = "https://files.invalid/synthetic-signed-url"
    session = FakeSession(
        [
            FakeResponse(302, headers={"Location": signed_url}),
            FakeResponse(200, body=b"PK\x03\x04synthetic"),
        ]
    )
    destination = tmp_path / "workbook.xlsx"

    written = GraphClient(settings_for(), session=session).download_to(destination)

    assert written == len(b"PK\x03\x04synthetic")
    redirect_request = session.requests[1]
    assert redirect_request["url"] == signed_url
    # The pre-authenticated URL is already signed; sending the token there
    # would leak it to a host that does not need it.
    assert "headers" not in redirect_request or "Authorization" not in redirect_request.get(
        "headers", {}
    )


def test_an_oversized_download_is_refused_while_streaming(tmp_path):
    session = FakeSession([FakeResponse(200, body=b"x" * 5000)])
    destination = tmp_path / "workbook.xlsx"

    with pytest.raises(GraphError, match="suurus"):
        GraphClient(settings_for(max_download_bytes=100), session=session).download_to(destination)


def test_a_declared_oversized_download_is_refused_before_reading(tmp_path):
    session = FakeSession([FakeResponse(200, headers={"Content-Length": "999999"})])

    with pytest.raises(GraphError, match="liiga suur"):
        GraphClient(settings_for(max_download_bytes=100), session=session).download_to(
            tmp_path / "workbook.xlsx"
        )


def test_a_non_xlsx_content_type_is_refused(tmp_path):
    session = FakeSession(
        [FakeResponse(200, headers={"Content-Type": "text/html"}, body=b"<html>")]
    )

    with pytest.raises(GraphError, match="failitüüp"):
        GraphClient(settings_for(), session=session).download_to(tmp_path / "workbook.xlsx")


def test_an_empty_download_is_refused(tmp_path):
    session = FakeSession([FakeResponse(200, body=b"")])

    with pytest.raises(GraphError, match="tühi"):
        GraphClient(settings_for(), session=session).download_to(tmp_path / "workbook.xlsx")


def test_no_token_appears_in_any_error_message(tmp_path):
    session = FakeSession([FakeResponse(403)])

    with pytest.raises(GraphError) as error:
        GraphClient(settings_for(), session=session).get_item_metadata()

    assert SYNTHETIC_TOKEN not in str(error.value)
