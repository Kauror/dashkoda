"""Check that the managed opinion store still holds what the database claims.

Re-reads every blob the current catalogue depends on and re-hashes it. Content
addressing makes that the whole verification: a file that no longer hashes to
its own name is corrupt, missing, or was replaced.

It **reports and never repairs**. Deleting or rewriting a blob because it looks
wrong is exactly the action that turns a recoverable storage fault into data
loss, and it is an operator's decision with a backup in hand — not a command's.

Exit codes:

    0  every checked blob is intact
    1  at least one problem was found
"""

import json

from django.core.management.base import BaseCommand

from apps.legal_work.opinion_models import OpinionCatalogueSnapshot, OpinionDocumentBlob
from apps.legal_work.opinion_storage import resolve_within_store, store_usage, verify_blob
from apps.legal_work.sync import EXIT_FAILED


class Command(BaseCommand):
    help = "Verify the managed opinion blob store against the database. Read-only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit one structured JSON line instead of prose.",
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]

        snapshot = OpinionCatalogueSnapshot.objects.filter(is_current=True).first()
        blobs = OpinionDocumentBlob.objects.all().order_by("pk")

        checked = intact = missing = mismatched = wrong_size = escaped = 0
        for blob in blobs.iterator(chunk_size=200):
            checked += 1
            # The stored key must still resolve under the store root. A row that
            # points outside it is the one failure that matters most, because it
            # is the shape a path-traversal bug would take.
            try:
                resolve_within_store(blob.storage_key)
            except Exception:  # noqa: BLE001 - any refusal is the same finding
                escaped += 1
                continue

            ok, reason = verify_blob(blob.sha256, expected_size=blob.byte_size)
            if ok:
                intact += 1
            elif reason == "missing":
                missing += 1
            elif reason == "size_mismatch":
                wrong_size += 1
            else:
                mismatched += 1

        # A catalogue entry must not claim an extraction without a valid blob.
        inconsistent = 0
        if snapshot is not None:
            inconsistent = (
                snapshot.entries.filter(extraction__isnull=False, blob__isnull=True).count()
                + snapshot.entries.exclude(blob__isnull=True)
                .exclude(blob__validation_status="valid")
                .filter(extraction__isnull=False)
                .count()
            )

        usage = store_usage()
        problems = missing + mismatched + wrong_size + escaped + inconsistent

        payload = {
            "result": "ok" if problems == 0 else "problems_found",
            "snapshot_id": snapshot.pk if snapshot else None,
            "blobs_checked": checked,
            "blobs_intact": intact,
            "blobs_missing": missing,
            "blobs_digest_mismatch": mismatched,
            "blobs_size_mismatch": wrong_size,
            "keys_outside_store": escaped,
            "inconsistent_entries": inconsistent,
            "store_files": usage["blob_files"],
            "store_bytes": usage["blob_bytes"],
        }

        if as_json:
            # Counts only. Never a digest, a filename or the store path.
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            style = self.style.SUCCESS if problems == 0 else self.style.ERROR
            self.stdout.write(
                style(
                    f"Kontrollitud {checked} faili: korras {intact}, puudu {missing}, "
                    f"vale räsi {mismatched}, vale suurus {wrong_size}, "
                    f"hoidlast väljas {escaped}, vastuolulisi kirjeid {inconsistent}."
                )
            )

        if problems:
            raise SystemExit(EXIT_FAILED)
