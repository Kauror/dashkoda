"""Output mechanics shared by the scheduled feed commands.

Nine commands had written the same three things out by hand: the two output
flags, a five-line `_emit` that chose JSON or prose, and a ten-line block that
turned a held lock into a `locked` result and exit code 3. Identical every time,
because the scheduler's contract is identical every time.

What this mixin owns is exactly that much:

- the `--dry-run` and `--json` arguments;
- emitting one line, in the requested mode;
- the standard locked result and its exit code.

What it deliberately does **not** own:

- which synchronisation runs, and with what arguments;
- what goes in the payload. Every feed keeps its own contract — some hand back
  `outcome.as_dict()`, some build a narrower dictionary — and a shared payload
  schema is precisely how a feed's log would start carrying a field nobody
  meant to publish;
- what the success message says. That sentence is the feed's, and it is the
  part an operator actually reads;
- which extra exceptions a feed treats as an operator error rather than a
  failure — `PublicUrlNotConfigured` is the workbook feeds' business, not
  every feed's;
- any audit event, any feed state and any transaction.

There are no callbacks and no hooks. A command calls these methods where it
used to have the lines; nothing calls back into the command. That is the whole
difference between removing duplication and building a framework that every
future feed has to be bent into.
"""

from __future__ import annotations

import json
from typing import NoReturn

#: Exit codes the host scheduler reads. They live here because every feed
#: command shares them; `apps.legal_work.sync` re-exports them for the callers
#: and tests that have always imported them from there.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_LOCKED = 3

DEFAULT_JSON_HELP = "Emit one structured JSON line instead of prose."


class FeedCommandOutputMixin:
    """Mix into a `BaseCommand` that runs one scheduled feed."""

    def add_output_arguments(self, parser, *, dry_run_help: str) -> None:
        """The two flags every scheduled feed command takes.

        `dry_run_help` is required rather than defaulted: what a dry run
        actually does differs per feed — validate a workbook, walk a listing,
        query an API — and a generic sentence would be the kind of documentation
        that is true of nothing in particular.
        """
        parser.add_argument("--dry-run", action="store_true", help=dry_run_help)
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help=DEFAULT_JSON_HELP,
        )

    def emit(self, as_json: bool, payload: dict, message: str, *, style) -> None:
        """Write exactly one line, in the mode the operator asked for.

        JSON is sorted and non-ASCII is kept literal, so the line is stable
        enough to diff between runs and readable in a log without decoding.
        """
        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(style(message))

    def exit_locked(self, error: Exception, *, as_json: bool) -> NoReturn:
        """Report a run skipped because the feed's lock was held, and stop.

        Not a failure: a previous run is still going, which is the mechanism
        working. Exit code 3 is what lets a scheduler tell the two apart.

        Annotated `NoReturn` on purpose. Callers use it as the whole body of an
        `except` clause and go on to read a variable the `try` block bound, so
        it must be unmistakable — to a reader and to a type checker — that this
        never falls through.
        """
        self.emit(
            as_json,
            {"result": "locked", "detail": str(error)},
            f"Vahele jäetud: {error}",
            style=self.style.WARNING,
        )
        raise SystemExit(EXIT_LOCKED) from None


__all__ = [
    "DEFAULT_JSON_HELP",
    "EXIT_FAILED",
    "EXIT_LOCKED",
    "EXIT_OK",
    "FeedCommandOutputMixin",
]
