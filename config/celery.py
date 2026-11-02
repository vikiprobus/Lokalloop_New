import os

# Set default Django settings module for Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

try:
    from celery import Celery
    app = Celery('lokalloop')

    # Load configuration from Django settings, using CELERY_ namespace
    app.config_from_object('django.conf:settings', namespace='CELERY')

    # Auto-discover tasks across all installed Django apps
    app.autodiscover_tasks()

    # Periodic task schedule (Celery Beat)
    app.conf.beat_schedule = {
        'purge-error-logs-daily': {
            'task': 'config.tasks.celery_purge_error_logs',
            'schedule': 86400.0,  # Every 24 hours
            'kwargs': {'days': 15},
        },
        'expire-stale-qrcodes-hourly': {
            'task': 'config.tasks.celery_expire_stale_qrcodes',
            'schedule': 3600.0,  # Every 1 hour
        },
        'sync-daily-analytics-nightly': {
            'task': 'config.tasks.celery_sync_daily_analytics',
            'schedule': 86400.0,  # Daily at midnight
        },
    }
except ImportError:
    # Graceful fallback when celery is not installed in lightweight/zero-RAM mode
    app = None
