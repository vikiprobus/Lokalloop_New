# Roles & Authorization Enforcement — LokalLoop

## 1. Role Taxonomy

Every user is assigned a single primary operational role in their `Profile`:
- `BUYER`: Local shoppers who discover deals, scan counter QRs, and earn loyalty points.
- `SELLER`: Local shop owners who manage physical stores, configure offers, display QR posters, and maintain promo credit balances.
- `ADMIN`: Platform operations team who monitor platform GMV, verify merchants, audit redemptions, and resolve customer support tickets.

## 2. Server-Side Authorization Decorators

Authorization is enforced at the server layer using Python function decorators:

- `@buyer_required`:
  - Verifies `request.user.is_authenticated`.
  - Asserts `request.user.profile.is_buyer == True`.
  - If a merchant or unauthenticated visitor attempts to access a buyer route, redirects to `/buyer/login/` with an alert message.
- `@seller_required`:
  - Verifies `request.user.is_authenticated`.
  - Asserts `request.user.profile.is_seller == True`.
  - If a buyer or unauthenticated visitor attempts to access `/seller/...`, redirects to `/seller/login/`.
- `@admin_required`:
  - Asserts `(profile and profile.is_admin) or request.user.is_superuser or request.user.is_staff`.
  - Restricts unauthorized visitors from accessing `/admin-portal/...`.

## 3. Explicit Prohibition of Role Switchers

LokalLoop strictly forbids role switchers (such as `[Buyer] [Seller] [Admin]` dropdowns) inside unified views. Each actor operates in their own isolated environment.
