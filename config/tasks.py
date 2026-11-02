from services.background_tasks import (
    purge_old_error_logs_job,
    expire_stale_qrcodes_job,
    sync_daily_analytics_job
)

try:
    from celery import shared_task

    @shared_task(name='config.tasks.celery_purge_error_logs')
    def celery_purge_error_logs(days=15):
        """Asynchronous Celery task for error log retention cleanup."""
        return purge_old_error_logs_job(days=days)

    @shared_task(name='config.tasks.celery_expire_stale_qrcodes')
    def celery_expire_stale_qrcodes():
        """Asynchronous Celery task for QR expiration."""
        return expire_stale_qrcodes_job()

    @shared_task(name='config.tasks.celery_sync_daily_analytics')
    def celery_sync_daily_analytics(target_date=None):
        """Asynchronous Celery task for aggregating daily store footfall and sales metrics."""
        return sync_daily_analytics_job(target_date=target_date)

except ImportError:
    # When celery is not present, stub callable functions so imports never break
    def celery_purge_error_logs(days=15):
        return purge_old_error_logs_job(days=days)

    def celery_expire_stale_qrcodes():
        return expire_stale_qrcodes_job()

    def celery_sync_daily_analytics(target_date=None):
        return sync_daily_analytics_job(target_date=target_date)
