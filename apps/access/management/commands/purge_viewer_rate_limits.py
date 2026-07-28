from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.access.models import ViewerRateLimitBucket


class Command(BaseCommand):
    help = "Delete inactive viewer rate-limit buckets older than 30 days."

    def handle(self, *args, **options):
        now = timezone.now()
        cutoff = now - timedelta(days=30)
        deleted, _details = (
            ViewerRateLimitBucket.objects.filter(
                updated_at__lt=cutoff,
            )
            .filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now))
            .delete()
        )
        self.stdout.write(str(deleted))
