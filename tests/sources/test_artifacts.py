import hashlib
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.db import IntegrityError, transaction

from apps.audit.models import AuditAction, AuditEvent
from apps.sources.models import ImmutableFieldError, SourceArtifact
from apps.sources.services import (
    ArtifactRejected,
    calculate_sha256,
    register_artifact,
    register_external_reference,
)

from .conftest import SYNTHETIC_CSV

pytestmark = pytest.mark.django_db


def test_checksum_and_size_are_calculated_from_the_streamed_content(data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())

    assert artifact.sha256 == hashlib.sha256(SYNTHETIC_CSV).hexdigest()
    assert artifact.size_bytes == len(SYNTHETIC_CSV)


def test_a_client_supplied_checksum_is_never_trusted(data_source, upload):
    handle = upload()
    handle.sha256 = "0" * 64

    artifact = register_artifact(source=data_source, upload=handle)

    assert artifact.sha256 == hashlib.sha256(SYNTHETIC_CSV).hexdigest()


def test_streaming_helper_rewinds_the_file(upload):
    handle = upload()

    checksum, size = calculate_sha256(handle)

    assert checksum == hashlib.sha256(SYNTHETIC_CSV).hexdigest()
    assert size == len(SYNTHETIC_CSV)
    assert handle.read() == SYNTHETIC_CSV


def test_same_content_is_rejected_for_the_same_source(data_source, upload):
    register_artifact(source=data_source, upload=upload())

    with pytest.raises(ArtifactRejected):
        register_artifact(source=data_source, upload=upload())


def test_same_content_under_a_different_filename_still_deduplicates(data_source, upload):
    register_artifact(source=data_source, upload=upload(name="first.csv"))

    with pytest.raises(ArtifactRejected):
        register_artifact(source=data_source, upload=upload(name="totally-different.csv"))


def test_same_content_is_allowed_for_a_different_source(data_source, other_data_source, upload):
    first = register_artifact(source=data_source, upload=upload())
    second = register_artifact(source=other_data_source, upload=upload())

    assert first.sha256 == second.sha256
    assert first.source_id != second.source_id


def test_database_also_enforces_source_checksum_uniqueness(data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())

    with pytest.raises(IntegrityError), transaction.atomic():
        SourceArtifact.objects.create(
            source=data_source,
            sha256=artifact.sha256,
            size_bytes=1,
            file="sources/x/duplicate.csv",
        )


def test_upload_larger_than_the_limit_is_rejected(data_source, upload, settings):
    settings.SOURCE_ARTIFACT_MAX_BYTES = 16

    with pytest.raises(ArtifactRejected, match="liiga suur"):
        register_artifact(source=data_source, upload=upload(b"x" * 64))


def test_upload_at_the_limit_is_accepted(data_source, upload, settings):
    settings.SOURCE_ARTIFACT_MAX_BYTES = 64

    artifact = register_artifact(source=data_source, upload=upload(b"x" * 64))

    assert artifact.size_bytes == 64


def test_empty_upload_is_rejected(data_source, upload):
    with pytest.raises(ArtifactRejected, match="Tühja"):
        register_artifact(source=data_source, upload=upload(b""))


@pytest.mark.parametrize(
    "filename",
    ["payload.exe", "script.sh", "macro.xlsm", "archive.zip", "library.dll", "noextension"],
)
def test_executable_and_unknown_formats_are_rejected(data_source, upload, filename):
    with pytest.raises(ArtifactRejected, match="laiend"):
        register_artifact(source=data_source, upload=upload(name=filename))


def test_default_upload_limit_is_conservative():
    assert settings.SOURCE_ARTIFACT_MAX_BYTES == 25 * 1024 * 1024


def test_stored_path_never_uses_the_client_filename(data_source, upload):
    artifact = register_artifact(
        source=data_source,
        upload=upload(name="../../etc/passwd-attempt.csv"),
    )

    stored = Path(artifact.file.name)
    assert ".." not in artifact.file.name
    assert "passwd-attempt" not in artifact.file.name
    assert stored.suffix == ".csv"
    assert stored.parts[0] == "sources"
    # The original name survives only as metadata.
    assert artifact.original_name == "../../etc/passwd-attempt.csv"


def test_stored_file_stays_inside_the_private_root(data_source, upload, private_artifact_root):
    artifact = register_artifact(source=data_source, upload=upload())

    assert Path(artifact.file.path).is_relative_to(private_artifact_root)


def test_private_root_is_outside_every_served_static_path(private_artifact_root):
    root = Path(private_artifact_root).resolve()

    assert not root.is_relative_to(Path(settings.STATIC_ROOT).resolve())
    for static_dir in settings.STATICFILES_DIRS:
        assert not root.is_relative_to(Path(static_dir).resolve())


def test_artifact_has_no_public_url(data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())

    with pytest.raises(SuspiciousFileOperation):
        artifact.file.url  # noqa: B018


def test_file_and_checksum_are_immutable_after_registration(data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())

    artifact.sha256 = "1" * 64
    with pytest.raises(ImmutableFieldError):
        artifact.save()

    reloaded = SourceArtifact.objects.get(pk=artifact.pk)
    reloaded.file.name = "sources/other/other.csv"
    with pytest.raises(ImmutableFieldError):
        reloaded.save()


def test_mutable_metadata_can_still_be_corrected(data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())

    artifact.mime_type = "text/csv"
    artifact.save()

    assert SourceArtifact.objects.get(pk=artifact.pk).mime_type == "text/csv"


def test_registration_records_an_audit_event(data_source, upload, staff_user):
    artifact = register_artifact(source=data_source, upload=upload(), uploaded_by=staff_user)

    event = AuditEvent.objects.get(action=AuditAction.ARTIFACT_REGISTERED)
    assert event.object_id == str(artifact.pk)
    assert event.actor_id == staff_user.pk
    assert event.change_summary["sha256"] == artifact.sha256


def test_external_reference_artifact_needs_no_file(data_source):
    artifact = register_external_reference(
        source=data_source,
        external_reference="https://example.invalid/synthetic-register",
    )

    assert artifact.is_external
    assert artifact.sha256 == ""


def test_artifact_must_have_exactly_one_of_file_or_reference(data_source):
    with pytest.raises(ValidationError):
        register_external_reference(source=data_source, external_reference="   ")

    with pytest.raises(IntegrityError), transaction.atomic():
        SourceArtifact.objects.create(source=data_source, file="", external_reference="")

    with pytest.raises(IntegrityError), transaction.atomic():
        SourceArtifact.objects.create(
            source=data_source,
            file="sources/1/both.csv",
            external_reference="https://example.invalid/both",
        )


@pytest.mark.parametrize(
    "reference",
    [
        "https://user:secret@example.invalid/file.csv",
        "https://example.invalid/file.csv?signature=abc123",
    ],
)
def test_external_reference_must_not_embed_credentials_or_tokens(data_source, reference):
    with pytest.raises(ValidationError):
        register_external_reference(source=data_source, external_reference=reference)
