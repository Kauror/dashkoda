import os

from django.core.exceptions import ImproperlyConfigured


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(f"Required environment variable is missing: {name}")
    return value


def comma_separated_env(name: str) -> list[str]:
    values = [value.strip() for value in required_env(name).split(",") if value.strip()]
    if not values:
        raise ImproperlyConfigured(f"Environment variable must contain a value: {name}")
    return values
