# Core Business Rules — LokalLoop

## 1. Offer Eligibility & Validation Rules

1. **Active Window**: An offer can only be redeemed when `start_date <= now <= end_date`.
2. **Quota Enforcement**: An offer cannot be redeemed once `current_redemptions >= max_redemptions`. Once reached, its status is automatically transitioned to `EXPIRED`.
3. **Minimum Billing Threshold**: If an offer specifies `min_purchase > 0`, the buyer must enter a bill equal to or greater than the minimum bill amount.
4. **Merchant Credit Sufficiency**: A redemption is blocked if the merchant's credit wallet balance is less than `credits_per_redemption` (typically 5 credits).

## 2. Tokenomics & Loyalty Ratios

- **Merchant Debit**: 5 Promotional Credits deducted per verified store visit.
- **Buyer Accrual**: 50 Loyalty Points credited to shopper on a standard deal.
- **Credit Recharge Packages**:
  - Starter: ₹499 -> 100 Credits (~20 customer visits)
  - Growth: ₹1,499 -> 400 Credits (350 base + 50 bonus, ~80 customer visits)
  - Business Pro: ₹3,999 -> 1,200 Credits (1000 base + 200 bonus, ~240 customer visits)
- **Onboarding Grants**:
  - New Buyer: +25 Welcome Reward Points.
  - New Verified Store: +100 Promotional Credits.
