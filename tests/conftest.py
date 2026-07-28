import pytest
from django.contrib.auth.hashers import make_password


@pytest.fixture(autouse=True)
def viewer_access_settings(settings):
    settings.VIEWER_PIN_HASH = make_password("8642")
    settings.VIEWER_PIN_VERSION = 3
    settings.VIEWER_RATE_LIMIT_SECRET = "synthetic-test-rate-limit-secret"
    settings.TRUST_CLOUDFLARE_IP_HEADER = False


@pytest.fixture
def viewer_pin():
    return "8642"


@pytest.fixture
def authenticate_viewer():
    def authenticate(client, *, version=3):
        session = client.session
        session["viewer_authenticated"] = True
        session["viewer_pin_version"] = version
        session.save()

    return authenticate
