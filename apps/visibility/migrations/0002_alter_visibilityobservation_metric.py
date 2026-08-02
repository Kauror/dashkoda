"""Three named newsletters replace the member / non-member / overlap model.

The Chamber sends e-Teataja, eNews and e-Vestnik, each to its own list. The
previous vocabulary described one newsletter with two lists and an overlap
between them, and derived a unique audience from those three. That was never
what actually goes out.

**No data is deleted or rewritten.** Observations already recorded under
`newsletter_member_recipients`, `newsletter_nonmember_recipients` and
`newsletter_overlap_recipients` were real readings and stay in the table. They
simply stop being read: no registry entry describes them any more, and every
read path iterates the registry rather than the table. `choices` is not a
database constraint, so those rows remain valid storage — only
`VisibilityObservation.clean()` refuses an unknown metric, and it runs on new
writes, never on rows already there.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("visibility", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="visibilityobservation",
            name="metric",
            field=models.CharField(
                choices=[
                    ("newsletter_eteataja", "e-Teataja"),
                    ("newsletter_enews", "eNews"),
                    ("newsletter_evestnik", "e-Vestnik"),
                    ("facebook_followers", "Facebooki jälgijad"),
                    ("linkedin_followers", "LinkedIni jälgijad"),
                    ("instagram_followers", "Instagrami jälgijad"),
                    ("youtube_subscribers", "YouTube’i tellijad"),
                ],
                db_index=True,
                max_length=48,
                verbose_name="Näitaja",
            ),
        ),
    ]
