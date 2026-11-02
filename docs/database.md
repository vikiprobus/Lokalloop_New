# Database Entity Relationship & Schema — LokalLoop

## 1. Schema Entities

### User & Authentication
- `auth_user`: Built-in Django User model (username, email, password, is_staff, is_active).
- `accounts_profile`: 1-to-1 extension holding role (`BUYER`, `SELLER`, `ADMIN`), phone number, city, and avatar.
- `accounts_buyerprofile`: 1-to-1 extension holding preferred city, GPS lat/long defaults, and total visit count.
- `accounts_sellerprofile`: 1-to-1 extension holding business name, GSTIN, PAN, and verification status.

### Shops & Outlets
- `shops_shopcategory`: Hierarchical categories (e.g., Food & Cafes, Fashion, Electronics, Salons) with slugs and icons.
- `shops_shop`: Core physical outlet entity. Belongs to a Seller (FK `User`) and `ShopCategory`. Stores Indian locality, city, state, PIN, coordinates, contact info, ratings, and moderation flags. Multi-shop ready.
- `shops_shopimage`: Storefront and interior photo gallery.

### Offers & Promotions
- `offers_offer`: In-store promotional campaign.
  - Fields: `title`, `slug`, `offer_type` (`PERCENTAGE`, `FLAT`, `BOGO`, `SPECIAL`), `discount_value`, `min_purchase`, `max_discount`.
  - Tokenomics: `credits_per_redemption` (default 5), `reward_points_per_redemption` (default 50).
  - Lifecycle: `start_date`, `end_date`, `max_redemptions`, `current_redemptions`, `status` (`DRAFT`, `UPCOMING`, `ACTIVE`, `PAUSED`, `EXPIRED`).

### QR & Redemptions
- `redemptions_qrcode`: Dynamic token model linked to an Offer. Stores token string (UUID), expiration timestamp, and `is_used` boolean flag.
- `redemptions_offerredemption`: Immutable audit record of a verified checkout redemption. Stores bill amount, calculated discount, credits debited, points credited, unique `idempotency_key`, and timestamp.

### Seller Wallets
- `wallets_sellerwallet`: 1-to-1 merchant wallet holding available credits, lifetime purchased, lifetime consumed.
- `wallets_creditpackage`: Configured INR recharge bundles (`Starter ₹499`, `Growth ₹1,499`, `Business Pro ₹3,999`).
- `wallets_creditpurchase`: Ledger of completed/pending mock or Razorpay payments.
- `wallets_wallettransaction`: Double-entry audit ledger for all credit debits and credits (`PURCHASE`, `BONUS`, `DEDUCTION`, `REFUND`).

### Buyer Rewards
- `rewards_buyerrewardaccount`: 1-to-1 shopper points wallet with balance, lifetime earned, lifetime spent.
- `rewards_rewardtransaction`: Double-entry audit ledger for all points movements (`REDEMPTION_REWARD`, `WELCOME_BONUS`, `REDEEMED`).

### Operations & Governance
- `notifications_notification`: In-app user notifications.
- `administration_adminaction`: Comprehensive security audit trail for all admin actions.
- `administration_supportticket`: User and merchant dispute / support tickets.
