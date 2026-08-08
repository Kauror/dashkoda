"""The wrapper examples and the documented schedule must agree.

This file exists because they stopped agreeing, twice.

The wrappers were written one at a time, so some named both Compose files and
some did not, and some carried the hour guard the pilot host actually needs
while others described a Tallinn-capable host that does not exist. Then the
morning chain moved from 07:00–08:00 to 05:30–06:30 and **eleven files and five
documents were left describing the old times** — including files corrected days
earlier for exactly that kind of drift.

Documentation that disagrees with the deployment is not a small problem here: an
operator copies one of these files onto a host and schedules it. So the schedule
is described once, in `ops/unraid/generate_examples.py`, and these tests fail if
anything on disk has wandered away from it.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
OPS = REPO / "ops" / "unraid"
sys.path.insert(0, str(OPS))

from generate_examples import CHAIN, render  # noqa: E402

#: Examples that are not part of the generated chain, and why.
UNGENERATED = {
    "backup_db.sh.example": "UTC-anchored, no hour guard, not part of the chain",
    "sync_ga4.sh.example": "deliberately unscheduled: GA4 is not enabled",
}


class TestTheGeneratedExamplesAreCurrent:
    @pytest.mark.parametrize("job", CHAIN, ids=lambda j: j.name)
    def test_the_file_on_disk_matches_the_generator(self, job):
        """Regenerate and compare. A stale file fails here, not in production."""
        path = OPS / f"{job.name}.sh.example"

        assert path.exists(), f"{path.name} is missing; run generate_examples.py"
        assert path.read_text(encoding="utf-8") == render(job), (
            f"{path.name} is out of date. Run:\n    uv run python ops/unraid/generate_examples.py"
        )

    def test_every_example_is_either_generated_or_explained(self):
        on_disk = {p.name for p in OPS.glob("*.sh.example")}
        generated = {f"{job.name}.sh.example" for job in CHAIN}

        assert on_disk == generated | set(UNGENERATED), (
            "an example exists that is neither generated nor listed as an exception in UNGENERATED"
        )


class TestTheChainIsCoherent:
    """Properties the schedule has to hold whatever the times are."""

    def test_every_job_runs_exactly_once_per_season(self):
        """The UTC pair plus the hour guard must resolve to one run, both seasons.

        This is the whole reason the pair exists. A job whose guard disagrees
        with its cron entries either runs twice a day or never.
        """
        for job in CHAIN:
            summer = [int(job.utc_summer.split()[1]) + 3, int(job.utc_winter.split()[1]) + 3]
            winter = [int(job.utc_summer.split()[1]) + 2, int(job.utc_winter.split()[1]) + 2]

            assert sum(1 for h in summer if h % 24 == job.hour) == 1, f"{job.name} summer"
            assert sum(1 for h in winter if h % 24 == job.hour) == 1, f"{job.name} winter"

    def test_the_chain_is_ordered_and_fits_before_seven(self):
        minutes = [job.hour * 60 + job.minute for job in CHAIN]

        assert minutes == sorted(minutes), "CHAIN is not in running order"
        assert minutes[0] >= 5 * 60 + 30, "the chain starts before 05:30"
        # The invariant is the reader at 07:00, not any particular end time. The
        # chain grew past 06:30 when event-link discovery and matching joined it.
        assert minutes[-1] < 7 * 60, "the chain finishes at or after 07:00"

    def test_no_two_jobs_share_a_minute(self):
        slots = [job.tallinn for job in CHAIN]

        assert len(set(slots)) == len(slots), "two jobs are scheduled at the same minute"

    def test_each_collector_runs_before_the_matcher_that_reads_it(self):
        at = {job.name: job.hour * 60 + job.minute for job in CHAIN}

        assert at["sync_oigusloome_public"] == min(at.values()), (
            "the workbook must be first: every matcher scores against the current legal snapshot"
        )
        for collector, matcher in [
            ("sync_legal_current_topics", "match_legal_current_topics"),
            ("sync_legal_archived_topics", "match_legal_archived_topics"),
            ("sync_legal_opinion_documents", "match_legal_opinion_documents"),
            ("sync_event_programme", "match_public_event_links"),
            ("discover_koda_event_pages", "match_public_event_links"),
        ]:
            assert at[collector] < at[matcher], f"{matcher} runs before {collector}"

    def test_the_archive_walk_gets_more_room_than_the_others(self):
        """A full walk is 143 pages; five minutes is not enough for it."""
        at = {job.name: job.hour * 60 + job.minute for job in CHAIN}
        gap = at["match_legal_archived_topics"] - at["sync_legal_archived_topics"]

        assert gap >= 15, f"only {gap} minutes for a full archive walk"

    def test_every_job_takes_a_lock_of_its_own(self):
        locks = [job.lock for job in CHAIN]

        assert len(set(locks)) == len(locks), "two jobs share a flock file"


class TestWhatTheExamplesMayNotContain:
    """These files are copied onto a host. They carry no secret and no host path."""

    @pytest.mark.parametrize("path", sorted(OPS.glob("*.sh.example")), ids=lambda p: p.name)
    def test_no_secret_or_real_sharing_url(self, path):
        body = path.read_text(encoding="utf-8")

        for forbidden in ("sharepoint.com", "1drv.ms", "onedrive.live.com", "?e="):
            assert forbidden not in body, f"{path.name} carries a sharing-URL fragment"
        for forbidden in ("PIN", "pbkdf2", "Bearer", "token="):
            assert forbidden not in body, f"{path.name} carries something credential-shaped"

    @pytest.mark.parametrize("path", sorted(OPS.glob("*.sh.example")), ids=lambda p: p.name)
    def test_the_deployment_directory_is_a_placeholder(self, path):
        body = path.read_text(encoding="utf-8")

        assert "DASHKODA_DEPLOYMENT_DIRECTORY" in body
        # The real path may appear in a documented example command, never as the
        # value the script would actually use.
        assert 'DEPLOYMENT_DIRECTORY="/mnt' not in body

    @pytest.mark.parametrize("path", sorted(OPS.glob("*.sh.example")), ids=lambda p: p.name)
    def test_both_compose_files_are_named(self, path):
        """A bare `docker compose` recreates web without the host's bind mounts."""
        body = path.read_text(encoding="utf-8")

        if "docker compose" not in body:
            pytest.skip("does not drive Compose")
        assert "compose.unraid.yaml" in body, f"{path.name} names only one Compose file"

    @pytest.mark.parametrize("path", sorted(OPS.glob("*.sh.example")), ids=lambda p: p.name)
    def test_it_says_the_repository_installs_nothing(self, path):
        body = path.read_text(encoding="utf-8")

        assert "installs nothing" in body or "not scheduled" in body


class TestTheDocumentedTimesMatchTheExamples:
    """The runbook is what an operator reads before touching the host."""

    def test_the_runbook_lists_every_job_at_its_generated_time(self):
        runbook = (REPO / "docs" / "operations-runbook.md").read_text(encoding="utf-8")
        schedule = re.search(r"<!-- SCHEDULE:BEGIN -->(.*?)<!-- SCHEDULE:END -->", runbook, re.S)

        assert schedule, "the runbook has no generated schedule block"
        for job in CHAIN:
            assert job.tallinn in schedule.group(1), f"{job.name} time missing"
            assert job.name in schedule.group(1), f"{job.name} missing from the runbook"

    def test_no_document_still_claims_the_retired_morning_chain(self):
        """The chain used to run 07:00–08:00. Nothing may still say so."""
        retired = ("07:05", "07:10", "07:15", "07:20", "07:25", "07:40")
        offenders = []

        for path in sorted((REPO / "docs").glob("*.md")):
            body = path.read_text(encoding="utf-8")
            for time in retired:
                if time in body:
                    offenders.append(f"{path.name}:{time}")

        assert not offenders, "retired schedule times still documented: " + ", ".join(offenders)
