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


def positive_int_env(name: str) -> int:
    raw_value = required_env(name)
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ImproperlyConfigured(
            f"Environment variable must be a positive integer: {name}"
        ) from error
    if value < 1:
        raise ImproperlyConfigured(f"Environment variable must be a positive integer: {name}")
    return value


def boolean_env(name: str) -> bool:
    raw_value = required_env(name).lower()
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ImproperlyConfigured(f"Environment variable must be true or false: {name}")
