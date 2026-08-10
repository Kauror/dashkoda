"""Rename the newsletter source now that its figures are collected, not typed.

`ensure_data_source` creates a source once and never updates it, which is
deliberate — it is registration, not configuration management. The consequence
is that an existing deployment would keep a row slugged `manual-smaily-audience`,
typed `manual` and described as something a person fills in by hand, for the rest
of the application's life. The admin shows that slug beside every newsletter
observation, so leaving it would be a quiet untruth in exactly the place an
auditor looks.

Only the source row moves. Every observation, artifact and import run points at
it by foreign key, so nothing else needs rewriting and no history changes
meaning: the readings taken while the figures *were* typed are still marked
`manual` by their own `collection_method`, which is where that fact belongs.

A deployment that has never registered the source has nothing to rename, and a
fresh database creates the new slug directly. Both are no-ops here.
"""

from django.db import migrations

OLD_SLUG = "manual-smaily-audience"
NEW_SLUG = "smaily-newsletter-audience"

OLD_NAME = "Smaily uudiskirjade auditoorium"
NEW_NAME = "Smaily uudiskirjade auditoorium"

# Frozen copies of the wording in `apps/visibility/bootstrap.py`. A migration
# must not import application constants: today's sentence is not the one this
# migration ran with, and re-running it after a restore has to reproduce the
# same state it produced the first time.
OLD_DESCRIPTION = (
    "Koja uudiskirjade nimekirjade aktiivsete saajate arv Smailys: e-Teataja, "
    "eNews ja e-Vestnik, iga nimekiri eraldi. Ei ole saadetud kirjade, "
    "avamiste ega klikkide arv. Väärtused sisestab koja töötaja käsitsi "
    "platvormi enda statistika põhjal. DashKoda ei päri platvormi API-t, ei "
    "kasuta ühtegi juurdepääsuvõtit ega salvesta ühtegi üksikut jälgijat, "
    "tellijat ega e-posti aadressi."
)
NEW_DESCRIPTION = (
    "Koja uudiskirjade nimekirjade tellijate arv Smailys: e-Teataja, "
    "eNews ja e-Vestnik, iga nimekiri eraldi. Kogutakse ajastatud käsuga "
    "sync_smaily ainult lugemispäringutega; salvestatakse üksnes segmentide "
    "tellijate arvud, mitte ühtegi e-posti aadressi, tellijat ega üksikut "
    "avamist või klikki."
)

OLD_TYPE = "manual"
NEW_TYPE = "other"


def _move(apps, *, from_slug, to_slug, name, description, source_type):
    DataSource = apps.get_model("sources", "DataSource")
    # `filter().update()` rather than a fetch and save: the historical model has
    # no service layer and no audit hook, and this is the one place a source row
    # is allowed to change without one.
    if DataSource.objects.filter(slug=to_slug).exists():
        # Already renamed, or a fresh database that registered the new slug
        # first. Renaming onto an existing slug would violate its uniqueness.
        return
    DataSource.objects.filter(slug=from_slug).update(
        slug=to_slug,
        name=name,
        description=description,
        source_type=source_type,
    )


def rename_forward(apps, schema_editor):
    _move(
        apps,
        from_slug=OLD_SLUG,
        to_slug=NEW_SLUG,
        name=NEW_NAME,
        description=NEW_DESCRIPTION,
        source_type=NEW_TYPE,
    )


def rename_backward(apps, schema_editor):
    _move(
        apps,
        from_slug=NEW_SLUG,
        to_slug=OLD_SLUG,
        name=OLD_NAME,
        description=OLD_DESCRIPTION,
        source_type=OLD_TYPE,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("visibility", "0005_smaily_newsletter_audience"),
        ("sources", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
