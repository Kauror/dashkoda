"""One-time resolution of the workbook's stable drive and item identifiers.

A sharing URL is not a runtime identifier: it can be revoked or regenerated,
and it is not something to keep in configuration. Run this once, put the
printed drive and item IDs into the environment, and never configure the URL.

Two resolution paths:

``--user`` + ``--path``
    Preferred. Works with the read-only ``Files.Read.All`` application
    permission, which is all the scheduled sync ever needs.

``--url``
    Fallback for when the path is not known. Microsoft's ``/shares/`` endpoint
    requires the broader ``Files.ReadWrite.All`` application permission, so use
    it only for this one-off lookup and do not leave that permission granted.

This command prints non-secret metadata only: never a token, an authorization
header, a client secret or a signed download URL.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.legal_work.graph import GraphClient, GraphError, GraphNotConfigured, load_graph_settings


class Command(BaseCommand):
    help = "Resolve the legal-work workbook to a stable drive ID and item ID."

    def add_arguments(self, parser):
        parser.add_argument("--url", help="Sharing URL for the workbook (one-time use).")
        parser.add_argument("--user", help="User principal name owning the OneDrive.")
        parser.add_argument("--path", help="Path to the workbook inside that OneDrive.")

    def handle(self, *args, **options):
        url = options.get("url")
        user = options.get("user")
        path = options.get("path")

        if not url and not (user and path):
            raise CommandError("Määra kas --url või --user koos --path väärtusega.")

        try:
            # The item is what we are resolving, so it is not required yet.
            client = GraphClient(load_graph_settings(require_item=False))
            remote = (
                client.resolve_user_path(user, path)
                if user and path
                else client.resolve_share_url(url)
            )
        except GraphNotConfigured as error:
            raise CommandError(str(error)) from error
        except GraphError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"name={remote.name}")
        self.stdout.write(f"OIGUSLOOME_DRIVE_ID={remote.drive_id}")
        self.stdout.write(f"OIGUSLOOME_ITEM_ID={remote.item_id}")
        self.stdout.write(f"size_bytes={remote.size_bytes}")
        self.stdout.write(
            f"last_modified={remote.modified_at.isoformat() if remote.modified_at else ''}"
        )
        if not remote.drive_id:
            self.stdout.write(
                self.style.WARNING(
                    "Graph ei tagastanud drive ID-d. Kasuta --user ja --path varianti."
                )
            )
