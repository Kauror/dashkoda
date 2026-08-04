"""Collect the previous completed day of GA4 website traffic."""

import hashlib
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.feed_sync import find_published_artifact
from apps.sources.services import (
    build_import_run,
    complete_import_run,
    register_external_reference,
    start_import_run,
)
from apps.visibility.bootstrap import ensure_ga4_source
from apps.visibility.ga4 import Ga4ApiCollector, Ga4NotConfigured, get_configuration
from apps.visibility.models import WebsiteTrafficObservation

IMPORTER_NAME = "ga4_daily"


class Command(BaseCommand):
    help = "Collect the previous completed day of Google Analytics website traffic."

    def handle(self, *args, **options):
        period = timezone.localdate() - timedelta(days=1)
        try:
            reading = Ga4ApiCollector(get_configuration()).collect(
                period_start=period, period_end=period
            )
        except (Ga4NotConfigured, OSError, ValueError) as error:
            raise CommandError(str(error)) from error

        payload = json.dumps(
            reading.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
        digest = hashlib.sha256(payload).hexdigest()
        with transaction.atomic():
            source = ensure_ga4_source()

            # A re-run of an already-collected day must finish cleanly, exactly
            # as the other feeds treat unchanged content: the checksum is the
            # content identity, and re-registering it would fail the artifact
            # uniqueness rule and escape a cron job as a traceback.
            artifact, already_published = find_published_artifact(source, digest, IMPORTER_NAME)
            if already_published:
                self.stdout.write(
                    self.style.SUCCESS(f"Google Analytics: {period.isoformat()} on juba avaldatud.")
                )
                return

            artifact = artifact or register_external_reference(
                source=source,
                external_reference="ga4:data-api:daily",
                original_name="ga4-daily.json",
                mime_type="application/json",
                sha256=digest,
                size_bytes=len(payload),
            )
            run = build_import_run(
                artifact=artifact,
                importer_name=IMPORTER_NAME,
                schema_version="1.0",
                dry_run=False,
            )
            start_import_run(run)
            WebsiteTrafficObservation.objects.filter(source=source, is_current=True).update(
                is_current=False
            )
            WebsiteTrafficObservation.objects.create(
                source=source,
                artifact=artifact,
                import_run=run,
                observed_at=timezone.now(),
                period_start=period,
                period_end=period,
                sessions=reading.sessions,
                active_users=reading.active_users,
                page_views=reading.page_views,
                is_current=True,
            )
            complete_import_run(run, rows_added=1)
        self.stdout.write(self.style.SUCCESS(f"Google Analytics: {period.isoformat()}"))
