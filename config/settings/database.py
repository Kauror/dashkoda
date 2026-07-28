from django.core.exceptions import ImproperlyConfigured

from .env import required_env


def postgres_database(*, persistent_connections: bool) -> dict[str, object]:
    port_value = required_env("POSTGRES_PORT")
    try:
        port = int(port_value)
    except ValueError as error:
        raise ImproperlyConfigured("POSTGRES_PORT must be an integer") from error

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required_env("POSTGRES_DB"),
        "USER": required_env("POSTGRES_USER"),
        "PASSWORD": required_env("POSTGRES_PASSWORD"),
        "HOST": required_env("POSTGRES_HOST"),
        "PORT": port,
        "CONN_MAX_AGE": 60 if persistent_connections else 0,
        "OPTIONS": {
            "connect_timeout": 5,
        },
    }
