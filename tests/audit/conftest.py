import pytest

from apps.sources.models import DataSource


@pytest.fixture
def data_source(db):
    """A plain synthetic source, created without the service.

    The audit tests record their own events, so this fixture deliberately does
    not generate any.
    """
    return DataSource.objects.create(
        slug="synthetic-audit-source",
        name="Sünteetiline auditiallikas",
        authority_rank=10,
    )
