import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

PRODUCTION_ENVIRONMENT = (
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "VIEWER_PIN_HASH",
    "VIEWER_PIN_VERSION",
    "VIEWER_RATE_LIMIT_SECRET",
    "TRUST_CLOUDFLARE_IP_HEADER",
)


def test_test_settings_use_estonian_locale_and_postgresql():
    assert settings.LANGUAGE_CODE == "et"
    assert settings.TIME_ZONE == "Europe/Tallinn"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


@pytest.mark.parametrize("missing_name", PRODUCTION_ENVIRONMENT)
def test_production_settings_require_environment_variables(missing_name):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    environment.update(
        {
            "DJANGO_SECRET_KEY": "test-only-production-secret-with-sufficient-length",
            "DJANGO_ALLOWED_HOSTS": "dash.orgusaar.ee",
            "POSTGRES_DB": "dashkoda",
            "POSTGRES_USER": "dashkoda",
            "POSTGRES_PASSWORD": "test-only-password",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5432",
            "VIEWER_PIN_HASH": "synthetic-hash-placeholder",
            "VIEWER_PIN_VERSION": "1",
            "VIEWER_RATE_LIMIT_SECRET": "synthetic-rate-limit-secret",
            "TRUST_CLOUDFLARE_IP_HEADER": "false",
        }
    )
    environment.pop(missing_name)

    result = subprocess.run(
        [sys.executable, "-c", "import config.settings.production"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert missing_name in result.stderr


def test_production_settings_accept_complete_environment():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    environment.update(
        {
            "DJANGO_SECRET_KEY": "test-only-production-secret-with-sufficient-length",
            "DJANGO_ALLOWED_HOSTS": "dash.orgusaar.ee",
            "POSTGRES_DB": "dashkoda",
            "POSTGRES_USER": "dashkoda",
            "POSTGRES_PASSWORD": "test-only-password",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5432",
            "VIEWER_PIN_HASH": "synthetic-hash-placeholder",
            "VIEWER_PIN_VERSION": "1",
            "VIEWER_RATE_LIMIT_SECRET": "synthetic-rate-limit-secret",
            "TRUST_CLOUDFLARE_IP_HEADER": "false",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from config.settings import production as p;"
                "assert p.DEBUG is False;"
                "assert p.ALLOWED_HOSTS == ['dash.orgusaar.ee'];"
                "assert p.DATABASES['default']['ENGINE'] == "
                "'django.db.backends.postgresql'"
            ),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_gitignore_excludes_environment_files_but_keeps_example():
    gitignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")

    assert "\n.env\n" in f"\n{gitignore}"
    assert "\n.env.*\n" in f"\n{gitignore}"
    assert "\n!.env.example\n" in f"\n{gitignore}"
