"""Record which filename normaliser produced a catalogue snapshot.

Additive and nullable-equivalent: one CharField with a blank default. Existing
snapshots keep `""`, which is not the current version, so the next sync rebuilds
them — which is the intent. Nothing is backfilled and no existing row is
rewritten, so no populated-predecessor test is required.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("legal_work", "0006_opinion_matching_and_resources"),
    ]

    operations = [
        migrations.AddField(
            model_name="opinioncataloguesnapshot",
            name="filename_normaliser_version",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
                verbose_name="Failinime normaliseerija versioon",
            ),
        ),
    ]
