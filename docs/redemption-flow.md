# In-Store QR Redemption Workflow — LokalLoop

## 1. Flow Diagram

```
[ Merchant Counter ]                        [ Shopper Phone ]
        │                                           │
1. Generates Counter QR                             │
   (services.redemption_service)                    │
        │                                           │
2. Displays on Desktop / Standee                    │
        │ ─── Displays QR Image & Token ──────────> │
        │                                           │
        │                                   3. Scans QR at /buyer/scan/
        │                                      or enters token
        │                                           │
        │                                   4. Submits Bill Amount &
        │                                      Idempotency Token
        │                                           │
        │                                           ▼
        │                                   [ Django Backend Server ]
        │                                           │
        │                                   5. BEGIN ATOMIC TRANSACTION
        │                                      - Verify Buyer Auth
        │                                      - Verify QRCode exists & !used
        │                                      - Verify QR not expired
        │                                      - Verify Offer active & quota
        │                                      - Verify Bill >= Min Purchase
        │                                      - Verify Merchant Credits >= 5
        │                                      - Deduct Merchant Credits (-5)
        │                                      - Award Buyer Points (+50)
        │                                      - Mark QR used (is_used=True)
        │                                      - Increment current_redemptions
        │                                      - Create OfferRedemption record
        │                                      - Dispatch in-app notifications
        │                                      - COMMIT TRANSACTION
        │                                           │
        │ <── Receives Alert & Credit Debit ────────┤
        │                                           ▼
        │                                   6. Green Confirmation Voucher
        │ <── Presents Screen to Cashier ───────────┘
```

## 2. Idempotency Guarantees

Every redemption submission carries an `idempotency_key` unique per buyer transaction.
If network jitter causes a shopper to tap "Redeem" multiple times or refresh their browser:
- The database unique constraint on `idempotency_key` prevents double-creation.
- `services.redemption_service` intercepts the duplicate and immediately returns the existing redemption object without re-debiting merchant credits or double-awarding loyalty points.
