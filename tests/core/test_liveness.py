from django.db import DatabaseError, connection
from django.urls import reverse


def test_liveness_url_is_registered():
    assert reverse("health-live") == "/health/live/"


def test_liveness_returns_only_minimal_status(client):
    response = client.get("/health/live/")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.content == b'{"status":"ok"}'
    assert response.json() == {"status": "ok"}
    assert set(response.json()) == {"status"}


def test_liveness_does_not_expose_version_or_dependency_details(client):
    payload = client.get("/health/live/").content.lower()

    assert b"version" not in payload
    assert b"django" not in payload
    assert b"python" not in payload
    assert b"database" not in payload


def test_liveness_stays_available_when_database_readiness_fails(client, monkeypatch):
    def broken_cursor():
        raise DatabaseError("database failure details must remain private")

    monkeypatch.setattr(connection, "cursor", broken_cursor)

    response = client.get("/health/live/")

    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
