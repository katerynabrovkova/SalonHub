"""
Deletes the customer data of the demo salon (slug "bella-demo"):
appointments, payments, guest tokens, notifications, reviews, customers and
client accounts. Keeps the salon and its catalogue, its admin accounts, the
platform `User` rows, the daily assistant counters, and the test account
item8-new@example.com with its Customer (docs/DECISIONS.md § "Stage 17
sub-step order", 17.8 "Demo salon commands"). Development only; deletes
only with --yes.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from tenants.demo import bella_demo


class Command(BaseCommand):
    help = 'Delete the customer data of the "bella-demo" salon (DEBUG only, needs --yes).'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--yes", action="store_true", help="Actually delete; without it, only show the plan."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("reset_bella_demo runs only with DEBUG on.")
        try:
            if not options["yes"]:
                counts = bella_demo.plan_bella_demo_reset()
                self.stdout.write(
                    f"Would delete from {bella_demo.SALON_SLUG} (run again with --yes to delete):"
                )
            else:
                counts = bella_demo.reset_bella_demo()
                self.stdout.write(f"Deleted from {bella_demo.SALON_SLUG}:")
        except bella_demo.DemoDataError as exc:
            raise CommandError(str(exc)) from exc
        for label, count in counts.items():
            self.stdout.write(f"  {label}: {count}")
