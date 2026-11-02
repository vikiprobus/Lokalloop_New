from django.core.management.base import BaseCommand
from services.background_tasks import expire_stale_qrcodes_job


class Command(BaseCommand):
    help = 'Scans for expired QR codes, deactivates them, and records audit trail.'

    def handle(self, *args, **options):
        self.stdout.write("Scanning for stale and expired QR code tokens...")
        res = expire_stale_qrcodes_job()
        count = res['expired_count']
        offers_count = res.get('expired_offers_count', 0)
        self.stdout.write(self.style.SUCCESS(f"Successfully processed and deactivated {count} expired QR codes and expired {offers_count} campaigns."))
