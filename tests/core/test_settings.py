import importlib
import sys


def test_nonproduction_settings_modules_are_importable():
    for module_name in (
        "config.settings.base",
        "config.settings.local",
        "config.settings.test",
    ):
        assert importlib.import_module(module_name)


def test_production_settings_are_importable_with_required_secret(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "injected-production-secret")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "dash.orgusaar.ee")
    sys.modules.pop("config.settings.production", None)

    production = importlib.import_module("config.settings.production")

    assert production.DEBUG is False
    assert production.SECRET_KEY == "injected-production-secret"
    assert production.ALLOWED_HOSTS == ["dash.orgusaar.ee"]


def test_base_settings_use_estonian_locale_and_no_persistent_database():
    base = importlib.import_module("config.settings.base")

    assert base.LANGUAGE_CODE == "et"
    assert base.TIME_ZONE == "Europe/Tallinn"
    assert base.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"
