"""Prove that a container publishes no host port for PostgreSQL.

`AGENTS.md` requires PostgreSQL to stay on the private backend network, and CI
is where that is meant to be caught. The check this replaces asked
`docker compose port db 5432` and failed the build only when the output was
non-empty, which made it vacuous in two separate ways:

- `2>/dev/null` swallowed every error, so a Compose invocation that failed
  outright — wrong project, service not running, Compose itself broken —
  produced empty stdout and *passed*;
- some Compose versions answer `:0` rather than nothing for an unpublished
  port, which is non-empty and would have *failed* a correctly configured
  stack.

So the guard never actually depended on the binding state. This module reads
the authoritative runtime representation instead — `.NetworkSettings.Ports`
from `docker inspect` — and treats every uncertainty as a failure. An
unpublished port is `null` there, or absent entirely; a published one carries
a list of `HostIp`/`HostPort` bindings, whatever address it was bound to.

The logic is split from the subprocess call so both halves are testable: see
`tests/core/test_db_port_guard.py`, which covers an unpublished port, a
loopback publication, a wildcard publication and an inspection that fails.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

#: The port that must never reach the host, in Docker's `port/protocol` form.
POSTGRES_PORT_SPEC = "5432/tcp"

INSPECT_FORMAT = "{{json .NetworkSettings.Ports}}"


class PortGuardError(RuntimeError):
    """The guard could not prove the port is unpublished.

    Raised both when a binding exists and when the state could not be
    established at all. The distinction matters to a reader, not to CI: either
    way the build must stop, because the whole point is that silence is not
    evidence.
    """


def inspect_ports(container: str, *, runner=subprocess.run) -> dict:
    """Return the container's port map, or raise.

    `runner` is injected so the failure path can be tested without Docker.
    """
    if not container or not container.strip():
        raise PortGuardError(
            "No database container was resolved. Compose returned nothing for the "
            "service, so there is no running container whose ports could be checked."
        )

    completed = runner(
        ["docker", "inspect", "--format", INSPECT_FORMAT, container.strip()],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PortGuardError(
            f"docker inspect failed for {container.strip()!r} "
            f"(exit {completed.returncode}): {(completed.stderr or '').strip()}"
        )

    return parse_port_bindings(completed.stdout)


def parse_port_bindings(raw: str) -> dict:
    """Parse the `.NetworkSettings.Ports` JSON document.

    A container with no ports at all inspects as `null`, which is a legitimate
    empty map rather than a parse failure.
    """
    text = (raw or "").strip()
    if not text:
        raise PortGuardError("docker inspect produced no output for the port map.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise PortGuardError(f"Could not parse the port map as JSON: {error}") from error

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise PortGuardError(f"Expected a port map object, got {type(parsed).__name__}.")
    return parsed


def published_bindings(ports: dict, *, port_spec: str = POSTGRES_PORT_SPEC) -> list:
    """Every host binding recorded for `port_spec`.

    An exposed-but-unpublished port is `null`; a port that was never exposed is
    absent. Both mean "no binding", so both return an empty list.
    """
    bindings = ports.get(port_spec)
    if bindings is None:
        return []
    if not isinstance(bindings, list):
        raise PortGuardError(
            f"Expected a list of bindings for {port_spec}, got {type(bindings).__name__}."
        )
    return bindings


def assert_unpublished(ports: dict, *, port_spec: str = POSTGRES_PORT_SPEC) -> None:
    """Raise unless `port_spec` has no host binding at all."""
    bindings = published_bindings(ports, port_spec=port_spec)
    if bindings:
        described = ", ".join(
            f"{binding.get('HostIp') or '0.0.0.0'}:{binding.get('HostPort') or '?'}"
            if isinstance(binding, dict)
            else repr(binding)
            for binding in bindings
        )
        raise PortGuardError(
            f"{port_spec} is published to the host ({described}). PostgreSQL must "
            "stay on the private backend network."
        )


def check(container: str, *, port_spec: str = POSTGRES_PORT_SPEC, runner=subprocess.run) -> None:
    """Full guard: resolve the port map and require no binding."""
    assert_unpublished(inspect_ports(container, runner=runner), port_spec=port_spec)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--container",
        required=True,
        help="Container id or name, normally from `docker compose ps --quiet db`.",
    )
    parser.add_argument(
        "--port",
        default=POSTGRES_PORT_SPEC,
        help=f"Port specification to check (default {POSTGRES_PORT_SPEC}).",
    )
    arguments = parser.parse_args(argv)

    try:
        check(arguments.container, port_spec=arguments.port)
    except PortGuardError as error:
        print(f"Database port guard FAILED: {error}", file=sys.stderr)
        return 1

    print(f"Database port guard passed: {arguments.port} has no host binding.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through `main`
    raise SystemExit(main())
