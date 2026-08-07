"""What the application says about itself, and what it must never say.

Eighteen modules call `logger.info(...)` to record what a collection did —
`legal_work.public_sync completed rows=… size=…`, `ga4.sync imported
period_end=…`. None of it reached anywhere: with no `LOGGING` setting Django
configures handlers for `django` and `django.server` only, so an `INFO` record
under `dashkoda.*` had no handler and was discarded.

The scheduled jobs still wrote their JSON line, so nothing looked broken. What
was missing was everything a collector noticed on the way to that line — which
host it read, how many bytes it took, why it decided the content was unchanged.

These tests hold both halves: that DashKoda's own records now reach stderr, and
that turning them on did not turn on anything that narrates credentials.
"""

from __future__ import annotations

import logging
import re

import pytest
from django.conf import settings


class TestTheConfigurationExists:
    def test_logging_is_configured_at_all(self):
        assert settings.LOGGING, "no LOGGING setting: dashkoda INFO records go nowhere"

    def test_dashkoda_logs_at_info(self):
        assert settings.LOGGING["loggers"]["dashkoda"]["level"] == "INFO"

    def test_it_writes_to_stderr(self):
        """stderr is what the container runtime and the cron wrappers capture."""
        handler = settings.LOGGING["handlers"]["stderr"]

        assert handler["class"] == "logging.StreamHandler"
        assert handler["stream"] == "ext://sys.stderr"

    def test_dashkoda_does_not_propagate(self):
        """Django's root handler would otherwise print every record twice."""
        assert settings.LOGGING["loggers"]["dashkoda"]["propagate"] is False


class TestThirdPartyNoiseStaysOff:
    """Hearing this application is the point. Hearing every library is not."""

    @pytest.mark.parametrize(
        "name", ["urllib3", "requests", "google", "google_auth_httplib2", "asyncio"]
    )
    def test_it_is_pinned_to_warning(self, name):
        assert settings.LOGGING["loggers"][name]["level"] == "WARNING"

    def test_google_auth_is_covered_by_the_google_namespace(self):
        """`google.auth` narrates credential handling. It must stay quiet."""
        logger = logging.getLogger("google.auth.transport.requests")

        assert logger.getEffectiveLevel() >= logging.WARNING

    def test_urllib3_would_otherwise_narrate_every_connection(self):
        assert logging.getLogger("urllib3.connectionpool").getEffectiveLevel() >= logging.WARNING


class TestARecordActuallyArrives:
    """The configuration is only worth having if a real call site reaches it."""

    def test_a_collector_info_record_is_emitted(self, caplog):
        with caplog.at_level(logging.INFO, logger="dashkoda.legal_work.public_sync"):
            logging.getLogger("dashkoda.legal_work.public_sync").info(
                "legal_work.public_sync completed rows=%s dry_run=%s size=%s", 612, False, 81_920
            )

        assert "rows=612" in caplog.text
        assert "size=81920" in caplog.text

    def test_a_debug_record_is_not(self):
        """INFO is the floor. Debug chatter is not what a cron log is for."""
        assert logging.getLogger("dashkoda.legal_work.public_sync").getEffectiveLevel() == (
            logging.INFO
        )

    def test_every_collector_logger_sits_under_the_dashkoda_namespace(self):
        """A logger outside it would silently get no handler, as they all did."""
        import pathlib
        import re

        repo = pathlib.Path(__file__).resolve().parents[2]
        names = set()
        for path in (repo / "apps").rglob("*.py"):
            names.update(re.findall(r'getLogger\("([^"]+)"\)', path.read_text(encoding="utf-8")))

        assert names, "no named loggers found at all"
        stray = sorted(n for n in names if not n.startswith("dashkoda."))
        assert not stray, f"these loggers would reach no handler: {stray}"


class TestWhatMayNeverBeLogged:
    """A log line is stored, shipped and read by people. Some things cannot be in one."""

    def test_no_call_site_logs_a_configured_url_or_credential(self):
        """The sharing URLs and the GA4 key path are bearer-style secrets."""
        import pathlib
        import re

        repo = pathlib.Path(__file__).resolve().parents[2]
        forbidden = re.compile(
            r"log(?:ger)?\.(?:debug|info|warning|error|exception)\([^)]*"
            r"(OIGUSLOOME_PUBLIC_URL|EVENT_PROGRAMME_PUBLIC_URL|GA4_CREDENTIALS_FILE"
            r"|GA4_PROPERTY_ID|credentials_file|property_id|sharing_url)",
            re.S,
        )
        offenders = []
        for path in (repo / "apps").rglob("*.py"):
            if forbidden.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(repo)))

        assert not offenders, f"a log call references a credential: {offenders}"

    def test_the_public_http_layer_logs_no_response_body(self):
        import inspect

        from apps.core import public_http

        source = inspect.getsource(public_http)
        for call in re.findall(r"logger\.\w+\([^)]*\)", source):
            assert ".content" not in call, "a log call carries a response body"
            assert ".text" not in call, "a log call carries a response body"
