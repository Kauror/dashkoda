"""Publish deterministic synthetic content for the browser acceptance suite.

CI's database is empty, so the browser suite has always exercised empty states
only. That is why a real 152-pixel horizontal overflow reached production while
every viewport assertion passed: nothing in CI was ever long enough to truncate.
This command fills the database with content shaped to expose exactly that class
of defect — very long Estonian titles, linked headings carrying a visually
hidden suffix, wide amounts, explicit zeros beside genuinely missing values, and
enough rows to scroll.

**Every value is invented.** No Chamber member total, fee figure, event, legal
topic, article, organisation or URL appears. The names are obviously synthetic
so that a screenshot can never be mistaken for real data.

Each domain owns its own builder in its own `e2e_seed.py`, published through the
domain services rather than by writing rows directly: the same collectors,
importers, import registry, atomic publication and audit trail. No immutability
guard is weakened to make seeding easier, and nothing performs a network request
or touches a real source. This command owns the order they run in and nothing
else — a domain adding content edits its own module, not this one.

Re-running is safe. Every publisher is idempotent over its own content identity
— the feed syncs by checksum, the manual publishers by content hash — so a
second run on the same day publishes nothing new and reports `unchanged`.
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# Settings modules under which seeding is permitted. Production is refused by
# construction rather than by a flag someone can pass anyway.
ALLOWED_SETTINGS_MODULES = frozenset(
    {
        "config.settings.local",
        "config.settings.test",
    }
)


def _require_non_production() -> str:
    """Refuse to seed anything but an explicit development or test database."""
    module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if module not in ALLOWED_SETTINGS_MODULES:
        raise CommandError(
            "seed_e2e_data refuses to run under "
            f"{module or '(unset DJANGO_SETTINGS_MODULE)'}. "
            "It is permitted only under " + ", ".join(sorted(ALLOWED_SETTINGS_MODULES)) + "."
        )
    return module


class Command(BaseCommand):
    help = (
        "Publish deterministic synthetic content for the browser acceptance "
        "suite. Refuses to run under production settings."
    )

    def handle(self, *args, **options):
        module = _require_non_production()
        today = timezone.localdate()

        # Imported here rather than at module scope: each builder pulls in its
        # own domain's models and services, and a management command should not
        # drag all twelve apps into every `manage.py` invocation.
        from apps.event_programme import e2e_seed as event_programme_seed
        from apps.events import e2e_seed as events_seed
        from apps.legal_work import e2e_seed as legal_work_seed
        from apps.membership import e2e_seed as membership_seed
        from apps.news import e2e_seed as news_seed
        from apps.shop import e2e_seed as shop_seed
        from apps.visibility import e2e_seed as visibility_seed

        # The order is the contract this command owns. Each builder is
        # independent except where a later one reads what an earlier one
        # published, and those two dependencies are spelled out below.
        #
        # The legal-work synchronisation owns its own temporary directory and
        # deletes it on every exit path, so no seeded workbook outlives the
        # command.
        lines = [
            legal_work_seed.seed(today),
            event_programme_seed.seed(today),
            events_seed.seed(today),
            news_seed.seed(today),
            membership_seed.seed_public(),
            membership_seed.seed_internal(today),
            visibility_seed.seed_manual(today),
            # After the news and the events: the page rows resolve their titles
            # from those catalogues, and a ranking seeded first would show paths
            # where the finished page shows titles.
            visibility_seed.seed_website_analytics(today),
            # Last, and after the analytics on purpose: the E-pood page divides
            # acquisitions by page views, and seeding it against an empty GA4
            # history would exercise only the "no web comparison" branch.
            shop_seed.seed(today),
        ]

        self.stdout.write(self.style.SUCCESS(f"Sünteetiline seeme ({module}):"))
        for line in lines:
            self.stdout.write(f"  {line}")
