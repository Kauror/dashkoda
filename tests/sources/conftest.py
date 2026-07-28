import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.sources.models import DataSource
from apps.sources.services import create_data_source

# Every byte in these tests is synthetic. Nothing here resembles Chamber data.
SYNTHETIC_CSV = b"as_of_date,total_members\n2099-01-01,0\n"


@pytest.fixture
def data_source(db):
    return create_data_source(
        slug="synthetic-test-source",
        name="Sünteetiline testallikas",
        authority_rank=10,
    )


@pytest.fixture
def other_data_source(db):
    return create_data_source(
        slug="synthetic-second-source",
        name="Teine sünteetiline testallikas",
        authority_rank=20,
    )


@pytest.fixture
def upload():
    def build(content: bytes = SYNTHETIC_CSV, name: str = "synthetic.csv"):
        return SimpleUploadedFile(name, content, content_type="text/csv")

    return build


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="synthetic-staff",
        password="synthetic-test-password",
        is_staff=True,
    )


@pytest.fixture
def downloader_user(db, staff_user):
    """Staff who additionally hold the artifact download permission."""
    permission = Permission.objects.get(
        codename="download_sourceartifact",
        content_type__app_label="sources",
    )
    staff_user.user_permissions.add(permission)
    return get_user_model().objects.get(pk=staff_user.pk)


@pytest.fixture
def viewer_only_user(db):
    return get_user_model().objects.create_user(
        username="synthetic-viewer",
        password="synthetic-test-password",
        is_staff=False,
    )


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser(
        username="synthetic-root",
        password="synthetic-test-password",
    )


@pytest.fixture
def inactive_source(db):
    return DataSource.objects.create(
        slug="synthetic-inactive",
        name="Mitteaktiivne testallikas",
        authority_rank=90,
        is_active=False,
    )
