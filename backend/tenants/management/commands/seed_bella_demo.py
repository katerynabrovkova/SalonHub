"""
Fills the demo salon (slug "bella-demo") from the tables in
tenants/demo/bella_demo.py. Creates or updates only, never deletes, and
overwrites the seeded fields on every run (docs/DECISIONS.md § "Stage 17
sub-step order", 17.8 "Demo salon commands"). Development only.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenants.demo import bella_demo


class Command(BaseCommand):
    help = 'Create or update the demo content of the "bella-demo" salon (DEBUG only).'

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_bella_demo runs only with DEBUG on.")
        try:
            created = bella_demo.seed_bella_demo(now=timezone.now())
        except bella_demo.DemoDataError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Seeded {bella_demo.SALON_SLUG}. Rows created:")
        for label, count in created.items():
            self.stdout.write(f"  {label}: {count}")
