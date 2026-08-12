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


def optional_boolean_env(name: str, *, default: bool) -> bool:
    """Like :func:`boolean_env`, but absence is allowed and means ``default``.

    A value that is present and unreadable is still refused. `os.environ.get(...)
    == "true"` would quietly treat `ture` -- or `True`, or `1` -- as false, and a
    development machine silently trusting no proxy header is exactly the kind of
    difference from production that is discovered late.
    """
    if name not in os.environ:
        return default
    return boolean_env(name)
