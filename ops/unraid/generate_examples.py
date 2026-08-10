"""Generate the Unraid wrapper examples from one description of the schedule.

The wrappers were written one at a time and drifted: some named both Compose
files and some did not, some carried the hour guard the host actually needs and
some described a Tallinn-capable host that does not exist, and when the schedule
moved to 05:30–06:30 every one of them still documented the old times.

So the schedule lives here once, in `CHAIN`, and every example is generated from
it. `tests/core/test_ops_wrappers.py` regenerates them and fails if the files on
disk disagree, which is what stops the next schedule change from leaving eleven
files behind.

Run after changing `CHAIN`:

    uv run python ops/unraid/generate_examples.py
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).parent


@dataclass(frozen=True)
class Job:
    """One scheduled wrapper: when it runs and what it runs."""

    name: str
    purpose: str
    command: str
    #: Wall-clock time in Europe/Tallinn. The UTC pair is derived from it.
    tallinn: str
    #: What the whole line of prose about ordering should say.
    ordering: str
    exit_codes: list[str]
    #: Extra paragraphs, each already wrapped, placed before the exit codes.
    notes: list[str] = field(default_factory=list)

    @property
    def hour(self) -> int:
        return int(self.tallinn.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.tallinn.split(":")[1])

    @property
    def guard(self) -> str:
        return f"{self.hour:02d}"

    @property
    def utc_summer(self) -> str:
        """EEST is UTC+3."""
        return f"{self.minute} {(self.hour - 3) % 24}"

    @property
    def utc_winter(self) -> str:
        """EET is UTC+2."""
        return f"{self.minute} {(self.hour - 2) % 24}"

    @property
    def summer_hhmm(self) -> str:
        return f"{(self.hour - 3) % 24:02d}:{self.minute:02d}"

    @property
    def winter_hhmm(self) -> str:
        return f"{(self.hour - 2) % 24:02d}:{self.minute:02d}"

    @property
    def lock(self) -> str:
        return "/tmp/dashkoda-" + self.name.replace("_", "-") + ".lock"


#: The whole morning chain, in the order it runs. Changing a time here is the
#: only edit needed; the cron lines, the guard hours and the prose all follow.
CHAIN: tuple[Job, ...] = (
    Job(
        name="sync_ga4",
        purpose="DashKoda Google Analytics reconciliation of recent reporting days.",
        command="sync_ga4 --json",
        tallinn="05:15",
        ordering=(
            "First of the morning, fifteen minutes before the chain. It shares\n"
            "nothing with the feeds — no snapshot, no lock, no source — so it neither\n"
            "waits for them nor holds them up, and the traffic figures are published\n"
            "before anything else starts writing."
        ),
        exit_codes=[
            "0  days were published, nothing had changed, or a successful dry run",
            "1  failed — every day already published stays published and the",
            "   dashboard says the last check failed",
            "3  another collection was still running",
        ],
        notes=[
            "This reconciles the last eight completed days, not just yesterday. GA4\n"
            "keeps adjusting a day for about a week after it ends, and a day whose\n"
            "figures have changed is republished as a new revision that names the one\n"
            "it replaces. A day that has not changed publishes nothing at all.",
            "It is three API requests. The five-year historical import is the same\n"
            "command with a date range and is run by hand, once — never from here.",
            "The property ID and the service-account key file are read from the\n"
            "container's own environment, so neither can enter shell history or a\n"
            "process listing. The key is mounted read-only and belongs to a\n"
            "credential that can only read.",
            "The JSON is counts and a date window. Never a page path, a property ID,\n"
            "a credential path or any part of Google's response.",
        ],
    ),
    Job(
        name="sync_smaily",
        purpose="DashKoda Smaily newsletter list sizes.",
        command="sync_smaily --json",
        tallinn="05:20",
        ordering=(
            "Five minutes after the traffic reconciliation and ten before the chain.\n"
            "It shares nothing with either — its own lock, its own source — so the\n"
            "offset is for readable logs rather than for correctness."
        ),
        exit_codes=[
            "0  the reading was published, nothing had changed, or a successful dry run",
            "1  failed — the last good reading stays published and the dashboard",
            "   says the last check failed",
            "3  another collection was still running",
        ],
        notes=[
            "One API request. It reads the size of every segment in the account and\n"
            "publishes a reading only when a list has actually changed size.",
            "There is no backfill and there cannot be one. Smaily reports what a list\n"
            "holds now and has no endpoint for what it held last year, so newsletter\n"
            "history starts on the day this schedule started and grows forward. A\n"
            "missed day is a day nobody can recover.",
            "Every request is a GET. The integration cannot create, send, modify or\n"
            "delete anything — not because the credential is limited, but because no\n"
            "code path exists that would build such a request.",
            "The subdomain, API user and password are read from the container's own\n"
            "environment, so none can enter shell history or a process listing.",
            "The JSON is counts and, when a newsletter has no figure, the metric key\n"
            "and DashKoda's own sentence explaining why. Never an address, a\n"
            "subscriber, a segment name or any part of Smaily's response.",
        ],
    ),
    Job(
        name="sync_oigusloome_public",
        purpose="DashKoda legal-work synchronisation over the public read-only OneDrive link.",
        command="sync_oigusloome_public --json",
        tallinn="05:30",
        ordering=(
            "First in the chain. Every matcher scores against whichever legal\n"
            "snapshot is current when it runs, so the workbook is refreshed before\n"
            "anything reads it."
        ),
        exit_codes=[
            "0  imported, unchanged, or a successful dry run",
            "1  failed — the previous snapshot stays published and the dashboard",
            "   says the last check failed",
            "3  another synchronisation was still running",
        ],
        notes=[
            "The sharing URL is never passed on the command line: the container reads\n"
            "it from its own environment, so it stays out of shell history and process\n"
            "listings. Treat it as a bearer-style secret."
        ],
    ),
    Job(
        name="sync_event_programme",
        purpose="DashKoda event-programme synchronisation over the public read-only OneDrive link.",
        command="sync_event_programme --json",
        tallinn="05:35",
        ordering=(
            "Five minutes after the workbook. The two take different advisory locks\n"
            "and could safely overlap; the offset is for readable logs."
        ),
        exit_codes=[
            "0  imported, unchanged, or a successful dry run",
            "1  failed — the previous snapshot stays published and the dashboard",
            "   says the last check failed",
            "3  another synchronisation was still running",
        ],
        notes=[
            "The sharing URL is never passed on the command line: the container reads\n"
            "it from its own environment, so it stays out of shell history and process\n"
            "listings. Treat it as a bearer-style secret."
        ],
    ),
    Job(
        name="sync_koda_public",
        purpose="DashKoda public Koda.ee feed collection: member count, news and events.",
        command="sync_koda_public --source all --json",
        tallinn="05:40",
        ordering="Needs no credential at all: all three endpoints are anonymous and read-only.",
        exit_codes=[
            "0  every source imported or was unchanged",
            "1  every source failed",
            "2  degraded — at least one source failed while another succeeded; the",
            "   sources that succeeded have published, and the failed one kept its",
            "   previous good data",
            "3  no source could take its lock, so another run is still in progress",
        ],
        notes=[
            "Each of the three sources runs under its own PostgreSQL advisory lock, so\n"
            "one failing source does not stop the other two."
        ],
    ),
    Job(
        name="sync_legal_current_topics",
        purpose='DashKoda Koda.ee "Hetkel käsil" catalogue collection.',
        command="sync_legal_current_topics --json",
        tallinn="05:45",
        ordering=(
            "After the workbook, because matching scores against whatever legal\n"
            "snapshot is current. Collecting first is not wrong — it simply answers\n"
            "about yesterday's records — so the offset makes the ordinary case the\n"
            "useful one."
        ),
        exit_codes=[
            "0  imported, or unchanged",
            "1  collection failed; the previous catalogue is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
    ),
    Job(
        name="match_legal_current_topics",
        purpose=(
            "DashKoda automatic matching of open legal-work records against the "
            "current-topic catalogue."
        ),
        command="match_legal_current_topics --json",
        tallinn="05:50",
        ordering="Step two of two: it needs both the workbook and the catalogue above.",
        exit_codes=[
            "0  a new match snapshot was published, or the inputs were unchanged",
            "1  matching failed, or a required current snapshot was missing; the",
            "   previous match snapshot is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
    ),
    Job(
        name="sync_legal_archived_topics",
        purpose="DashKoda archive consultation fallback, step one of two.",
        command="sync_legal_archived_topics --json",
        tallinn="06:00",
        ordering=(
            "The long one. An incremental walk reads two pages; a full walk reads 143,\n"
            "which is why its matcher sits fifteen minutes later rather than five."
        ),
        exit_codes=[
            "0  imported, or unchanged",
            "1  failed; the previous archive data is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
    ),
    Job(
        name="match_legal_archived_topics",
        purpose="DashKoda archive consultation fallback, step two of two.",
        command="match_legal_archived_topics --json",
        tallinn="06:15",
        ordering=(
            "After the archive collection, and after the current-topic matcher whose\n"
            "snapshot it reads."
        ),
        exit_codes=[
            "0  imported, or unchanged",
            "1  failed; the previous archive data is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
    ),
    Job(
        name="sync_legal_opinion_documents",
        purpose="DashKoda opinion document catalogue collection.",
        command="sync_legal_opinion_documents --json",
        tallinn="06:20",
        ordering=(
            "Before the opinion matcher. Without this the matcher runs every morning\n"
            "against a catalogue that never gains a document, and opinions sent since\n"
            "the last drop show no link at all."
        ),
        exit_codes=[
            "0  imported, or unchanged",
            "1  collection failed; the previous catalogue is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
        notes=[
            "Reads the PDFs an operator has placed in the configured source directory.\n"
            "Nothing is fetched from a remote host, and the private blob store is never\n"
            "served: the only way to read a document is the permission-guarded route."
        ],
    ),
    Job(
        name="sync_public_opinions",
        purpose="DashKoda public Koda.ee opinion corpus collection.",
        command="sync_public_opinions --json",
        tallinn="06:25",
        ordering=(
            "After the private opinion catalogue and before the opinion matcher: the\n"
            "matcher reads whichever corpus is current when it runs, so both sources\n"
            "are refreshed first."
        ),
        exit_codes=[
            "0  imported, or unchanged",
            "1  collection failed; the previous corpus is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
        notes=[
            "Incremental by default: the listing edge plus a short refresh overlap.\n"
            "The historical 2025+ walk runs once with --full before the schedule is\n"
            "useful, and identical corpus content publishes nothing."
        ],
    ),
    Job(
        name="match_legal_opinion_documents",
        purpose="DashKoda matching of sent legal-work records against opinion documents.",
        command="match_legal_opinion_documents --json",
        tallinn="06:30",
        ordering=(
            "Last in the chain, and the reason the chain ends at 06:30: everything a\n"
            "reader sees at 07:00 is already published."
        ),
        exit_codes=[
            "0  a new match snapshot was published, or the inputs were unchanged",
            "1  matching failed, or a required current snapshot was missing; the",
            "   previous match snapshot is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
        notes=[
            "The JSON is aggregates only: counts, a snapshot id and the matcher version.\n"
            "Never a topic, a filename, a recipient, a subject, document text or a path."
        ],
    ),
    Job(
        name="discover_koda_event_pages",
        purpose="DashKoda discovery of public Koda.ee event pages.",
        command="discover_koda_event_pages --json",
        tallinn="06:40",
        ordering=(
            "After the legal chain, and before the matcher below it. A different job\n"
            "from `sync_koda_public --source events`: that publishes the upcoming\n"
            "calendar, while this accumulates the addresses of pages for events that\n"
            "have already happened."
        ),
        exit_codes=[
            "0  discovered, or a successful dry run",
            "1  the sitemap could not be read; the catalogue is exactly as it was",
            "3  another discovery run was still going",
        ],
        notes=[
            "The ordinary run reads only pages it has never seen plus those outside the\n"
            "recheck window, which on a settled catalogue is a handful of requests. The\n"
            "initial backfill is `--full --max-detail-pages N`, run by hand once.",
            "Nothing is ever deleted. A page that 404s, or that a run simply did not\n"
            "reach, keeps its row — one bad fetch must not remove a working link.",
            "A run that hit its budget or failed a fetch reports `is_complete: false`\n"
            "with a warning code, so a partial crawl never passes as complete history.",
        ],
    ),
    Job(
        name="match_public_event_links",
        purpose="DashKoda matching of programme events against public Koda.ee pages.",
        command="match_public_event_links --json",
        tallinn="06:50",
        ordering=(
            "Ten minutes after discovery, so it scores against pages found this\n"
            "morning. Still before 07:00, so everything a reader sees is published."
        ),
        exit_codes=[
            "0  a new match snapshot was published, or a successful dry run",
            "1  matching failed, or no current programme snapshot exists; the",
            "   previous match snapshot is still published",
            "3  the lock was already held, so a previous run is still in progress",
        ],
        notes=[
            "Local and cheap: no network access, just arithmetic over rows already in\n"
            "the database. Safe to re-run at any time.",
            "It changes no programme field. The event-programme workbook remains the\n"
            "authority on an event's name, date, type, delivery mode, tag, service code\n"
            "and inclusion status; this only records which public page it points at.",
            "The JSON is counts, a snapshot id and the matcher version. Never an event\n"
            "name, a page title or a URL.",
        ],
    ),
)

TEMPLATE = """#!/bin/bash
#
# {purpose}
#
# GENERATED FILE — edit `ops/unraid/generate_examples.py` and re-run it.
# The schedule is described once there; these examples and the documented times
# are checked against each other by `tests/core/test_ops_wrappers.py`.
#
# Intended for the Unraid host's own scheduler. Copy this file, set the
# deployment directory, make it executable, and schedule it. This repository
# installs nothing and enables nothing.
#
#   cp {name}.sh.example \\
#     /boot/config/plugins/user.scripts/{name}.sh
#   chmod +x /boot/config/plugins/user.scripts/{name}.sh
#
# Run it by hand once before scheduling it, and check the exit code. The guard
# below would otherwise skip it outside its own hour:
#
#   DASHKODA_FORCE=1 DASHKODA_DEPLOYMENT_DIRECTORY=/mnt/user/appdata/dashkoda \\
#     /boot/config/plugins/user.scripts/{name}.sh; echo "exit=$?"
#
# Intended schedule — {tallinn} every day, Europe/Tallinn.
#
# The pilot host cannot express Tallinn time: `/etc/localtime` is absent, so the
# clock and crond both run on UTC. The job is installed as a PAIR of UTC entries
# and the hour guard runs only the occurrence that is {tallinn} in Tallinn:
#
#   {utc_summer} * * * /boot/config/plugins/user.scripts/{name}.sh &> /dev/null
#   {utc_winter} * * * /boot/config/plugins/user.scripts/{name}.sh &> /dev/null
#
#   summer (EEST, UTC+3): the {summer_hhmm} UTC entry runs, the {winter_hhmm} one skips
#   winter (EET,  UTC+2): the {winter_hhmm} UTC entry runs, the {summer_hhmm} one skips
#
# The skipped occurrence is a no-op. `Europe/Tallinn` is missing from that host's
# trimmed zoneinfo, so the guard reads `Europe/Athens`, which has identical
# EET/EEST offsets. On a host that really is on Europe/Tallinn, install a single
# entry at `{minute} {hour} * * *` and the guard passes anyway.
#
# The whole chain runs 05:30–06:30, so every figure is fresh before 07:00.
# `docs/operations-runbook.md` lists it in order.
#
# {ordering}
#
{notes}# Expected exit codes:
#
{exit_codes}
#
# This job takes its own PostgreSQL advisory lock, so it can neither block nor
# be blocked by another feed. The flock below is host-side defence in depth
# against two copies of this script overlapping.

