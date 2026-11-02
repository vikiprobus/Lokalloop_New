from django.core.management.base import BaseCommand
from services.background_tasks import sync_daily_analytics_job
from datetime import datetime


class Command(BaseCommand):
    help = 'Aggregates verified counter redemptions and computed checkout sales into merchant ShopMetric records.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Target date to calculate metrics for (format YYYY-MM-DD, defaults to today)'
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        target_date = None
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format '{date_str}'. Use YYYY-MM-DD."))
                return

        self.stdout.write("Synchronizing daily merchant footfall & sales metrics...")
        res = sync_daily_analytics_job(target_date=target_date)
        synced_count = res['synced_shops_count']
        redemptions = res['total_redemptions']
        sales = res['total_sales']
        self.stdout.write(self.style.SUCCESS(
            f"Successfully synced metrics for {synced_count} stores ({redemptions} redemptions, INR {sales:.2f} total volume)."
        ))
