# LokalLoop 🛍️🇮🇳
### Discover Local. Save More. Shop Nearby.
**Indian Local Commerce Discovery, Offers, Counter QR Redemptions, Credits & Rewards Platform**

Built with **Python + Django 5.1 + SQLite (PostgreSQL/PostGIS-ready) + Vanilla JS + HTML5/CSS3**.

---

## 🌟 1. Product Overview

LokalLoop connects Indian local brick-and-mortar merchants with nearby shoppers through:
- **Hyperlocal Discovery**: Discover verified boutiques, cafes, electronics stores, and salons in **Bengaluru, Mumbai, and Pune**.
- **In-Store Offers**: Verified discount deals valid at retail checkout counters.
- **Counter QR Desk**: High-contrast, dynamic QR codes generated for billing counters and printable standees.
- **Atomic Redemptions**: Server-side transactional redemptions with idempotency, credit deduction, and points accrual.
- **Merchant Credit Wallet**: Transparent INR credit recharge packages (`Starter ₹499`, `Growth ₹1,499`, `Business Pro ₹3,999`).
- **Buyer Loyalty Rewards**: Universal reward points earned on every store checkout.
- **3 Strictly Separated Experiences**: Public Website, Buyer Web App, Merchant Portal, and Custom Admin Console.

---

## 🏛️ 2. Architectural Pillars

```
                    PUBLIC WEBSITE (/)
                          │
             ┌────────────┼────────────┐
             ↓            ↓            ↓
           BUYER        SELLER        ADMIN
         (/buyer/)    (/seller/)  (/admin-portal/)
```

- **Strict Role Separation**: A Buyer cannot access the Seller Portal; a Seller cannot access the Admin Console. No mixed "role switcher" dashboards.
- **Service Layer Pattern**: All business calculations, geo-distance math, atomic transactions, and ledger entries live in `services/`.
- **Relational Integrity**: Complete double-entry ledger for merchant credits (`WalletTransaction`) and buyer loyalty points (`RewardTransaction`).
- **PostgreSQL / PostGIS Readiness**: Engineered cleanly without SQLite-specific logic; isolates spatial math inside `services/location_service.py`.

---

## 💻 3. Technology Stack

- **Backend**: Python 3.12+, Django 5.1
- **Database**: SQLite 3 (engineered for PostgreSQL + PostGIS)
- **Frontend**: Django Templates, HTML5, CSS3 Custom Design Tokens, Vanilla JavaScript
- **QR Engine**: Python `qrcode` library generating base64 Data URI PNGs
- **Security**: Django PBKDF2 Password Hashing, CSRF protection, Session authentication, atomic transactions

---

## 📂 4. Project Folder Structure

```
c:\WEB\
├── manage.py
├── requirements.txt
├── .env.example
├── .env
├── .gitignore
├── README.md
├── db.sqlite3
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── apps/
│   ├── accounts/         # User roles, Profile, auth views, buyer views
│   ├── shops/            # Shops, Categories, merchant views, seed command
│   ├── offers/           # Promotions, offer types, lifecycle
│   ├── redemptions/      # Dynamic QRCodes, OfferRedemption, tests
│   ├── wallets/          # Seller credits, packages, wallet ledger
│   ├── rewards/          # Buyer loyalty points, reward ledger
│   ├── notifications/    # In-app notification center
│   ├── analytics/        # Daily metrics & aggregation models
│   ├── administration/   # Admin dashboard, audit logs, dispute tickets
│   └── public/           # Landing page, public explore directory
├── services/
│   ├── __init__.py
│   ├── redemption_service.py   # Atomic QR redemption engine
│   ├── wallet_service.py       # Merchant credit mutations & checkout
│   ├── reward_service.py       # Buyer reward points mutations
│   ├── location_service.py     # Haversine distance calculations
│   └── notification_service.py # In-app notification dispatcher
├── templates/
│   ├── base/
│   │   ├── public_base.html
│   │   ├── buyer_base.html
│   │   ├── seller_base.html
│   │   └── admin_base.html
│   ├── components/
│   │   ├── shop_card.html
│   │   ├── offer_card.html
│   │   ├── stat_card.html
│   │   └── empty_state.html
│   ├── public/                 # Home, explore, shop & offer pages
│   ├── auth/                   # Buyer, seller & admin logins/registers
│   ├── buyer/                  # Buyer dashboard, scan, rewards, history
│   ├── seller/                 # Seller dashboard, shop, offers, QR, credits
│   └── admin/                  # Admin dashboard, merchants, audit, tickets
├── static/
│   ├── css/
│   │   ├── design_tokens.css   # Indian startup color palette & tokens
│   │   └── main.css            # Responsive layout & component CSS
│   └── js/
│       └── main.js             # Toast alerts, modals, clipboard, geo
└── docs/
    ├── architecture.md
    ├── database.md
    ├── authentication.md
    ├── roles-and-permissions.md
    ├── business-rules.md
    ├── redemption-flow.md
    ├── wallet-and-rewards.md
    └── future-production-plan.md
```

