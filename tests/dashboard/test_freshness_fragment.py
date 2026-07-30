import pytest

from apps.membership.collector import MembershipCollectionError
from apps.membership.sync import synchronize_membership
from tests.koda.conftest import collector_raising, collector_returning, membership_collection

pytestmark = pytest.mark.django_db

FRAGMENT_URL = "/dashboard/varskus/"


def test_fragment_is_protected_like_every_other_route(client):
    response = client.get(FRAGMENT_URL)

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_expired_session_gets_an_hx_redirect_instead_of_a_swapped_login_page(client):
    response = client.get(FRAGMENT_URL, headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/sisene/?")
    assert b"PIN" not in response.content


def test_authenticated_fragment_returns_only_the_partial(client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get(FRAGMENT_URL, headers={"HX-Request": "true"})
    content = response.content.decode()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "<html" not in content
    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Ühendamata" in content


def test_fragment_is_read_only(client, authenticate_viewer):
    authenticate_viewer(client)

    assert client.post(FRAGMENT_URL).status_code == 405


def test_fragment_counts_a_source_once_it_publishes(client, authenticate_viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3000)))
    authenticate_viewer(client)

    content = client.get(FRAGMENT_URL, headers={"HX-Request": "true"}).content.decode()

    assert "Ühendatud andmeallikaid: 1/4." in content
    assert "Ühendatud" in content
    assert "Andmeallikas ei ole veel ühendatud." not in content


def test_fragment_reports_a_stale_source_after_a_failed_check(client, authenticate_viewer):
    synchronize_membership(collector=collector_returning(membership_collection(3000)))
    synchronize_membership(
        collector=collector_raising(MembershipCollectionError("Allikat ei leitud (404)."))
    )
    authenticate_viewer(client)

    content = client.get(FRAGMENT_URL, headers={"HX-Request": "true"}).content.decode()

    # The published observation is still current, so the source stays counted;
    # the failed check is disclosed rather than hidden.
    assert "Ühendatud andmeallikaid: 1/4. Vananenud: 1." in content
    assert "Vananenud" in content


def test_fragment_does_not_count_a_source_that_only_failed(client, authenticate_viewer):
    synchronize_membership(
        collector=collector_raising(MembershipCollectionError("Allikat ei leitud (404)."))
    )
    authenticate_viewer(client)

    content = client.get(FRAGMENT_URL, headers={"HX-Request": "true"}).content.decode()

    # A source that never published anything is not connected, however many
    # times it has been checked.
    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Ühendamata" in content
