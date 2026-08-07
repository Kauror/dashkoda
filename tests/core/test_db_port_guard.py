"""The CI guard that keeps PostgreSQL off the host network.

The guard this replaces could pass without ever establishing anything, so these
tests deliberately cover **both polarities**: a correct stack must pass, a
published port must fail, and an inspection that does not work must fail rather
than fall through to a vacuous success.

Nothing here runs Docker. `inspect_ports` takes its runner as an argument
precisely so the failure path is reachable in an ordinary test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from ops.check_db_port import (
    POSTGRES_PORT_SPEC,
    PortGuardError,
    assert_unpublished,
    check,
    main,
    parse_port_bindings,
    published_bindings,
)


@dataclass
class FakeCompleted:
    """The parts of `subprocess.CompletedProcess` the guard reads."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def runner_returning(payload, *, returncode: int = 0, stderr: str = ""):
    """A `subprocess.run` stand-in that answers with a fixed port map."""

    def run(command, **kwargs):
        run.command = command
        return FakeCompleted(
            returncode=returncode,
            stdout=payload if isinstance(payload, str) else json.dumps(payload),
            stderr=stderr,
        )

    return run


class TestCaseOnePortNotPublished:
    """CASE 1 — the database exists and 5432/tcp is not published: PASS."""

    def test_an_exposed_but_unpublished_port_passes(self):
        check("db-container", runner=runner_returning({"5432/tcp": None}))

    def test_a_port_that_was_never_exposed_passes(self):
        check("db-container", runner=runner_returning({}))

    def test_a_container_with_no_port_map_at_all_passes(self):
        check("db-container", runner=runner_returning("null"))

    def test_an_unrelated_published_port_does_not_fail_the_database_check(self):
        """The web container publishing 8000 says nothing about PostgreSQL."""
        check(
            "db-container",
            runner=runner_returning(
                {
                    "5432/tcp": None,
                    "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}],
                }
            ),
        )


class TestCaseTwoPublishedToLoopback:
    """CASE 2 — 5432/tcp published to 127.0.0.1: FAIL.

    Loopback is still the host. The rule is that the port is not published at
    all, not that it is published somewhere reassuring.
    """

    def test_it_fails(self):
        with pytest.raises(PortGuardError) as error:
            check(
                "db-container",
                runner=runner_returning(
                    {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}
                ),
            )
        assert "published to the host" in str(error.value)
        assert "127.0.0.1:5432" in str(error.value)


