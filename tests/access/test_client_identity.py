import hashlib
import hmac

from django.test import RequestFactory

from apps.access.client import client_ip, client_key


def test_proxy_headers_are_ignored_by_default(settings):
    request = RequestFactory().get(
        "/sisene/",
        REMOTE_ADDR="192.0.2.20",
        HTTP_X_FORWARDED_FOR="198.51.100.1",
        HTTP_X_REAL_IP="198.51.100.2",
        HTTP_CF_CONNECTING_IP="198.51.100.3",
    )

    assert client_ip(request) == "192.0.2.20"


def test_cloudflare_header_is_used_only_when_explicitly_trusted(settings):
    settings.TRUST_CLOUDFLARE_IP_HEADER = True
    request = RequestFactory().get(
        "/sisene/",
        REMOTE_ADDR="192.0.2.20",
        HTTP_CF_CONNECTING_IP="198.51.100.3",
    )

    assert client_ip(request) == "198.51.100.3"


def test_invalid_trusted_cloudflare_header_falls_back_to_remote_address(settings):
    settings.TRUST_CLOUDFLARE_IP_HEADER = True
    request = RequestFactory().get(
        "/sisene/",
        REMOTE_ADDR="192.0.2.20",
        HTTP_CF_CONNECTING_IP="not-an-address",
    )

    assert client_ip(request) == "192.0.2.20"


def test_client_key_is_hmac_pseudonym_without_raw_address(settings):
    request = RequestFactory().get("/sisene/", REMOTE_ADDR="192.0.2.20")
    expected = hmac.new(
        settings.VIEWER_RATE_LIMIT_SECRET.encode(),
        b"192.0.2.20",
        hashlib.sha256,
    ).hexdigest()

    assert client_key(request) == expected
    assert "192.0.2.20" not in client_key(request)
