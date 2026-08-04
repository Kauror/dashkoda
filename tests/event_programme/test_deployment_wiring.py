"""The sharing URL has to reach the container, and only the container.

Three places have to agree before a production import can work, and two of them
are not Python:

- `config/settings/base.py` reads `EVENT_PROGRAMME_PUBLIC_URL` from the process
  environment;
- `.env.example` documents it, blank, so an operator knows to supply it;
- `compose.yaml` names it under the web service's `environment:`.

The third is the one that silently does not fail. Compose reads `.env` for
**interpolation**, so a variable present in the file but never referenced under
`environment:` is simply absent inside the container: the application starts
perfectly, the dashboard looks fine, and `sync_event_programme` reports the URL
as missing however correct the environment file is. That gap shipped once and is
what this module exists to prevent.
"""

from __future__ import annotations

import pathlib

from django.conf import settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SETTING = "EVENT_PROGRAMME_PUBLIC_URL"


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_the_setting_exists_and_defaults_to_blank():
    """Ordinary startup must succeed without it; only the command needs it."""
    assert hasattr(settings, SETTING)


def test_compose_passes_the_sharing_url_into_the_web_container():
    compose = _read("compose.yaml")

    assert f"{SETTING}: ${{{SETTING}:-}}" in compose, (
        f"{SETTING} must be named under the web service's environment, or the "
        "value in the server's .env never reaches the container"
    )


def test_the_environment_file_documents_it_blank():
    text = _read(".env.example")

    assert f"{SETTING}=" in text
    # Documented, never carrying a value: this file is committed.
    for line in text.splitlines():
        if line.startswith(f"{SETTING}="):
            assert line == f"{SETTING}=", "no sharing URL may be committed"
            break
    else:  # pragma: no cover - the assertion above already failed
        raise AssertionError(f"{SETTING} is not documented in .env.example")


def test_no_sharing_url_is_committed_anywhere_in_the_deployment_files():
    """A sharing link is a bearer-style secret. None may appear in Git."""
    for name in ("compose.yaml", "compose.dev.yaml", ".env.example"):
        text = _read(name)
        assert "sharepoint.com" not in text
        assert "1drv.ms" not in text
        assert "my.sharepoint" not in text


def test_the_command_accepts_no_url_argument():
    """The URL comes from the environment only, so it cannot enter shell history
    or a process listing.

    Asserted against the real argument parser rather than the source text, which
    mentions `--url` in the docstring explaining why there isn't one.
    """
    from django.core.management import load_command_class

    command = load_command_class("apps.event_programme", "sync_event_programme")
    parser = command.create_parser("manage.py", "sync_event_programme")

    destinations = {action.dest for action in parser._actions}
    flags = {option for action in parser._actions for option in action.option_strings}

    assert "url" not in destinations
    assert not any("url" in flag for flag in flags)
