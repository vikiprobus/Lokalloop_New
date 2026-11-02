# System Architecture — LokalLoop

## 1. High-Level Architecture

LokalLoop is an Indian local-commerce discovery and engagement platform built with Python, Django 5.1, SQLite (PostgreSQL/PostGIS-ready), and Django Templates.

```
                    ┌────────────────────────────┐
                    │      HTTP Clients          │
                    │ (Desktop / Mobile Browser) │
                    └─────────────┬──────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────┐
                    │  Django WSGI/ASGI Server   │
                    │   (Authentication, CSRF,   │
                    │    Session, Context Proc.) │
                    └─────────────┬──────────────┘
                                  │
      ┌───────────────────────────┼───────────────────────────┐
      ▼                           ▼                           ▼
┌──────────────┐           ┌──────────────┐           ┌──────────────┐
│ Public Web   │           │ Buyer Portal │           │ Seller Portal│
│ (/)          │           │ (/buyer/...) │           │ (/seller/...)│
└──────┬───────┘           └──────┬───────┘           └──────┬───────┘
       │                          │                          │
       └──────────────────────────┼──────────────────────────┘
                                  │ (Strict Role Boundary)
                                  ▼
                      ┌─────────────────────────┐
                      │      Service Layer      │
                      │ ─────────────────────── │
                      │ - redemption_service.py │
                      │ - wallet_service.py     │
                      │ - reward_service.py     │
                      │ - location_service.py   │
                      │ - notification_service  │
                      └───────────┬─────────────┘
                                  │
                                  ▼
                      ┌─────────────────────────┐
                      │    Django ORM Layer     │
                      └───────────┬─────────────┘
                                  │
                                  ▼
                      ┌─────────────────────────┐
                      │    SQLite 3 Database    │
                      │ (PostgreSQL/GIS ready)  │
                      └─────────────────────────┘
```

## 2. Core Separation of Concerns

1. **Presentation Layer**:
   - `templates/base/public_base.html`: Marketing, discovery directory, storefront profiles.
   - `templates/base/buyer_base.html`: Shopper web application, reward points status, scanner, activity history.
   - `templates/base/seller_base.html`: Merchant suite sidebar, counter QR desk, credit balance, analytics.
   - `templates/base/admin_base.html`: Operations command center, dispute tickets, audit log, merchant approvals.
2. **Service Layer**:
   - Isolates all transaction boundaries, credit calculations, point accruals, and spatial calculations from HTTP view handlers.
   - All critical mutations execute inside `@transaction.atomic`.
3. **Database Layer**:
   - Strongly typed relational schema with constraints, unique idempotency keys, and explicit foreign keys.
