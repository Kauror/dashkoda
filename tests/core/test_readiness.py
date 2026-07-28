import pytest
from django.db import DatabaseError, connection
from django.urls import reverse


def test_readiness_url_is_registered():
    assert reverse("health-ready") == "/health/ready/"


@pytest.mark.django_db
def test_readiness_returns_minimal_success_for_working_database(client):
    response = client.get("/health/ready/")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.content == b'{"status":"ok"}'


def test_readiness_returns_minimal_503_without_exception_details(client, monkeypatch):
    private_error = "private-host:5432 secret-password connection refused"

    def broken_cursor():
        raise DatabaseError(private_error)

    monkeypatch.setattr(connection, "cursor", broken_cursor)

    response = client.get("/health/ready/")

    assert response.status_code == 503
    assert response.content == b'{"status":"unavailable"}'
    assert private_error.encode() not in response.content
    assert b"database" not in response.content.lower()
    assert b"exception" not in response.content.lower()


@pytest.mark.django_db
def test_test_suite_uses_postgresql():
    assert connection.vendor == "postgresql"
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