set -euo pipefail

# Europe/Athens stands in for Europe/Tallinn: identical offsets, and present in
# the trimmed zoneinfo where Tallinn is not.
TALLINN_TZ="Europe/Athens"
TARGET_HOUR="{guard}"

if [ "${{DASHKODA_FORCE:-0}}" != "1" ]; then
  if [ "$(TZ="${{TALLINN_TZ}}" date +%H)" != "${{TARGET_HOUR}}" ]; then
    exit 0
  fi
fi

# Set this in the User Scripts environment, or edit the fallback below.
DEPLOYMENT_DIRECTORY="${{DASHKODA_DEPLOYMENT_DIRECTORY:-<DASHKODA_DEPLOYMENT_DIRECTORY>}}"

if [ ! -d "${{DEPLOYMENT_DIRECTORY}}" ]; then
  echo "Deployment directory not found: ${{DEPLOYMENT_DIRECTORY}}" >&2
  echo "Set DASHKODA_DEPLOYMENT_DIRECTORY or edit this script." >&2
  exit 1
fi

cd "${{DEPLOYMENT_DIRECTORY}}"

# Both Compose files are named explicitly when the deployment has a host
# override. Bare `docker compose` would recreate `web` without the host's bind
# mounts on a stack that was created from both.
COMPOSE_FILES=(-f compose.yaml)
if [ -f compose.unraid.yaml ]; then
  COMPOSE_FILES+=(-f compose.unraid.yaml)
