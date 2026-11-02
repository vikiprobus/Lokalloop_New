# Wallets & Loyalty Rewards System — LokalLoop

## 1. Merchant Credit Wallet Ledger

The `SellerWallet` represents promotional purchasing power for merchants.

### Transaction Types:
- `PURCHASE`: Recharge of an INR bundle (e.g., Growth Pack ₹1,499 = +400 Credits).
- `BONUS`: Onboarding promotion or festival grant (e.g., +100 Credits upon shop verification).
- `DEDUCTION`: Automated debit when a customer redeems a counter promotion (-5 Credits).
- `REFUND`: Platform administrator adjustment or dispute resolution.

Every balance change records:
1. `credits` delta (+ or -)
2. `balance_after` running audit total
3. `reference_id` (e.g. `REDEMPTION_#12` or `PAY_MOCK_XYZ`)
4. Human-readable notes and timestamp.

---

## 2. Buyer Loyalty Points Ledger

The `BuyerRewardAccount` tracks shopper loyalty tokens across all network stores.

### Transaction Types:
- `WELCOME_BONUS`: +50 Points awarded automatically on account registration.
- `REDEMPTION_REWARD`: Points earned on every verified store checkout (+50 to +75 Points).
- `REDEEMED`: Points spent on partner gift cards or platform rewards.
- `ADJUSTMENT`: Operations adjustments.

### Membership Tiers:
- **Bronze Member**: < 300 lifetime points.
- **Silver Patron**: 300 to 999 lifetime points.
- **Gold Explorer**: 1,000+ lifetime points.
