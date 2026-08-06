"""The managed store's safety properties, and that the admin cannot write.

The store is the only place in DashKoda where the application writes binary
files it will later serve, so the tests here are about the two ways that goes
wrong: a write that leaves a half-file behind, and a read that escapes the root.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.legal_work.opinion_catalogue_sync import synchronize_opinion_documents
from apps.legal_work.opinion_models import (
    OpinionCatalogueEntry,
    OpinionCatalogueSnapshot,
    OpinionDocumentBlob,
    OpinionDocumentExtraction,
)
from apps.legal_work.opinion_storage import (
    BlobMismatch,
    StorageError,
    blob_path,
    clear_temporary,
    digest_bytes,
    ensure_directories,
    quarantine_blob,
    read_blob,
    resolve_within_store,
    storage_key,
    store_blob,
    store_root,
    verify_blob,
)

from .opinion_factory import build_zip, make_pdf, opinion_pdf

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


# -- content addressing -----------------------------------------------------


def test_a_storage_key_is_a_pure_function_of_the_digest(opinion_roots):
    digest = digest_bytes(b"hello")

    assert storage_key(digest) == f"blobs/{digest[:2]}/{digest}.pdf"


@pytest.mark.parametrize("bad", ["", "not-hex", "AB" * 32, "a" * 63])
def test_a_key_needs_a_real_lower_case_digest(opinion_roots, bad):
    with pytest.raises(StorageError):
        storage_key(bad)


def test_storing_bytes_puts_them_under_their_own_digest(opinion_roots):
    ensure_directories()
    payload = opinion_pdf()

    stored = store_blob(payload)

    assert stored.digest == digest_bytes(payload)
    assert stored.reused is False
    assert blob_path(stored.digest).read_bytes() == payload


def test_storing_the_same_bytes_twice_reuses_the_first_copy(opinion_roots):
    ensure_directories()
    payload = opinion_pdf()

    first = store_blob(payload)
    second = store_blob(payload)

    assert second.reused is True
    assert first.key == second.key
    assert len(list((store_root() / "blobs").rglob("*.pdf"))) == 1


def test_a_digest_that_does_not_describe_the_bytes_is_refused(opinion_roots):
    ensure_directories()

    with pytest.raises(BlobMismatch):
        store_blob(opinion_pdf(), expected_digest=digest_bytes(b"something else"))


def test_a_write_leaves_no_temporary_file_behind(opinion_roots):
    ensure_directories()

    store_blob(opinion_pdf())

    assert list((store_root() / "temporary").glob("*")) == []


def test_verification_notices_corruption(opinion_roots):
    ensure_directories()
    stored = store_blob(opinion_pdf())
    blob_path(stored.digest).write_bytes(b"%PDF-1.7 tampered")

    ok, reason = verify_blob(stored.digest, expected_size=stored.byte_size)

    assert ok is False
    assert reason in {"digest_mismatch", "size_mismatch"}


def test_verification_notices_a_missing_file(opinion_roots):
    ensure_directories()
    stored = store_blob(opinion_pdf())
    blob_path(stored.digest).unlink()

    assert verify_blob(stored.digest) == (False, "missing")


def test_reading_a_missing_blob_raises_rather_than_returning_nothing(opinion_roots):
    ensure_directories()

    with pytest.raises(StorageError):
        read_blob(digest_bytes(b"never stored"))


def test_clearing_temporary_files_touches_only_that_directory(opinion_roots):
    ensure_directories()
    stored = store_blob(opinion_pdf())
    (store_root() / "temporary" / "abandoned.part").write_bytes(b"x")

    removed = clear_temporary()

    assert removed == 1
    assert blob_path(stored.digest).exists()


# -- the store cannot be escaped --------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "../outside.pdf",
        "../../etc/passwd",
        "blobs/../../outside.pdf",
        "/etc/passwd",
        "blobs/ab/../../../outside.pdf",
    ],
)
def test_a_key_that_resolves_outside_the_store_is_refused(opinion_roots, key):
    ensure_directories()

    with pytest.raises(StorageError):
        resolve_within_store(key)


def test_a_symlink_planted_in_the_store_cannot_be_followed_out(opinion_roots, tmp_path):
    """Resolution compares the *resolved* target, so a link is not a way out."""
    ensure_directories()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"secret")
    link = store_root() / "blobs" / "link.pdf"
    try:
        link.symlink_to(outside)
    except OSError, NotImplementedError:
        pytest.skip("this platform does not permit creating symlinks here")

    with pytest.raises(StorageError):
        resolve_within_store("blobs/link.pdf")


def test_quarantined_bytes_land_outside_the_blob_area(opinion_roots):
    ensure_directories()
    payload = make_pdf(with_javascript=True)

    key = quarantine_blob(
        payload, digest=digest_bytes(payload), reason="quarantined_active_content"
    )

    assert key.startswith("quarantine/")
    assert not blob_path(digest_bytes(payload)).exists()


def test_a_quarantine_reason_cannot_shape_a_path(opinion_roots):
    ensure_directories()
    payload = b"%PDF-1.7 x"

    key = quarantine_blob(payload, digest=digest_bytes(payload), reason="../../escape me/../")

    assert ".." not in key
    resolve_within_store(key)  # resolves, and stays inside


# -- nothing private is publicly reachable ----------------------------------


def test_the_store_is_not_inside_any_served_root(opinion_roots, settings):
    """WhiteNoise must not be able to reach a private letter."""
    store = store_root()
    served = [settings.STATIC_ROOT, *settings.STATICFILES_DIRS]

    for root in served:
        from pathlib import Path

        assert not store.is_relative_to(Path(root).resolve())


def test_nothing_is_configured_to_serve_files_from_disk(settings):
    """`MEDIA_URL` defaults to `/` in Django whether or not media is served, so
    the meaningful property is that no media *root* is configured and no route
    serves one. Without a root there is nothing for a media view to read."""
    assert not settings.MEDIA_ROOT

    from django.urls import get_resolver

    patterns = [str(getattr(p, "pattern", "")) for p in get_resolver().url_patterns]
    assert not any("media" in pattern.lower() for pattern in patterns)


def test_the_store_is_not_the_artifact_area(opinion_roots, settings):
    """Artifacts are served to staff under a download permission; the opinion
    store is not, and the two roots must never coincide."""
    from pathlib import Path

    artifacts = Path(settings.SOURCE_ARTIFACT_ROOT).resolve()

    assert not store_root().is_relative_to(artifacts)
    assert not artifacts.is_relative_to(store_root())


@pytest.mark.django_db
def test_the_viewer_routes_accept_an_opaque_identifier_only():
    """Phase 2 added both routes, and each takes a UUID converter.

    Phase 1 asserted these routes did not exist at all. They do now, so the
    property worth holding is the narrower one: neither will parse anything
    that is not an opaque identifier, so a filename or a traversal attempt
    cannot even reach a view.
    """
    import uuid

    from django.urls import NoReverseMatch

    for name in ("opinion-resource", "opinion-document"):
        assert reverse(name, args=[uuid.uuid4()])
        for hostile in ("../../etc/passwd", "some-file.pdf", "1"):
            with pytest.raises(NoReverseMatch):
                reverse(name, args=[hostile])


# -- the admin is read-only -------------------------------------------------


@pytest.mark.django_db
class TestOpinionAdmin:
    @pytest.fixture(autouse=True)
    def catalogue(self, opinion_roots, opinion_source):
        source, _ = opinion_roots
        name = "Opinions/2026-01-05 - Rahandusministeerium - Arvamus eelnou.pdf"
        build_zip({name: opinion_pdf()}, path=source / "Opinions.zip")
        synchronize_opinion_documents()

    def _signed_in(self, client, superuser, authenticate_viewer):
        # `/admin/` sits behind both viewer access and Django authentication.
        authenticate_viewer(client)
        client.force_login(superuser)
        return client

    @pytest.mark.parametrize(
        "model",
        [
            OpinionCatalogueSnapshot,
            OpinionCatalogueEntry,
            OpinionDocumentBlob,
            OpinionDocumentExtraction,
        ],
    )
    def test_a_changelist_renders(self, client, superuser, authenticate_viewer, model):
        self._signed_in(client, superuser, authenticate_viewer)
        url = reverse(f"admin:legal_work_{model._meta.model_name}_changelist")

        assert client.get(url).status_code == 200

    @pytest.mark.parametrize(
        "model",
        [
            OpinionCatalogueSnapshot,
            OpinionCatalogueEntry,
            OpinionDocumentBlob,
            OpinionDocumentExtraction,
        ],
    )
    def test_nothing_may_be_added(self, client, superuser, authenticate_viewer, model):
        self._signed_in(client, superuser, authenticate_viewer)
        url = reverse(f"admin:legal_work_{model._meta.model_name}_add")

        assert client.get(url).status_code in (403, 302)

    def test_an_entry_page_offers_no_editable_field(self, client, superuser, authenticate_viewer):
        self._signed_in(client, superuser, authenticate_viewer)
        entry = OpinionCatalogueEntry.objects.first()
        url = reverse("admin:legal_work_opinioncatalogueentry_change", args=[entry.pk])

        body = client.get(url).content.decode()

        assert "Save" not in body or 'name="_save"' not in body

    def test_no_admin_page_shows_a_full_digest_or_a_store_path(
        self, client, superuser, authenticate_viewer
    ):
        self._signed_in(client, superuser, authenticate_viewer)
        blob = OpinionDocumentBlob.objects.first()

        for url in (
            reverse("admin:legal_work_opiniondocumentblob_changelist"),
            reverse("admin:legal_work_opiniondocumentblob_change", args=[blob.pk]),
            reverse("admin:legal_work_opinioncatalogueentry_changelist"),
        ):
            body = client.get(url).content.decode()
            assert blob.sha256 not in body
            assert str(store_root()) not in body
            assert blob.storage_key not in body

    def test_an_anonymous_visitor_reaches_no_admin_page(self, client):
        url = reverse("admin:legal_work_opinioncatalogueentry_changelist")

        response = client.get(url)

        assert response.status_code in (302, 403)
        assert "Rahandusministeerium" not in response.content.decode()

    def test_a_viewer_without_django_login_reaches_no_admin_page(self, client, authenticate_viewer):
        """Passing the viewer PIN is not the same as being staff."""
        authenticate_viewer(client)
        url = reverse("admin:legal_work_opinioncatalogueentry_changelist")

        assert client.get(url).status_code in (302, 403)
