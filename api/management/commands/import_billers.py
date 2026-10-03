from django.core.management.base import BaseCommand, CommandError

from api.biller_import import parse_rows
from api.models import Biller


class Command(BaseCommand):
    help = 'Add or update billers from a CSV file (see api/biller_import.py for the columns).'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', help='Path to the CSV, e.g. billers_cdo.csv')
        parser.add_argument('--dry-run', action='store_true', help='Check the file and show what would change, save nothing.')

    def handle(self, *args, **opts):
        try:
            # utf-8-sig: Excel adds an invisible marker at the start of CSV files
            with open(opts['csv_path'], newline='', encoding='utf-8-sig') as f:
                rows, errors = parse_rows(f)
        except FileNotFoundError:
            raise CommandError(f'File not found: {opts["csv_path"]}')

        if errors:
            for e in errors:
                self.stderr.write(self.style.ERROR(e))
            raise CommandError(f'{len(errors)} problem(s) in the file. Nothing was saved.')

        created = updated = 0
        for row in rows:
            name = row.pop('name')
            existing = Biller.objects.filter(name__iexact=name).first()
            if existing is None:
                created += 1
                if not opts['dry_run']:
                    Biller.objects.create(name=name, **row)
                self.stdout.write(f'  + {name}')
            else:
                updated += 1
                if not opts['dry_run']:
                    for field, value in row.items():
                        setattr(existing, field, value)
                    existing.save()
                self.stdout.write(f'  ~ {existing.name}')

        prefix = 'Dry run: would have ' if opts['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(f'{prefix}added {created}, updated {updated}.'))
