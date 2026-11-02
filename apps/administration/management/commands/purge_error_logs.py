from django.core.management.base import BaseCommand
from services.background_tasks import purge_old_error_logs_job


class Command(BaseCommand):
    help = 'Automatically purges error logs older than specified retention days and records audit log.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=15,
            help='Number of retention days before purging error logs (default: 15)'
        )

    def handle(self, *args, **options):
        days = options['days']
        self.stdout.write(f"Initiating automatic error log cleanup for records older than {days} days...")
        res = purge_old_error_logs_job(days=days)
        deleted_count = res['deleted_count']
        self.stdout.write(self.style.SUCCESS(f"Successfully purged {deleted_count} error log entries older than {days} days."))
