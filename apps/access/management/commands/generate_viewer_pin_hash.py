import getpass
import re

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate a Django password hash for a hidden four-digit viewer PIN."

    def handle(self, *args, **options):
        pin = getpass.getpass("PIN-kood: ")
        if not re.fullmatch(r"\d{4}", pin):
            raise CommandError("PIN-kood peab koosnema täpselt neljast numbrist.")
        self.stdout.write(make_password(pin))
