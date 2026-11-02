# Future Production Architecture Plan — LokalLoop

## 1. Migration from Prototype to Production

The codebase has been engineered with clean service and data boundaries so each prototype component can transition to enterprise-scale production infrastructure with minimal architectural disruption:

| Prototype Component | Current Implementation | Production Target | Transition Strategy |
| :--- | :--- | :--- | :--- |
| **Database** | SQLite 3 | PostgreSQL 16 + PostGIS | Replace `ENGINE` in `settings.py`; Run migrations; Swap `location_service.py` to use `django.contrib.gis.db.models.functions.Distance`. |
| **Geo-spatial Discovery** | Haversine formula in `services/location_service.py` | PostGIS Spatial Indexing (`ST_DWithin`) | Service boundary already isolates distance math; views and templates require zero changes. |
| **Payment Gateway** | Isolated demo checkout in `services/wallet_service.py` | Razorpay Orders & Webhooks | Replace `process_demo_package_purchase` with Razorpay API order creation and server-side HMAC-SHA256 signature webhook verification. |
| **Push Notifications** | SQLite-backed `Notification` model | Firebase Cloud Messaging (FCM) + Celery / Redis | Service boundary `services/notification_service.py` already isolates dispatch; inject FCM pySDK payload sender. |
| **Static & Media Files**| Local staticfiles | AWS S3 / Cloudflare R2 + CDN | Configure `django-storages` with S3 backend. |
| **Task Queue** | Synchronous database writes | Celery + Redis | Move notification dispatches and email receipts into asynchronous background tasks. |
| **Maps & Navigation** | Visual placeholder + Google Maps links | Mapbox GL JS / Google Maps SDK | Embed interactive JS map tiles into existing container divs. |