fi

# Targets the Compose service by name rather than a generated container name,
# so a recreated container does not silently break the schedule.
exec flock --nonblock {lock} \\
  docker compose "${{COMPOSE_FILES[@]}}" exec -T web \\
    python manage.py {command}
"""


def render(job: Job) -> str:
    notes = "".join("# " + note.replace("\n", "\n# ") + "\n#\n" for note in job.notes)
    return TEMPLATE.format(
        purpose=job.purpose,
        name=job.name,
        tallinn=job.tallinn,
        utc_summer=job.utc_summer,
        utc_winter=job.utc_winter,
        summer_hhmm=job.summer_hhmm,
        winter_hhmm=job.winter_hhmm,
        minute=job.minute,
        hour=job.hour,
        guard=job.guard,
        ordering=job.ordering.replace("\n", "\n# "),
        notes=notes,
        exit_codes="\n".join(f"#   {line}" for line in job.exit_codes),
        lock=job.lock,
        command=job.command,
    )


def write_all() -> list[pathlib.Path]:
    written = []
    for job in CHAIN:
        path = HERE / f"{job.name}.sh.example"
        # LF explicitly. Without it a regeneration on Windows rewrites every
        # wrapper with CRLF and eleven files show up as changed when nothing is.
        path.write_text(render(job), encoding="utf-8", newline=chr(10))
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write_all():
        print(f"wrote {path.relative_to(HERE.parent.parent)}")
