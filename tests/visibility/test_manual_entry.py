"""The staff-only manual entry and correction workflow."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client

from apps.sources.models import ImportRun, SourceArtifact
from apps.visibility.models import (
    VisibilityEntryBatch,
    VisibilityMetric,
    VisibilityObservation,
)

from .conftest import NEW_URL, confirm, form_data, preview

pytestmark = pytest.mark.django_db


# -- access -------------------------------------------------------------


def test_staff_can_open_the_form(staff_client):
    response = staff_client.get(NEW_URL)

    assert response.status_code == 200
    assert "Lisa kanalite näitajad" in response.content.decode()


def test_anonymous_visitor_is_sent_to_the_viewer_login(client):
    response = client.get(NEW_URL)

    assert response.status_code == 302
    assert "/sisene/" in response["Location"]


def test_viewer_pin_alone_cannot_reach_the_form(viewer_client):
    """A PIN gets you into the dashboard, never into data entry."""
    response = viewer_client.get(NEW_URL)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_a_non_staff_account_cannot_reach_the_form(nonstaff_client):
    response = nonstaff_client.get(NEW_URL)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_a_viewer_cannot_publish_by_posting_directly(viewer_client):
    response = viewer_client.post(NEW_URL, confirm(form_data(facebook_followers=1000)))

    assert response.status_code == 302
    assert VisibilityObservation.objects.count() == 0


def test_csrf_is_enforced(staff_user, authenticate_viewer):
    enforcing = Client(enforce_csrf_checks=True)
    authenticate_viewer(enforcing)
    enforcing.force_login(staff_user)

    response = enforcing.post(NEW_URL, confirm(form_data(facebook_followers=1000)))

    assert response.status_code == 403
    assert VisibilityObservation.objects.count() == 0


# -- what the form shows ------------------------------------------------


def test_the_form_shows_the_fixed_public_profile_links(staff_client):
    body = staff_client.get(NEW_URL).content.decode()

    assert "https://www.facebook.com/Kaubanduskoda" in body
    assert "https://www.linkedin.com/company/ecci/" in body
    assert "https://www.instagram.com/kaubanduskoda" in body
    assert "https://www.youtube.com/user/Kaubanduskoda" in body
    assert 'target="_blank" rel="noopener noreferrer"' in body


def test_the_form_shows_the_latest_stored_value_beside_each_input(submit, staff_client, today):
    submit(facebook_followers=4100)

    body = staff_client.get(NEW_URL).content.decode()

    assert "Viimane salvestatud väärtus:" in body
    assert "4100" in body
    assert today.strftime("%d.%m.%Y") in body
    # The stored reading names how it was collected, so the person entering the
    # next one can see it was typed rather than fetched.
    assert "käsitsi sisestatud" in body


def test_a_metric_with_no_history_says_so_rather_than_showing_a_dash(staff_client):
    body = staff_client.get(NEW_URL).content.decode()

    assert "Salvestatud väärtust ei ole." in body


def test_the_observation_date_defaults_to_today(staff_client, today):
    body = staff_client.get(NEW_URL).content.decode()

    assert f'value="{today.isoformat()}"' in body


def test_no_google_analytics_field_appears_in_the_form(staff_client):
    """There is no GA input. The page *mentions* Google Analytics on purpose.

    The form's own help text says DashKoda queries neither a platform nor
    Google Analytics, which is exactly the disclosure this workflow owes a
    reader — so the assertion is about **fields**, not about the words.
    """
    body = staff_client.get(NEW_URL).content.decode()

    for absent in ("metric_sessions", "metric_page_views", "metric_active_users"):
        assert f'name="{absent}"' not in body
    assert "ei päri ühtegi platvormi ega Google Analyticsit" in body


# -- preview ------------------------------------------------------------


def test_preview_saves_nothing(staff_client):
    response = staff_client.post(NEW_URL, preview(form_data(facebook_followers=4100)))

    assert response.status_code == 200
    assert VisibilityEntryBatch.objects.count() == 0
    assert VisibilityObservation.objects.count() == 0
    assert SourceArtifact.objects.count() == 0
    assert ImportRun.objects.count() == 0


def test_a_stray_submit_previews_rather_than_publishing(staff_client):
    """No `action` at all must never publish."""
    response = staff_client.post(NEW_URL, form_data(facebook_followers=4100))

    assert response.status_code == 200
    assert VisibilityObservation.objects.count() == 0


def test_preview_lists_each_newsletter_and_derives_nothing_from_them(staff_client):
    """The preview shows what was typed, list by list.

    There is no total: the three go to three separate lists whose overlap
    nobody has counted, so 2150 would be an audience figure invented here.
    """
    response = staff_client.post(
        NEW_URL,
        preview(
            form_data(
                newsletter_eteataja=1200,
                newsletter_enews=800,
                newsletter_evestnik=150,
            )
        ),
    )
    body = response.content.decode()

    for label in ("e-Teataja", "eNews", "e-Vestnik"):
        assert label in body
    assert "2150" not in body


def test_preview_shows_the_change_against_the_previous_observation(submit, staff_client, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)

    body = staff_client.post(NEW_URL, preview(form_data(facebook_followers=4400))).content.decode()

    assert "300" in body
    assert days_ago(30).strftime("%d.%m.%Y") in body


def test_preview_warns_about_a_same_date_correction(submit, staff_client):
    submit(facebook_followers=4100)

    body = staff_client.post(NEW_URL, preview(form_data(facebook_followers=4200))).content.decode()

    assert "asendatakse" in body


# -- publication --------------------------------------------------------


def test_confirmation_publishes_every_supplied_metric(staff_client, today):
    response = staff_client.post(
        NEW_URL,
        confirm(
            form_data(
                newsletter_eteataja=1200,
                newsletter_enews=800,
                newsletter_evestnik=150,
                facebook_followers=4200,
                linkedin_followers=2500,
                instagram_followers=700,
                youtube_subscribers=60,
            )
        ),
    )

    assert response.status_code == 302
    batch = VisibilityEntryBatch.objects.get()
    assert batch.observation_date == today
    assert batch.observations.count() == 7
    assert all(row.is_current_for_date for row in batch.observations.all())
    assert all(row.published_at is not None for row in batch.observations.all())


def test_publication_redirects_rather_than_re_rendering(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200)))

    assert response.status_code == 302
    assert response["Location"].startswith("/admin/data-entry/visibility/")


def test_a_double_submit_returns_the_same_batch(staff_client):
    first = staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200)))
    second = staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200)))

    assert VisibilityEntryBatch.objects.count() == 1
    assert VisibilityObservation.objects.count() == 1
    assert first["Location"] == second["Location"]


def test_a_partial_submission_is_accepted(staff_client):
    """Nobody has every figure to hand on every day."""
    staff_client.post(NEW_URL, confirm(form_data(instagram_followers=700)))

    assert VisibilityObservation.objects.count() == 1
    assert VisibilityObservation.objects.get().metric == VisibilityMetric.INSTAGRAM_FOLLOWERS


def test_each_contributing_source_gets_its_own_artifact_and_import_run(staff_client):
    """One reading of Smaily is one artifact, whatever it produced.

    The three newsletter metrics share a source, so they share provenance; the
    two social channels are separate readings and get their own.
    """
    staff_client.post(
        NEW_URL,
        confirm(
            form_data(
                newsletter_eteataja=1200,
                newsletter_enews=800,
                facebook_followers=4200,
                linkedin_followers=2500,
            )
        ),
    )

    assert SourceArtifact.objects.count() == 3
    assert ImportRun.objects.count() == 3
    assert all(run.status == "succeeded" for run in ImportRun.objects.all())


def test_no_profile_url_is_stored_as_an_artifact_reference(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200)))

    reference = SourceArtifact.objects.get().external_reference
    assert reference.startswith("manual:facebook-followers:")
    assert "facebook.com" not in reference
    assert "http" not in reference


def test_one_correlation_id_threads_a_whole_submission(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200, linkedin_followers=2500)))

    batch = VisibilityEntryBatch.objects.get()
    assert {run.correlation_id for run in ImportRun.objects.all()} == {batch.correlation_id}


# -- validation ---------------------------------------------------------


def test_at_least_one_metric_is_required(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data()))

    assert response.status_code == 200
    assert "Vähemalt üks näitaja tuleb sisestada." in response.content.decode()
    assert VisibilityEntryBatch.objects.count() == 0


def test_a_future_observation_date_is_refused(staff_client, today):
    response = staff_client.post(
        NEW_URL,
        confirm(form_data(observation_date=today + timedelta(days=1), facebook_followers=4200)),
    )

    assert response.status_code == 200
    assert "ei saa olla tulevikus" in response.content.decode()
    assert VisibilityEntryBatch.objects.count() == 0


def test_a_negative_value_is_refused(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data(facebook_followers=-5)))

    assert response.status_code == 200
    assert VisibilityEntryBatch.objects.count() == 0


def test_invalid_text_is_not_silently_coerced(staff_client):
    response = staff_client.post(NEW_URL, confirm(form_data(facebook_followers="umbes 12k")))

    assert response.status_code == 200
    assert VisibilityEntryBatch.objects.count() == 0


def test_a_thousands_separator_written_with_a_space_is_accepted(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(facebook_followers="4 200")))

    assert VisibilityObservation.objects.get().value == 4200


def test_a_comma_separator_stays_invalid(staff_client):
    """A comma could be a decimal mark somewhere, and guessing would be worse
    than refusing."""
    response = staff_client.post(NEW_URL, confirm(form_data(facebook_followers="4,200")))

    assert response.status_code == 200
    assert VisibilityEntryBatch.objects.count() == 0


def test_a_blank_field_is_not_stored_as_zero(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200, instagram_followers="")))

    assert VisibilityObservation.objects.count() == 1
    assert not VisibilityObservation.objects.filter(
        metric=VisibilityMetric.INSTAGRAM_FOLLOWERS
    ).exists()


def test_an_explicit_zero_is_stored(staff_client):
    staff_client.post(NEW_URL, confirm(form_data(instagram_followers=0)))

    assert VisibilityObservation.objects.get().value == 0


def test_one_invalid_metric_rolls_back_the_whole_batch(staff_client):
    """One negative figure, so nothing at all is written.

    Not the three valid newsletter counts beside it either: a batch is one
    reading of the whole board, and half of one is not a state this table is
    allowed to hold.
    """
    response = staff_client.post(
        NEW_URL,
        confirm(
            form_data(
                newsletter_eteataja=100,
                newsletter_enews=900,
                newsletter_evestnik=500,
                facebook_followers=-1,
            )
        ),
    )

    assert response.status_code == 200
    assert VisibilityEntryBatch.objects.count() == 0
    assert VisibilityObservation.objects.count() == 0
    assert SourceArtifact.objects.count() == 0


def test_a_note_is_bounded_and_stored_on_the_batch(staff_client):
    staff_client.post(
        NEW_URL, confirm(form_data(facebook_followers=4200, note="Loetud lehe statistikast."))
    )

    assert VisibilityEntryBatch.objects.get().note == "Loetud lehe statistikast."


def test_an_over_long_note_is_refused(staff_client):
    response = staff_client.post(
        NEW_URL, confirm(form_data(facebook_followers=4200, note="x" * 501))
    )

    assert response.status_code == 200
    assert VisibilityEntryBatch.objects.count() == 0


# -- change warnings ----------------------------------------------------


def test_a_large_movement_warns_without_blocking(submit, staff_client, days_ago):
    """Both thresholds are exceeded: 4 100 → 1 000 is −76 % and −3 100."""
    submit(observation_date=days_ago(30), facebook_followers=4100)

    body = staff_client.post(NEW_URL, preview(form_data(facebook_followers=1000))).content.decode()

    assert "ebatavaliselt suur" in body
    assert "sisestuskontroll" in body
    # A warning asks for a second look; it never removes the ability to save.
    assert "Kinnita ja salvesta" in body


def test_an_ordinary_movement_does_not_warn(submit, staff_client, days_ago):
    """+300 clears the absolute floor but is only +7 % — one threshold, not both."""
    submit(observation_date=days_ago(30), facebook_followers=4100)

    body = staff_client.post(NEW_URL, preview(form_data(facebook_followers=4400))).content.decode()

    assert "ebatavaliselt suur" not in body


def test_a_large_proportional_move_on_a_small_channel_does_not_warn(submit, staff_client, days_ago):
    """60 → 90 is +50 % but only +30, so the absolute floor stops it."""
    submit(observation_date=days_ago(30), youtube_subscribers=60)

    body = staff_client.post(NEW_URL, preview(form_data(youtube_subscribers=90))).content.decode()

    assert "ebatavaliselt suur" not in body


def test_a_decrease_is_pointed_out_on_its_own(submit, staff_client, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4200)

    body = staff_client.post(NEW_URL, preview(form_data(facebook_followers=4180))).content.decode()

    assert "väiksem kui seisuga" in body


# -- corrections --------------------------------------------------------


def test_the_correction_form_prefills_from_the_batch(submit, staff_client):
    batch = submit(facebook_followers=4200, instagram_followers=700)

    body = staff_client.get(f"/admin/data-entry/visibility/{batch.pk}/correct/").content.decode()

    assert 'value="4200"' in body
    assert 'value="700"' in body
    assert "Paranda kanalite näitajaid" in body


def test_a_correction_publishes_a_revision_and_retires_the_original(submit, staff_client, today):
    batch = submit(facebook_followers=4200)

    staff_client.post(
        f"/admin/data-entry/visibility/{batch.pk}/correct/",
        confirm(form_data(facebook_followers=4250)),
    )

    rows = VisibilityObservation.objects.filter(
        metric=VisibilityMetric.FACEBOOK_FOLLOWERS, observation_date=today
    ).order_by("id")
    original, correction = rows
    assert original.value == 4200
    assert original.is_current_for_date is False
    assert correction.value == 4250
    assert correction.is_current_for_date is True
    assert correction.supersedes_id == original.pk


def test_an_unchanged_resubmission_of_the_same_date_changes_nothing(submit, staff_client):
    batch = submit(facebook_followers=4200)

    staff_client.post(
        f"/admin/data-entry/visibility/{batch.pk}/correct/",
        confirm(form_data(facebook_followers=4200)),
    )

    assert VisibilityEntryBatch.objects.count() == 1
    assert VisibilityObservation.objects.count() == 1


def test_a_later_date_preserves_the_earlier_reading(submit, staff_client, today, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)

    staff_client.post(NEW_URL, confirm(form_data(facebook_followers=4200)))

    rows = VisibilityObservation.objects.filter(
        metric=VisibilityMetric.FACEBOOK_FOLLOWERS
    ).order_by("observation_date")
    assert [(row.observation_date, row.value, row.is_current_for_date) for row in rows] == [
        (days_ago(30), 4100, True),
        (today, 4200, True),
    ]


# -- history and detail -------------------------------------------------


def test_the_detail_page_shows_exactly_what_was_published(submit, staff_client):
    batch = submit(facebook_followers=4200, note="Sünteetiline märkus.")

    body = staff_client.get(f"/admin/data-entry/visibility/{batch.pk}/").content.decode()

    assert "4200" in body
    assert "Sünteetiline märkus." in body
    assert batch.content_hash in body


def test_the_detail_page_offers_no_way_to_edit_the_record(submit, staff_client):
    """The admin chrome has its own logout form, so the check is specific:
    nothing on this page posts to a data-entry route."""
    batch = submit(facebook_followers=4200)

    body = staff_client.get(f"/admin/data-entry/visibility/{batch.pk}/").content.decode()

    assert 'action="/admin/data-entry/' not in body
    assert "Paranda" in body


def test_the_detail_page_rejects_a_post(submit, staff_client):
    batch = submit(facebook_followers=4200)

    response = staff_client.post(f"/admin/data-entry/visibility/{batch.pk}/", {})

    assert response.status_code == 405


def test_the_history_lists_submissions_newest_first(submit, staff_client, today, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    body = staff_client.get("/admin/data-entry/visibility/").content.decode()

    assert body.index(today.strftime("%d.%m.%Y")) < body.index(days_ago(30).strftime("%d.%m.%Y"))


def test_the_history_requires_staff(viewer_client):
    response = viewer_client.get("/admin/data-entry/visibility/")

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]
