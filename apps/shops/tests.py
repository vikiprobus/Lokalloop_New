from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.accounts.models import Profile, Role, SellerProfile
from apps.shops.models import Shop, ShopCategory
from apps.wallets.models import SellerWallet, CreditPackage, WalletTransaction
from services.wallet_service import get_or_create_wallet, credit_wallet


class SellerCreditsOfflineWorkflowTestCase(TestCase):
    """
    Test Suite for "Contact Field Executive" Offline Credit Recharge Workflow:
    - Removal of mock sandbox checkout form
    - Modernized Rate Card header & 100% Verified Offline Billing badge
    - 'Contact Field Executive' CTA button on packages
    - Interactive Modal Dialog with WhatsApp (+91 98220 11223) & Direct Phone CTA
    - Offline Payment Guidelines (Cash, UPI/Bank Transfer, 5-min verification)
    - Excel export preservation for Wallet Audit Ledger
    - Manual admin allocation audit integrity
    """

    def setUp(self):
        self.client = Client()
        self.category = ShopCategory.objects.create(name="Apparel", slug="apparel")

        # Create Seller User
        self.seller = User.objects.create_user(
            username="merchant_offline@test.com",
            email="merchant_offline@test.com",
            password="testPassword123!",
            first_name="Vikram",
            last_name="Malhotra"
        )
        self.profile = Profile.objects.create(
            user=self.seller,
            role=Role.SELLER,
            city="Bengaluru"
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller,
            business_name="Malhotra Silk Mills",
            phone="9876543210",
            is_verified=True
        )

        # Create Shop
        self.shop = Shop.objects.create(
            seller=self.seller,
            category=self.category,
            name="Malhotra Silk Mills Store",
            slug="malhotra-silk-mills",
            address="Commercial Street",
            locality="Shivajinagar",
            city="Bengaluru",
            phone="+91 98765 43210",
            is_approved=True
        )

        # Create Wallet
        self.wallet = get_or_create_wallet(self.seller)
        self.wallet.balance_credits = 120
        self.wallet.lifetime_purchased = 300
        self.wallet.lifetime_consumed = 180
        self.wallet.loyalty_points = 25
        self.wallet.save()

        # Create Credit Packages
        self.pkg_starter = CreditPackage.objects.create(
            name="Starter Pack",
            code="STARTER_100",
            price_inr=Decimal('299.00'),
            credits=100,
            bonus_credits=0,
            description="Ideal for new neighbourhood stores testing footfall campaigns.",
            is_popular=False,
            is_active=True
        )
        self.pkg_growth = CreditPackage.objects.create(
            name="Growth Pack",
            code="GROWTH_600",
            price_inr=Decimal('999.00'),
            credits=500,
            bonus_credits=100,
            description="Our most popular plan for retail stores.",
            is_popular=True,
            is_active=True
        )

    def test_seller_credits_view_offline_workflow_rendering(self):
        """GET /seller/credits/ must render official offline top-up rate card and contact executive elements."""
        self.client.force_login(self.seller)
        res = self.client.get('/seller/credits/')
        self.assertEqual(res.status_code, 200)

        # 1. Header & Trust Badge verification
        self.assertContains(res, "Official Credit Rate Card &amp; Offline Top-Up")
        self.assertContains(res, "Our authorized field executives visit your store directly")
        self.assertContains(res, "100% Verified Offline Billing")

        # 2. Contact Field Executive CTA buttons
        self.assertContains(res, "Contact Field Executive")
        self.assertContains(res, "openFieldExecutiveModal")

        # 3. Completely removed mock payment forms
        self.assertNotContains(res, "PAY_MOCK_")
        self.assertNotContains(res, 'action="/seller/credits/" method="POST"')
        self.assertNotContains(res, "Recharge Starter Pack")
        self.assertNotContains(res, "Instant sandbox recharge")

        # 4. Interactive Field Executive Modal elements
        self.assertContains(res, 'id="fieldExecutiveModal"')
        self.assertContains(res, 'Contact Field Executive')
        self.assertContains(res, "Official Doorstep Billing &amp; Assisted Credit Top-Up")

        # 5. WhatsApp and Phone Direct Call CTAs
        self.assertContains(res, "https://wa.me/919822011223")
        self.assertContains(res, "tel:+919822011223")
        self.assertContains(res, "Call Field Support: +91 98220 11223")

        # 6. Payment Guidelines Callout
        self.assertContains(res, "Cash Collection:")
        self.assertContains(res, "Official Company UPI / Bank Transfer:")
        self.assertContains(res, "Instant Credit Delivery:")
        self.assertContains(res, "Malhotra Silk Mills Store")

    def test_seller_credits_post_redirects_safely(self):
        """POST /seller/credits/ must not execute online charge and redirect with guidance."""
        self.client.force_login(self.seller)
        res = self.client.post('/seller/credits/', {'package_code': 'GROWTH_600'}, follow=True)
        self.assertEqual(res.status_code, 200)
        
        # Balance must remain unchanged
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_credits, 120)

        # Informative notice displayed
        messages = [m.message for m in res.context['messages']]
        self.assertTrue(any("Official Offline Billing" in m for m in messages))

    def test_seller_credits_excel_export_preserved(self):
        """GET /seller/credits/?export=excel returns immutable wallet ledger spreadsheet."""
        self.client.force_login(self.seller)
        # Create a ledger transaction
        WalletTransaction.objects.create(
            wallet=self.wallet,
            transaction_type='PURCHASE',
            credits=500,
            balance_after=620,
            reference_id="OFFLINE_CASH_REC_1049",
            notes="Cash collected by Field Rep - Receipt #1049"
        )

        res = self.client.get('/seller/credits/?export=excel')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertIn('attachment; filename="my_wallet_ledger_', res['Content-Disposition'])

    def test_staff_manual_credit_allocation_audit_integrity(self):
        """Staff can allocate offline collected credits with immutable audit records."""
        initial_balance = self.wallet.balance_credits

        # Simulate staff recording offline field recharge
        purchase = credit_wallet(
            seller_user=self.seller,
            amount_credits=600,
            transaction_type='PURCHASE',
            reference_id="REC_OFFLINE_2026_9941",
            notes="Offline UPI verified by Field Executive - Receipt #9941"
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_credits, initial_balance + 600)
        self.assertEqual(self.wallet.lifetime_purchased, 300 + 600)

        # Audit ledger record verified
        tx = WalletTransaction.objects.filter(reference_id="REC_OFFLINE_2026_9941").first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.credits, 600)
        self.assertEqual(tx.balance_after, initial_balance + 600)
        self.assertIn("Field Executive", tx.notes)
