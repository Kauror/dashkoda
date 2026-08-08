"""The durable catalogue of public Koda.ee event pages.

Two new tables and nothing else. No existing table is altered, no imported row
is rewritten and no `EventSnapshot` or `EventItem` is touched — the calendar
feed continues to work exactly as before, and this catalogue sits beside it.

`PublicEventResource` is deliberately not tied to a snapshot. It accumulates,
so a discovery run that fails partway leaves every previously known page in
place rather than orphaning it.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
        ("sources", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicEventResource",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                (
                    "canonical_url",
                    models.URLField(max_length=500, unique=True, verbose_name="Viide"),
                ),
                (
                    "stable_key",
                    models.CharField(max_length=200, unique=True, verbose_name="Püsiv võti"),
                ),
                ("title", models.TextField(verbose_name="Pealkiri")),
                ("starts_on", models.DateField(db_index=True, verbose_name="Algab")),
                ("ends_on", models.DateField(blank=True, null=True, verbose_name="Lõpeb")),
                (
                    "category",
                    models.CharField(blank=True, max_length=120, verbose_name="Kategooria"),
                ),
                (
                    "location",
                    models.CharField(blank=True, max_length=200, verbose_name="Toimumiskoht"),
                ),
                (
                    "discovered_from",
                    models.CharField(
                        choices=[
                            ("sitemap", "Saidikaart"),
                            ("archive", "Arhiiv"),
                            ("current", "Jooksev nimekiri"),
                        ],
                        max_length=16,
                        verbose_name="Leidmisviis",
                    ),
                ),
                ("content_checksum", models.CharField(max_length=64, verbose_name="Sisu räsi")),
                (
                    "first_seen_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Esmakordselt nähtud"),
                ),
                ("last_seen_at", models.DateTimeField(verbose_name="Viimati nähtud")),
                (
                    "last_changed_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Viimati muutunud"),
                ),
            ],
            options={
                "verbose_name": "Avalik sündmuse leht",
                "verbose_name_plural": "Avalikud sündmuste lehed",
                "ordering": ("-starts_on", "title", "stable_key"),
                "indexes": [
                    models.Index(
                        fields=["starts_on", "stable_key"], name="events_publ_starts__1c7f34_idx"
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("title", ""), _negated=True),
                        name="publicevent_title_required",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("ends_on__isnull", True),
                            ("ends_on__gte", models.F("starts_on")),
                            _connector="OR",
                        ),
                        name="publicevent_end_date_not_before_start",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PublicEventDiscoverySnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[("full", "Täielik"), ("incremental", "Uuendus")],
                        max_length=16,
                        verbose_name="Režiim",
                    ),
                ),
                ("observed_at", models.DateTimeField(verbose_name="Vaatluse aeg")),
                ("is_current", models.BooleanField(default=False, verbose_name="Kehtiv")),
                (
                    "pages_fetched",
                    models.PositiveIntegerField(default=0, verbose_name="Laaditud lehti"),
                ),
                (
                    "urls_seen",
                    models.PositiveIntegerField(default=0, verbose_name="Nähtud viiteid"),
                ),
                (
                    "resources_created",
                    models.PositiveIntegerField(default=0, verbose_name="Uusi lehti"),
                ),
                (
                    "resources_updated",
                    models.PositiveIntegerField(default=0, verbose_name="Muutunud lehti"),
                ),
                (
                    "resources_unchanged",
                    models.PositiveIntegerField(default=0, verbose_name="Muutumatuid lehti"),
                ),
                ("is_complete", models.BooleanField(default=True, verbose_name="Täielik")),
                ("error_count", models.PositiveIntegerField(default=0, verbose_name="Vigu")),
                (
                    "warning_codes",
                    models.JSONField(blank=True, default=list, verbose_name="Hoiatuskoodid"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Loodud")),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="public_event_discoveries",
                        to="sources.datasource",
                        verbose_name="Andmeallikas",
                    ),
                ),
            ],
            options={
                "verbose_name": "Avalike lehtede avastusjooks",
                "verbose_name_plural": "Avalike lehtede avastusjooksud",
                "ordering": ("-observed_at", "-id"),
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("is_current", True)),
                        fields=("source",),
                        name="publiceventdiscovery_one_current_per_source",
                    )
                ],
            },
        ),
    ]