---

## ⚡ 5. Installation & Setup

### Step 1: Clone and Create Virtual Environment
```bash
cd c:\WEB
python -m venv venv
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy `.env.example` to `.env` (a pre-configured `.env` is already present for local development):
```bash
cp .env.example .env
```

### Step 4: Run Database Migrations
```bash
python manage.py migrate
```

### Step 5: Seed Realistic Indian Demo Data
```bash
python manage.py seed_demo
```

---

## 🔑 6. Demo Accounts & Credentials

The `seed_demo` command provisions ready-to-test Indian accounts:

| Role | Username / Email | Password | Dedicated Entry URL |
| :--- | :--- | :--- | :--- |
| **Buyer** | `buyer@example.com` | `demo123` | [http://127.0.0.1:8000/buyer/login/](http://127.0.0.1:8000/buyer/login/) |
| **Seller** | `seller@example.com` | `demo123` | [http://127.0.0.1:8000/seller/login/](http://127.0.0.1:8000/seller/login/) |
| **Admin** | `admin@example.com` | `demo123` | [http://127.0.0.1:8000/admin-login/](http://127.0.0.1:8000/admin-login/) |

- **Public Website Landing Page**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Built-in Developer Admin**: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

---

## 🚀 7. Running the Application

Start the development server:
```bash
python manage.py runserver
```
Visit `http://127.0.0.1:8000/` in your browser.

---

## 🧪 8. Automated Testing

To run the automated test suite covering the atomic redemption engine, wallet transactions, reward point ledger, idempotency protection, expired QR handling, and role authorization:

```bash
python manage.py test apps.redemptions
```

**Test Coverage Highlights**:
- `test_successful_atomic_redemption`: Validates full workflow (QR marked used, 5 merchant credits debited, 50 buyer points credited, redemption created).
- `test_idempotent_duplicate_redemption_protection`: Prevents double-rewards if the user refreshes or submits twice.
- `test_failed_redemption_expired_qr_no_mutation`: Ensures no partial state mutation on expired tokens.
- `test_failed_redemption_insufficient_credits_rolls_back`: Ensures complete rollback if merchant has insufficient promo credits.
- `test_wallet_credit_and_debit_ledger`: Verifies merchant double-entry audit trail.
- `test_rewards_service_ledger`: Verifies buyer loyalty ledger.
- `test_role_authorization_enforcement`: Tests server-side decorator redirection.

---

## 🔮 9. Production Architecture Plan

See [`docs/future-production-plan.md`](docs/future-production-plan.md) for the detailed migration roadmap towards:
- PostgreSQL 16 + PostGIS for spatial spatial indexing
- Redis + Celery for background jobs and notification queues
- Razorpay Webhooks for live Indian UPI & Card billing
- Firebase Cloud Messaging (FCM) for mobile notifications
- AWS S3 / Cloudflare R2 for asset storage
