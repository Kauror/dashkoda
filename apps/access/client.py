import hashlib
import hmac
import ipaddress

from django.conf import settings


def _normalized_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value or ""))
    except ValueError:
        return None


def client_ip(request) -> str:
    if settings.TRUST_CLOUDFLARE_IP_HEADER:
        cloudflare_ip = request.META.get("HTTP_CF_CONNECTING_IP")
        if cloudflare_ip:
            normalized_cloudflare_ip = _normalized_ip(cloudflare_ip)
            if normalized_cloudflare_ip:
                return normalized_cloudflare_ip
    return _normalized_ip(request.META.get("REMOTE_ADDR")) or "unknown"


def client_key(request) -> str:
    return hmac.new(
        settings.VIEWER_RATE_LIMIT_SECRET.encode(),
        client_ip(request).encode(),
        hashlib.sha256,
    ).hexdigest()
