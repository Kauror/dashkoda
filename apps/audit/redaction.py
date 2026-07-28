"""Redaction for audit change summaries.

An audit trail is only safe to keep forever if it can never accumulate secrets.
Redaction therefore runs inside ``AuditEvent.save()`` rather than only in the
service layer, so no caller can write an unredacted summary even by using the
ORM directly.
"""

import re

MASK = "[redacted]"
MAX_VALUE_LENGTH = 500

# Key names whose values are never safe to keep. Matched case-insensitively
# anywhere in the key, so `viewer_pin_hash` and `HTTP_AUTHORIZATION` both match.
SENSITIVE_KEY_PATTERN = re.compile(
    r"pin|password|passwd|secret|token|api[-_]?key|private[-_]?key|credential"
    r"|authorization|auth[-_]?header|cookie|session[-_]?key|csrf"
    r"|connection[-_]?string|dsn|salt|signature|hash",
    re.IGNORECASE,
)

# A stored artifact checksum is a wanted, non-secret fact, so it is exempt from
# the `hash` rule above.
ALLOWED_KEYS = frozenset({"sha256", "import_key", "content_hash"})


def _redact_value(value):
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return f"{value[:MAX_VALUE_LENGTH]}…[truncated]"
    return value


def redact(summary):
    """Return a copy of ``summary`` with sensitive values masked.

    Non-dict input is rejected rather than silently stored, because a free-form
    blob cannot be inspected for secrets.
    """
    if summary is None:
        return {}
    if not isinstance(summary, dict):
        raise TypeError("change_summary must be a dict")

    redacted = {}
    for key, value in summary.items():
        text_key = str(key)
        if text_key not in ALLOWED_KEYS and SENSITIVE_KEY_PATTERN.search(text_key):
            redacted[text_key] = MASK
        else:
            redacted[text_key] = _redact_value(value)
    return redacted
