"""The event matcher's output.

Two new tables, `CreateModel` only. No existing table is altered and no
imported programme row is rewritten — in particular `EventProgrammeItem`
gains no field, so the workbook's own `public_url` is untouched.

Depends on `events.0002`, which creates the pages these rows point at.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("event_programme", "0001_initial"),
        ("events", "0002_public_event_catalogue"),
    ]

    operations = [
        migrations.CreateModel(
            name="EventPublicMatchSnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "resource_high_water",
                    models.BigIntegerField(default=0, verbose_name="Lehtede ülempiir"),
                ),
                (
                    "matcher_version",
                    models.CharField(max_length=64, verbose_name="Sobitaja versioon"),
                ),
                ("generated_at", models.DateTimeField(auto_now_add=True, verbose_name="Arvutatud")),
                (
                    "considered_count",
                    models.PositiveIntegerField(default=0, verbose_name="Vaadatud sündmusi"),
                ),
                ("matched_count", models.PositiveIntegerField(default=0, verbose_name="Seotud")),
                (
                    "ambiguous_count",
                    models.PositiveIntegerField(default=0, verbose_name="Ebaselgeid"),
                ),
                (
                    "unmatched_count",
                    models.PositiveIntegerField(default=0, verbose_name="Sidumata"),
                ),
                ("is_current", models.BooleanField(default=False, verbose_name="Kehtiv")),
                (
                    "programme_snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_match_snapshots",
                        to="event_programme.eventprogrammesnapshot",
                        verbose_name="Sündmuste programmi hetkeseis",
                    ),
                ),
            ],
            options={
                "verbose_name": "Sündmuste viidete sobitamine",
                "verbose_name_plural": "Sündmuste viidete sobitamised",
                "ordering": ("-generated_at", "-id"),
            },
        ),
        migrations.CreateModel(
            name="EventPublicMatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "event_id",
                    models.CharField(db_index=True, max_length=32, verbose_name="Sündmuse tunnus"),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("matched", "Sobitatud"),
                            ("ambiguous", "Mitmetimõistetav"),
                            ("unmatched", "Sobitamata"),
                        ],
                        max_length=16,
                        verbose_name="Otsus",
                    ),
                ),
                ("score", models.FloatField(default=0.0, verbose_name="Skoor")),
                ("runner_up_score", models.FloatField(default=0.0, verbose_name="Teise skoor")),
                ("score_margin", models.FloatField(default=0.0, verbose_name="Vahe")),
                (
                    "evidence_codes",
                    models.JSONField(blank=True, default=list, verbose_name="Tõendikoodid"),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="event_matches",
                        to="events.publiceventresource",
                        verbose_name="Avalik leht",
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches",
                        to="event_programme.eventpublicmatchsnapshot",
                        verbose_name="Sobitamise hetkeseis",
                    ),
                ),
            ],
            options={
                "verbose_name": "Sündmuse viite otsus",
                "verbose_name_plural": "Sündmuse viite otsused",
                "ordering": ("event_id",),
            },
        ),
        migrations.AddConstraint(
            model_name="eventpublicmatchsnapshot",
            constraint=models.UniqueConstraint(
                fields=("programme_snapshot", "resource_high_water", "matcher_version"),
                name="eventpublicmatch_unique_inputs",
            ),
        ),
        migrations.AddConstraint(
            model_name="eventpublicmatchsnapshot",
            constraint=models.UniqueConstraint(
                models.F("is_current"),
                condition=models.Q(("is_current", True)),
                name="eventpublicmatch_one_current",
            ),
        ),
        migrations.AddConstraint(
            model_name="eventpublicmatchsnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(("matched_count__lte", models.F("considered_count"))),
                name="eventpublicmatch_matched_within_total",
            ),
        ),
        migrations.AddIndex(
            model_name="eventpublicmatch",
            index=models.Index(
                fields=["snapshot", "decision"], name="event_progr_snapsho_7e9a58_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="eventpublicmatch",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "event_id"), name="eventpublicmatch_one_decision_per_event"
            ),
        ),
        migrations.AddConstraint(
            model_name="eventpublicmatch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("decision", "matched"), ("resource__isnull", False)),
                    models.Q(
                        models.Q(("decision", "matched"), _negated=True), ("resource__isnull", True)
                    ),
                    _connector="OR",
                ),
                name="eventpublicmatch_resource_only_when_matched",
            ),
        ),
    ]
