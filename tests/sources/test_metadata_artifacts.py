"""External references that carry a server-computed content identity.

A metadata-only artifact records what content *was* without keeping the file.
That is what lets a collector import a workbook it holds only temporarily, and
it must not weaken any existing registration rule.
"""

import hashlib

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditAction, AuditEvent
from apps.sources.models import SourceArtifact
from apps.sources.services import (
    ArtifactRejected,
    build_import_run,
    register_artifact,
    register_external_reference,
)

pytestmark = pytest.mark.django_db

IMPORTER = "synthetic-importer"
SCHEMA = "v1"
SAFE_REFERENCE = "onedrive-public:oigusloome"
CHECKSUM = hashlib.sha256(b"synthetic workbook bytes").hexdigest()


def register_metadata_only(source, *, sha256=CHECKSUM, size_bytes=1234, **kwargs):
    return register_external_reference(
        source=source,
        external_reference=SAFE_REFERENCE,
        original_name="dashkoda_oigusloome.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=sha256,
        size_bytes=size_bytes,
        **kwargs,
    )


# -- importability ------------------------------------------------------


def test_an_external_reference_without_a_checksum_is_not_importable(data_source):
    artifact = register_external_reference(
        source=data_source,
        external_reference="synthetic-registry-entry",
    )

    with pytest.raises(ValidationError, match="kontrollsumma"):
        build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)


def test_an_external_reference_with_a_valid_checksum_is_importable(data_source):
    artifact = register_metadata_only(data_source)

    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    assert run.artifact_id == artifact.pk
    assert run.import_key


def test_the_import_key_is_still_derived_from_the_artifact_checksum(data_source):
    from apps.sources.services import calculate_import_key

    artifact = register_metadata_only(data_source)

    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    assert run.import_key == calculate_import_key(IMPORTER, SCHEMA, CHECKSUM)


def test_a_file_backed_artifact_is_unaffected(data_source, upload):
    artifact = register_artifact(source=data_source, upload=upload())

    run = build_import_run(artifact=artifact, importer_name=IMPORTER, schema_version=SCHEMA)

    assert artifact.is_external is False
    assert run.import_key


# -- checksum validation -----------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        "abc",
        CHECKSUM.upper(),
        CHECKSUM[:-1] + "g",
        CHECKSUM + "0",
    ],
)
def test_a_malformed_checksum_is_refused(data_source, malformed):
    with pytest.raises(ArtifactRejected, match="kuueteistkümnendmärki"):
        register_metadata_only(data_source, sha256=malformed)

    assert SourceArtifact.objects.count() == 0


@pytest.mark.parametrize("size", [0, -1])
def test_a_checksum_without_a_positive_size_is_refused(data_source, size):
    with pytest.raises(ArtifactRejected, match="suurem kui null"):
        register_metadata_only(data_source, size_bytes=size)

    assert SourceArtifact.objects.count() == 0


def test_an_oversized_declared_size_is_refused(data_source, settings):
    settings.SOURCE_ARTIFACT_MAX_BYTES = 100

    with pytest.raises(ArtifactRejected, match="liiga suur"):
        register_metadata_only(data_source, size_bytes=101)


def test_the_same_content_cannot_be_registered_twice_under_one_source(data_source):
    register_metadata_only(data_source)

    with pytest.raises(ArtifactRejected, match="juba registreeritud"):
        register_metadata_only(data_source)

    assert SourceArtifact.objects.filter(sha256=CHECKSUM).count() == 1


def test_the_same_content_may_be_registered_under_a_different_source(
    data_source, other_data_source
):
    register_metadata_only(data_source)

    second = register_metadata_only(other_data_source)

    assert second.sha256 == CHECKSUM
    assert SourceArtifact.objects.filter(sha256=CHECKSUM).count() == 2


def test_a_file_backed_artifact_still_blocks_the_same_content_as_metadata(data_source, upload):
    stored = register_artifact(source=data_source, upload=upload())

    with pytest.raises(ArtifactRejected, match="juba registreeritud"):
        register_metadata_only(data_source, sha256=stored.sha256)


# -- shape and provenance ----------------------------------------------


def test_a_metadata_only_artifact_stores_no_file(data_source):
    artifact = register_metadata_only(data_source)

    assert artifact.is_external is True
    assert not artifact.file
    assert artifact.sha256 == CHECKSUM
    assert artifact.size_bytes == 1234
    assert artifact.original_name == "dashkoda_oigusloome.xlsx"
    assert artifact.mime_type.endswith("spreadsheetml.sheet")


def test_the_external_reference_is_a_safe_fixed_label(data_source):
    artifact = register_metadata_only(data_source)

    assert artifact.external_reference == SAFE_REFERENCE
    assert "?" not in artifact.external_reference
    assert "@" not in artifact.external_reference
    assert "http" not in artifact.external_reference


def test_a_reference_carrying_query_parameters_is_still_refused(data_source):
    """A sharing URL must never become the external reference."""
    with pytest.raises(ValidationError):
        register_external_reference(
            source=data_source,
            external_reference="https://synthetic.sharepoint.com/:x:/g/personal/a?e=synthetic",
            sha256=CHECKSUM,
            size_bytes=10,
        )


def test_a_registration_only_reference_keeps_working(data_source):
    artifact = register_external_reference(
        source=data_source,
        external_reference="synthetic-paper-archive-box-12",
    )

    assert artifact.sha256 == ""
    assert artifact.size_bytes == 0
    assert artifact.is_external is True


def test_registration_is_audited_with_the_content_identity(data_source):
    artifact = register_metadata_only(data_source)

    event = AuditEvent.objects.get(
        action=AuditAction.ARTIFACT_REGISTERED,
        object_id=str(artifact.pk),
    )
    assert event.change_summary["sha256"] == CHECKSUM
    assert event.change_summary["size_bytes"] == 1234
    assert event.change_summary["external_reference"] == SAFE_REFERENCE


def test_the_content_identity_is_immutable_once_registered(data_source):
    from apps.sources.models import ImmutableFieldError

    artifact = register_metadata_only(data_source)
    artifact.sha256 = hashlib.sha256(b"different").hexdigest()

    with pytest.raises(ImmutableFieldError):
        artifact.save()
