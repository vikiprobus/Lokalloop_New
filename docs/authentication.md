# Authentication Architecture — LokalLoop

## 1. Authentication Paths

LokalLoop separates authentication into three dedicated entry points:

1. **Buyer Authentication**:
   - Registration: `/buyer/register/` (Full Name, Email, Phone, City, Password)
   - Auto-Enrollment: Automatically initializes a `BuyerProfile`, `BuyerRewardAccount`, and grants a +50 points welcome bonus.
   - Login: `/buyer/login/` -> Redirects to `/buyer/dashboard/`
   - Logout: `/buyer/logout/`
2. **Merchant / Seller Authentication**:
   - Registration: `/seller/register/` (Full Name, Business Name, Category, Locality, City, Address, Phone, Email, Password)
   - Auto-Enrollment: Automatically initializes a `SellerProfile`, `Shop` record, and `SellerWallet` with +100 free promotional credits.
   - Login: `/seller/login/` -> Redirects to `/seller/dashboard/`
   - Logout: `/seller/logout/`
3. **Platform Administrator Authentication**:
   - Controlled Access: Admin registration is strictly non-public. Admin accounts are provisioned via `seed_demo` or `createsuperuser`.
   - Login: `/admin-login/` -> Redirects to `/admin-portal/dashboard/`
   - Logout: `/admin-logout/`

## 2. Security Controls

- Passwords hashed using Django's default PBKDF2 with SHA256.
- CSRF protection enforced across all POST forms.
- Authentication sessions managed via secure HTTP cookies with 7-day expiration.
- Input sanitation and form validations applied server-side.