class TestCaseThreePublishedToEveryInterface:
    """CASE 3 — 5432/tcp published to 0.0.0.0: FAIL."""

    def test_it_fails(self):
        with pytest.raises(PortGuardError) as error:
            check(
                "db-container",
                runner=runner_returning({"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]}),
            )
        assert "published to the host" in str(error.value)

    def test_an_empty_host_ip_is_reported_as_every_interface(self):
        with pytest.raises(PortGuardError) as error:
            check(
                "db-container",
                runner=runner_returning({"5432/tcp": [{"HostIp": "", "HostPort": "5432"}]}),
            )
        assert "0.0.0.0:5432" in str(error.value)

    def test_an_ipv6_publication_also_fails(self):
        with pytest.raises(PortGuardError):
            check(
                "db-container",
                runner=runner_returning({"5432/tcp": [{"HostIp": "::", "HostPort": "5432"}]}),
            )


class TestCaseFourInspectionCannotBePerformed:
    """CASE 4 — the service or container cannot be inspected: FAIL.

    This is the case the old guard got wrong. Every one of these once produced
    empty stdout, and empty stdout used to mean success.
    """

    def test_a_failing_inspect_command_fails(self):
        with pytest.raises(PortGuardError) as error:
            check(
                "db-container",
                runner=runner_returning("", returncode=1, stderr="No such object: db-container"),
            )
        assert "docker inspect failed" in str(error.value)

    def test_an_unresolved_container_fails(self):
        """Compose answering nothing for the service is not a pass."""
        with pytest.raises(PortGuardError) as error:
            check("", runner=runner_returning({"5432/tcp": None}))
        assert "No database container was resolved" in str(error.value)

    def test_a_whitespace_only_container_fails(self):
        with pytest.raises(PortGuardError):
            check("   \n", runner=runner_returning({"5432/tcp": None}))

    def test_empty_output_from_a_successful_command_fails(self):
        """Exit zero with no document proves nothing either."""
        with pytest.raises(PortGuardError) as error:
            check("db-container", runner=runner_returning(""))
        assert "no output" in str(error.value)

    def test_unparsable_output_fails(self):
        with pytest.raises(PortGuardError) as error:
            check("db-container", runner=runner_returning("not json at all"))
        assert "Could not parse" in str(error.value)

    def test_output_of_the_wrong_shape_fails(self):
        with pytest.raises(PortGuardError) as error:
            check("db-container", runner=runner_returning("[1, 2, 3]"))
        assert "Expected a port map object" in str(error.value)

    def test_bindings_of_the_wrong_shape_fail(self):
        with pytest.raises(PortGuardError) as error:
            check("db-container", runner=runner_returning({"5432/tcp": "127.0.0.1:5432"}))
        assert "Expected a list of bindings" in str(error.value)


class TestTheColonZeroRegression:
    """The literal `:0` string must not be what the guard reasons about.

    Some Compose versions answer `:0` for an unpublished port. That is a fact
    about `docker compose port`, which this guard no longer calls — it reads the
    binding state directly, so the string cannot reach any decision.
    """

    def test_a_port_map_is_what_decides_not_a_compose_string(self):
        assert published_bindings(parse_port_bindings('{"5432/tcp": null}')) == []
        assert published_bindings({}, port_spec=POSTGRES_PORT_SPEC) == []

    def test_the_guard_never_invokes_compose_port(self):
        run = runner_returning({"5432/tcp": None})
        check("db-container", runner=run)
        assert run.command[:2] == ["docker", "inspect"]
        assert "port" not in run.command


class TestTheCommandLineEntryPoint:
    """CI calls `main`, so its exit codes are part of the contract."""

    def test_a_clean_stack_exits_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "ops.check_db_port.subprocess.run", runner_returning({"5432/tcp": None})
        )
        assert main(["--container", "db-container"]) == 0
        assert "guard passed" in capsys.readouterr().out

    def test_a_published_port_exits_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "ops.check_db_port.subprocess.run",
            runner_returning({"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]}),
        )
        assert main(["--container", "db-container"]) == 1
        assert "FAILED" in capsys.readouterr().err

    def test_a_failed_inspection_exits_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "ops.check_db_port.subprocess.run", runner_returning("", returncode=125)
        )
        assert main(["--container", "db-container"]) == 1
        assert "FAILED" in capsys.readouterr().err

    def test_an_empty_container_argument_exits_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "ops.check_db_port.subprocess.run", runner_returning({"5432/tcp": None})
        )
        assert main(["--container", ""]) == 1
        assert "FAILED" in capsys.readouterr().err


class TestAssertUnpublishedDirectly:
    """The pure predicate, independent of any subprocess plumbing."""

    def test_it_accepts_an_absent_key(self):
        assert_unpublished({"8000/tcp": None})

    def test_it_rejects_any_binding_at_all(self):
        with pytest.raises(PortGuardError):
            assert_unpublished({"5432/tcp": [{"HostIp": "10.0.0.5", "HostPort": "15432"}]})

    def test_it_rejects_a_binding_on_a_non_default_host_port(self):
        with pytest.raises(PortGuardError) as error:
            assert_unpublished({"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]})
        assert "127.0.0.1:55432" in str(error.value)

    def test_it_can_check_another_port(self):
        with pytest.raises(PortGuardError):
            assert_unpublished(
                {"6379/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6379"}]},
                port_spec="6379/tcp",
            )
