import uuid
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.accounts.models import Profile, Role, BuyerProfile, SellerProfile
from apps.shops.models import Shop, ShopCategory
from apps.offers.models import Offer, OfferType, OfferStatus
from apps.redemptions.models import QRCode, OfferRedemption
from apps.wallets.models import SellerWallet, CreditPackage, WalletTransaction
from apps.rewards.models import BuyerRewardAccount, RewardTransaction
from services.wallet_service import credit_wallet, deduct_credits, get_or_create_wallet
from services.reward_service import award_points, spend_points, get_or_create_reward_account
from services.redemption_service import process_redemption, create_or_refresh_offer_qr


class LokalLoopCoreLogicTestCase(TestCase):
    def setUp(self):
        # 1. Setup Category
        self.category = ShopCategory.objects.create(
            name="Food & Dining",
            slug="food-dining",
            icon="bi-cup-hot"
        )

        # 2. Setup Buyer
        self.buyer_user = User.objects.create_user(
            username="testbuyer@example.com",
            email="testbuyer@example.com",
            password="password123",
            first_name="Buyer"
        )
        self.buyer_profile = Profile.objects.create(
            user=self.buyer_user,
            role=Role.BUYER,
            city="Bengaluru"
        )
        self.buyer_reward_account = get_or_create_reward_account(self.buyer_user)

        # 3. Setup Seller & Shop
        self.seller_user = User.objects.create_user(
            username="testseller@example.com",
            email="testseller@example.com",
            password="password123",
            first_name="Seller"
        )
        self.seller_profile = Profile.objects.create(
            user=self.seller_user,
            role=Role.SELLER,
            city="Bengaluru"
        )
        self.seller_wallet = get_or_create_wallet(self.seller_user)
        self.seller_wallet.balance_credits = 50
        self.seller_wallet.save()

        self.shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Metro Test Cafe",
            address="100 Feet Road",
            locality="Indiranagar",
            city="Bengaluru",
            phone="+91 98000 11111"
        )

        # 4. Setup Offer
        now = timezone.now()
        self.offer = Offer.objects.create(
            shop=self.shop,
            title="Flat 20% Off",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('20.00'),
            min_purchase=Decimal('200.00'),
            credits_per_redemption=5,
            reward_points_per_redemption=50,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=10),
            max_redemptions=50,
            current_redemptions=0,
            status=OfferStatus.ACTIVE
        )

        # 5. Setup Active QR
        self.qr, self.qr_img = create_or_refresh_offer_qr(self.offer)
        from apps.administration.models import PlatformSetting
        PlatformSetting.objects.update_or_create(id=1, defaults={'geofence_enabled': False})

    def test_wallet_credit_and_debit_ledger(self):
        """Test wallet service credits, debits, and balance enforcement."""
        initial_bal = self.seller_wallet.balance_credits
        
        # Add credits
        credit_wallet(self.seller_user, 25, "PURCHASE", "REF_001", "Bought credits")
        self.seller_wallet.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, initial_bal + 25)

        # Deduct credits
        deduct_credits(self.seller_user, 10, "REDEMPTION_001", "Customer redeemed")
        self.seller_wallet.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, initial_bal + 15)

        # Deduct more than available should raise ValidationError
        with self.assertRaises(ValidationError):
            deduct_credits(self.seller_user, 9999, "OVERDRAW", "Overdraft attempt")

    def test_rewards_service_ledger(self):
        """Test loyalty reward points awards and deductions."""
        initial_points = self.buyer_reward_account.current_points

        award_points(self.buyer_user, 100, "BONUS", notes="Bonus earned")
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.buyer_reward_account.current_points, initial_points + 100)

        spend_points(self.buyer_user, 50, notes="Voucher spent")
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.buyer_reward_account.current_points, initial_points + 50)

        # Spending more points than available raises ValidationError
        with self.assertRaises(ValidationError):
            spend_points(self.buyer_user, 99999, notes="Illegal spend")

    def test_successful_atomic_redemption(self):
        """Test full successful redemption: QR marked used, seller credits debited, buyer points awarded."""
        seller_initial_credits = self.seller_wallet.balance_credits
        buyer_initial_points = self.buyer_reward_account.current_points

        success, msg, redemption = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('500.00'),
            idempotency_key="unique_test_key_1"
        )

        self.assertTrue(success)
        self.assertIsNotNone(redemption)
        self.assertEqual(redemption.discount_given, Decimal('100.00'))  # 20% of 500 = 100

        # Verify standee QR code remains active for subsequent customer redemptions until max quota is reached
        self.qr.refresh_from_db()
        self.assertFalse(self.qr.is_used)

        # Verify seller wallet debited exactly 5 credits
        self.seller_wallet.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, seller_initial_credits - 5)

        # Verify buyer reward account credited exactly 2 points (1% of 200 min_purchase)
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.buyer_reward_account.current_points, buyer_initial_points + 2)
        self.assertEqual(redemption.reward_points_earned, 2)

        # Verify offer counter incremented
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.current_redemptions, 1)

    def test_idempotent_duplicate_redemption_protection(self):
        """Submitting twice with same idempotency key must not double-credit points or double-debit credits."""
        success1, msg1, redemp1 = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('500.00'),
            idempotency_key="idempotent_test_key"
        )
        self.assertTrue(success1)

        seller_bal = self.seller_wallet.refresh_from_db() or self.seller_wallet.balance_credits
        buyer_pts = self.buyer_reward_account.refresh_from_db() or self.buyer_reward_account.current_points

        # Second attempt with same key
        success2, msg2, redemp2 = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('500.00'),
            idempotency_key="idempotent_test_key"
        )
        self.assertTrue(success2)
        self.assertEqual(redemp1.id, redemp2.id)

        # Balances must NOT have mutated again
        self.seller_wallet.refresh_from_db()
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, seller_bal)
        self.assertEqual(self.buyer_reward_account.current_points, buyer_pts)

    def test_failed_redemption_expired_qr_no_mutation(self):
        """Expired QR fails cleanly and prevents wallet/reward mutation."""
        now = timezone.now()
        expired_qr = QRCode.objects.create(
            offer=self.offer,
            code=str(uuid.uuid4()),
            expires_at=now - timedelta(hours=2),
            is_used=False
        )

        seller_bal_before = self.seller_wallet.balance_credits
        buyer_pts_before = self.buyer_reward_account.current_points

        success, msg, redemp = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=expired_qr.code,
            bill_amount=Decimal('500.00')
        )

        self.assertFalse(success)
        self.assertIn("expired", msg.lower())
        self.assertIsNone(redemp)

        # Ensure NO wallet or point mutations occurred
        self.seller_wallet.refresh_from_db()
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, seller_bal_before)
        self.assertEqual(self.buyer_reward_account.current_points, buyer_pts_before)

    def test_failed_redemption_insufficient_credits_rolls_back(self):
        """When seller has insufficient credits, redemption fails and does not award points to buyer."""
        # Set seller balance to 2 credits (need 5)
        self.seller_wallet.balance_credits = 2
        self.seller_wallet.save()

        buyer_pts_before = self.buyer_reward_account.current_points

        success, msg, redemp = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('400.00')
        )

        self.assertFalse(success)
        self.assertIn("credits", msg.lower())
        self.assertIsNone(redemp)

        # Buyer points and QR code status must remain untouched
        self.buyer_reward_account.refresh_from_db()
        self.qr.refresh_from_db()
        self.assertEqual(self.buyer_reward_account.current_points, buyer_pts_before)
        self.assertFalse(self.qr.is_used)

    def test_role_authorization_enforcement(self):
        """Buyers are blocked from seller portal, sellers blocked from admin portal."""
        client = Client()

        # 1. Unauthenticated trying seller portal -> redirected to /seller/login/
        response = client.get('/seller/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/seller/login/', response.url)

        # 2. Buyer logged in trying seller portal -> blocked / redirected
        client.force_login(self.buyer_user)
        response = client.get('/seller/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, '/seller/dashboard/')

        # 3. Seller logged in trying admin portal -> blocked with 403 Forbidden
        client.force_login(self.seller_user)
        response = client.get('/admin-portal/dashboard/')
        self.assertEqual(response.status_code, 403)


class ViewRenderingIntegrationTestCase(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command('seed_demo')
        self.client = Client()

    def test_public_views_render_200(self):
        """Verify public landing, explore, and seller pitch render cleanly."""
        for path in ['/', '/explore/', '/for-sellers/', '/buyer/login/', '/seller/login/', '/admin-login/']:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Path {path} returned {resp.status_code}")

    def test_buyer_views_render_200(self):
        """Verify authenticated buyer portal views render cleanly."""
        buyer = User.objects.get(username='buyer@example.com')
        self.client.force_login(buyer)
        for path in ['/buyer/dashboard/', '/buyer/explore/', '/buyer/scan/', '/buyer/rewards/', '/buyer/history/', '/buyer/notifications/']:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Path {path} returned {resp.status_code}")

    def test_seller_views_render_200(self):
        """Verify authenticated seller portal views render cleanly."""
        seller = User.objects.get(username='seller@example.com')
        self.client.force_login(seller)
        for path in ['/seller/dashboard/', '/seller/shop/', '/seller/offers/', '/seller/offers/create/', '/seller/qr/', '/seller/credits/', '/seller/redemptions/', '/seller/analytics/', '/seller/notifications/']:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Path {path} returned {resp.status_code}")

    def test_admin_portal_views_render_200(self):
        """Verify authenticated admin console views render cleanly."""
        admin_user = User.objects.get(username='admin@example.com')
        self.client.force_login(admin_user)
        for path in ['/admin-portal/dashboard/', '/admin-portal/buyers/', '/admin-portal/sellers/', '/admin-portal/shops/', '/admin-portal/offers/', '/admin-portal/redemptions/', '/admin-portal/credits/', '/admin-portal/audit/', '/admin-portal/tickets/']:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Path {path} returned {resp.status_code}")


class AntiAbuseRedemptionLimitsTestCase(TestCase):
    """
    Anti-Abuse Verification:
    - Strictly 1 offer redemption per seller per buyer per day.
    - Maximum 3 offer redemptions per buyer per day.
    - Daily 25-point cap.
    - Terms disclaimer modal in templates.
    """

    def setUp(self):
        self.client = Client()
        self.category = ShopCategory.objects.create(name="Retail", slug="retail")

        # Buyer
        self.buyer = User.objects.create_user(
            username="buyer_antiabuse@test.com",
            email="buyer_antiabuse@test.com",
            password="testPassword123!"
        )
        Profile.objects.create(user=self.buyer, role=Role.BUYER)
        get_or_create_reward_account(self.buyer)
        from apps.administration.models import PlatformSetting
        PlatformSetting.objects.update_or_create(id=1, defaults={'geofence_enabled': False})

        # Create 4 distinct sellers and shops
        self.sellers = []
        self.shops = []
        self.offers = []
        self.qrs = []

        now = timezone.now()
        for i in range(1, 5):
            seller = User.objects.create_user(
                username=f"seller_{i}@test.com",
                email=f"seller_{i}@test.com",
                password="testPassword123!"
            )
            Profile.objects.create(user=seller, role=Role.SELLER)
            wallet = get_or_create_wallet(seller)
            wallet.balance_credits = 100
            wallet.save()

            shop = Shop.objects.create(
                seller=seller,
                category=self.category,
                name=f"Merchant Shop {i}",
                city="Pune",
                slug=f"merchant-shop-{i}"
            )
            offer = Offer.objects.create(
                shop=shop,
                title=f"Deal at Shop {i}",
                offer_type=OfferType.PERCENTAGE,
                discount_value=Decimal('10.00'),
                min_purchase=Decimal('500.00'),  # 1% = 5 points
                credits_per_redemption=5,
                start_date=now - timedelta(days=1),
                end_date=now + timedelta(days=5),
                status=OfferStatus.ACTIVE
            )
            qr, _ = create_or_refresh_offer_qr(offer)

            self.sellers.append(seller)
            self.shops.append(shop)
            self.offers.append(offer)
            self.qrs.append(qr)

    def test_second_redemption_at_same_seller_on_same_day_rejected(self):
        """
        Strictly 1 offer redemption per seller per buyer per day.
        Second redemption at the same seller must be rejected.
        """
        # 1st redemption at Shop 0 (Seller 0)
        success1, msg1, redemp1 = process_redemption(
            buyer_user=self.buyer,
            qr_code_str=self.qrs[0].code,
            bill_amount=Decimal('500.00')
        )
        self.assertTrue(success1)
        self.assertIsNotNone(redemp1)

        # Create fresh QR for another offer at the same shop/seller
        now = timezone.now()
        offer2_same_seller = Offer.objects.create(
            shop=self.shops[0],
            title="Second Deal Same Shop",
            offer_type=OfferType.FLAT,
            discount_value=Decimal('50.00'),
            min_purchase=Decimal('500.00'),
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        qr2, _ = create_or_refresh_offer_qr(offer2_same_seller)

        # 2nd redemption at same seller on the same day -> must fail
        success2, msg2, redemp2 = process_redemption(
            buyer_user=self.buyer,
            qr_code_str=qr2.code,
            bill_amount=Decimal('500.00')
        )
        self.assertFalse(success2)
        self.assertIsNone(redemp2)
        self.assertIn("seller", msg2.lower())

    def test_fourth_redemption_in_single_day_rejected(self):
        """
        Maximum 3 offer redemptions per buyer per calendar day.
        4th redemption in a single day must be rejected even at a distinct seller.
        """
        # Redemption 1 at Shop 1
        s1, m1, r1 = process_redemption(self.buyer, self.qrs[0].code, Decimal('500.00'))
        self.assertTrue(s1)

        # Redemption 2 at Shop 2
        s2, m2, r2 = process_redemption(self.buyer, self.qrs[1].code, Decimal('500.00'))
        self.assertTrue(s2)

        # Redemption 3 at Shop 3
        s3, m3, r3 = process_redemption(self.buyer, self.qrs[2].code, Decimal('500.00'))
        self.assertTrue(s3)

        # Redemption 4 at Shop 4 -> must be rejected
        s4, m4, r4 = process_redemption(self.buyer, self.qrs[3].code, Decimal('500.00'))
        self.assertFalse(s4)
        self.assertIsNone(r4)
        self.assertIn("maximum 3", m4.lower())

    def test_daily_25_point_cap_enforced(self):
        """
        Maximum 25 reward points per buyer per calendar day.
        When daily points reach 25, subsequent redemptions are rejected under the daily cap.
        """
        now = timezone.now()
        # Set offer at Shop 0 with min_purchase=3000 -> 1% is 30, capped at 25 points
        self.offers[0].min_purchase = Decimal('3000.00')
        self.offers[0].save()

        # 1st redemption awards 25 points (hitting daily cap)
        s1, m1, r1 = process_redemption(self.buyer, self.qrs[0].code, Decimal('3000.00'))
        self.assertTrue(s1)
        self.assertEqual(r1.reward_points_earned, 25)

        # 2nd redemption at distinct Shop 1 on same day -> rejected because daily points cap (25) is reached
        s2, m2, r2 = process_redemption(self.buyer, self.qrs[1].code, Decimal('500.00'))
        self.assertFalse(s2)
        self.assertIsNone(r2)
        self.assertIn("25", m2)

    def test_points_partial_cap_adjustment(self):
        """
        When a buyer has earned 20 points and redeems an offer with 10 points base,
        they receive 5 points to exactly reach the 25 daily cap.
        """
        now = timezone.now()
        # Shop 0 offer min_purchase=2000 -> 20 points
        self.offers[0].min_purchase = Decimal('2000.00')
        self.offers[0].save()

        # Shop 1 offer min_purchase=1000 -> 10 points
        self.offers[1].min_purchase = Decimal('1000.00')
        self.offers[1].save()

        # 1st redemption earns 20 points
        s1, m1, r1 = process_redemption(self.buyer, self.qrs[0].code, Decimal('2000.00'))
        self.assertTrue(s1)
        self.assertEqual(r1.reward_points_earned, 20)

        # 2nd redemption earns remaining 5 points (capped at 25 total)
        s2, m2, r2 = process_redemption(self.buyer, self.qrs[1].code, Decimal('1000.00'))
        self.assertTrue(s2)
        self.assertEqual(r2.reward_points_earned, 5)

    def test_mandatory_terms_disclaimer_modal_rendered_in_templates(self):
        """
        Verify the mandatory English disclaimer modal is rendered in store deals and offer templates.
        """
        self.client.force_login(self.buyer)

        # 1. Store Deals URL
        res_deals = self.client.get(f"/store/{self.shops[0].slug}/deals/")
        self.assertEqual(res_deals.status_code, 200)
        self.assertContains(res_deals, "Offer Terms &amp; Conditions")
        self.assertContains(res_deals, "termsAgreementCheckbox")

        # 2. Offer Detail URL
        res_offer = self.client.get(f"/offers/{self.offers[0].slug}/")
        self.assertEqual(res_offer.status_code, 200)
        self.assertContains(res_offer, "Offer Terms &amp; Conditions")
        self.assertContains(res_offer, "termsAgreementCheckbox")

        # 3. Buyer Scan / QR Scanner URL
        res_scan = self.client.get("/buyer/scan/")
        self.assertEqual(res_scan.status_code, 200)
        self.assertContains(res_scan, "Offer Terms &amp; Conditions")
        self.assertContains(res_scan, "termsAgreementCheckbox")


class MasterCounterWorkflowTestCase(TestCase):
    """
    Test Counter Store QR Code & Live Seller "Accept Request" Workflow:
    - Master Counter QR route /shop/<slug>/offers/
    - Buyer initiates redemption -> PENDING_APPROVAL with 4-digit OTP
    - Seller approves -> 1 credit deducted, 1 loyalty point credited, points awarded, status SUCCESS
    - Seller rejects -> 0 credits deducted, 0 points awarded, status CANCELLED
    - Real-time seller polling API & status polling API
    """

    def setUp(self):
        self.client = Client()
        self.category = ShopCategory.objects.create(name="Fashion", slug="fashion")

        # Buyer
        self.buyer = User.objects.create_user(
            username="buyer_counter@test.com",
            email="buyer_counter@test.com",
            password="testPassword123!",
            first_name="Aarav"
        )
        Profile.objects.create(user=self.buyer, role=Role.BUYER)
        BuyerProfile.objects.create(user=self.buyer, nickname="SparkleGennie", public_unique_id="LL-883-772")
        self.buyer_reward_account = get_or_create_reward_account(self.buyer)

        # Seller 1
        self.seller = User.objects.create_user(
            username="seller_counter@test.com",
            email="seller_counter@test.com",
            password="testPassword123!",
            first_name="ShopOwner"
        )
        Profile.objects.create(user=self.seller, role=Role.SELLER)
        self.seller_wallet = get_or_create_wallet(self.seller)
        self.seller_wallet.balance_credits = 50
        self.seller_wallet.loyalty_points = 5
        self.seller_wallet.save()

        # Shop
        self.shop = Shop.objects.create(
            seller=self.seller,
            category=self.category,
            name="Counter Boutique",
            slug="counter-boutique",
            city="Pune"
        )

        # Active Offer (min_purchase = 500 -> 1% = 5 points)
        now = timezone.now()
        self.offer = Offer.objects.create(
            shop=self.shop,
            title="Weekend 15% Off",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('15.00'),
            min_purchase=Decimal('500.00'),
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        # Other Seller (for permission checks)
        self.other_seller = User.objects.create_user(
            username="other_seller@test.com",
            email="other_seller@test.com",
            password="testPassword123!"
        )
        Profile.objects.create(user=self.other_seller, role=Role.SELLER)
        get_or_create_wallet(self.other_seller)

    def test_master_counter_qr_route_opens_store_offers(self):
        """GET /shop/<slug>/offers/ renders active deals and counter QR landing."""
        resp = self.client.get(f"/shop/{self.shop.slug}/offers/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Counter Boutique")
        self.assertContains(resp, "Weekend 15% Off")
        self.assertContains(resp, "Offer Terms &amp; Conditions")

    def test_initiate_redemption_creates_pending_with_4_digit_otp(self):
        """Buyer initiating redemption creates PENDING_APPROVAL record with 4-digit token without deducting credits."""
        from services.redemption_service import initiate_redemption
        from apps.redemptions.models import RedemptionStatus

        initial_credits = self.seller_wallet.balance_credits
        initial_points = self.buyer_reward_account.current_points

        success, msg, redemption = initiate_redemption(
            buyer_user=self.buyer,
            offer=self.offer,
            bill_amount=Decimal('600.00')
        )

        self.assertTrue(success)
        self.assertIsNotNone(redemption)
        self.assertEqual(redemption.status, RedemptionStatus.PENDING_APPROVAL)
        self.assertEqual(len(redemption.verification_otp), 4)
        self.assertTrue(redemption.verification_otp.isdigit())

        # Credits and Points must NOT be deducted/awarded yet
        self.seller_wallet.refresh_from_db()
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, initial_credits)
        self.assertEqual(self.buyer_reward_account.current_points, initial_points)
        self.assertEqual(self.offer.current_redemptions, 0)

    def test_seller_approve_redemption_workflow(self):
        """Seller approving redemption deducts 1 credit, adds 1 merchant loyalty point, awards points to buyer."""
        from services.redemption_service import initiate_redemption, approve_redemption
        from apps.redemptions.models import RedemptionStatus

        initial_credits = self.seller_wallet.balance_credits
        initial_loyalty = self.seller_wallet.loyalty_points
        initial_buyer_points = self.buyer_reward_account.current_points

        # Initiate
        success, msg, redemption = initiate_redemption(
            buyer_user=self.buyer,
            offer=self.offer,
            bill_amount=Decimal('500.00')
        )
        self.assertTrue(success)

        # Seller approves
        app_success, app_msg, approved_redemp = approve_redemption(redemption.id, self.seller)
        self.assertTrue(app_success)
        self.assertEqual(approved_redemp.status, RedemptionStatus.SUCCESS)

        # Verify wallet changes: strictly 1 credit deducted, strictly 1 loyalty point added
        self.seller_wallet.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, initial_credits - self.offer.credits_per_redemption)
        self.assertEqual(self.seller_wallet.loyalty_points, initial_loyalty + 1)

        # Verify buyer points: 1% of 500 = 5 points
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.buyer_reward_account.current_points, initial_buyer_points + 5)
        self.assertEqual(approved_redemp.reward_points_earned, 5)

        # Verify offer counter
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.current_redemptions, 1)

    def test_seller_reject_redemption_workflow(self):
        """Seller rejecting redemption sets status CANCELLED with 0 credit deduction and 0 points awarded."""
        from services.redemption_service import initiate_redemption, reject_redemption
        from apps.redemptions.models import RedemptionStatus

        initial_credits = self.seller_wallet.balance_credits
        initial_loyalty = self.seller_wallet.loyalty_points
        initial_buyer_points = self.buyer_reward_account.current_points

        # Initiate
        success, msg, redemption = initiate_redemption(
            buyer_user=self.buyer,
            offer=self.offer,
            bill_amount=Decimal('500.00')
        )
        self.assertTrue(success)

        # Seller rejects
        rej_success, rej_msg, rej_redemp = reject_redemption(redemption.id, self.seller, reason="Bill did not meet criteria")
        self.assertTrue(rej_success)
        self.assertEqual(rej_redemp.status, RedemptionStatus.CANCELLED)

        # Balances remain untouched
        self.seller_wallet.refresh_from_db()
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, initial_credits)
        self.assertEqual(self.seller_wallet.loyalty_points, initial_loyalty)
        self.assertEqual(self.buyer_reward_account.current_points, initial_buyer_points)

    def test_counter_api_endpoints(self):
        """Test polling, approve, and status endpoints via HTTP client."""
        from services.redemption_service import initiate_redemption

        # 1. Buyer initiates
        success, msg, redemption = initiate_redemption(
            buyer_user=self.buyer,
            offer=self.offer,
            bill_amount=Decimal('500.00')
        )
        self.assertTrue(success)

        # 2. Check live status API for buyer screen (authenticated as buyer)
        self.client.force_login(self.buyer)
        res_status = self.client.get(f"/api/redemptions/{redemption.id}/status/")
        self.assertEqual(res_status.status_code, 200)
        data = res_status.json()
        self.assertEqual(data['status'], 'PENDING_APPROVAL')
        self.assertFalse(data['is_approved'])
        self.assertEqual(data['verification_otp'], redemption.verification_otp)

        # 3. Seller pending redemptions polling API
        self.client.force_login(self.seller)
        res_pending = self.client.get('/api/seller/pending-redemptions/')
        self.assertEqual(res_pending.status_code, 200)
        pending_data = res_pending.json()
        self.assertEqual(pending_data['count'], 1)
        self.assertEqual(pending_data['requests'][0]['id'], redemption.id)
        self.assertEqual(pending_data['requests'][0]['customer_name'], 'SparkleGennie')
        self.assertEqual(pending_data['requests'][0]['verification_otp'], redemption.verification_otp)

        # 4. Another seller cannot approve this redemption
        self.client.force_login(self.other_seller)
        res_bad_approve = self.client.post(f"/api/seller/redemptions/{redemption.id}/approve/")
        self.assertEqual(res_bad_approve.status_code, 400)

        # 5. Correct seller approves
        self.client.force_login(self.seller)
        res_approve = self.client.post(f"/api/seller/redemptions/{redemption.id}/approve/")
        self.assertEqual(res_approve.status_code, 200)
        self.assertTrue(res_approve.json()['success'])

        # 6. Check buyer status API again -> now approved
        res_status2 = self.client.get(f"/api/redemptions/{redemption.id}/status/")
        self.assertEqual(res_status2.status_code, 200)
        self.assertTrue(res_status2.json()['is_approved'])
        self.assertEqual(res_status2.json()['status'], 'SUCCESS')





import uuid
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone

from apps.accounts.models import Profile, Role, BuyerProfile
from apps.shops.models import Shop, ShopCategory
from apps.offers.models import Offer, OfferType, OfferStatus
from apps.redemptions.models import QRCode, OfferRedemption, RedemptionStatus
from apps.wallets.models import SellerWallet
from apps.rewards.models import BuyerRewardAccount
from services.wallet_service import get_or_create_wallet
from services.reward_service import get_or_create_reward_account
from services.redemption_service import (
    create_or_refresh_offer_qr,
    initiate_redemption,
    approve_redemption,
    reject_redemption,
    process_redemption
)
from services.background_tasks import expire_stale_qrcodes_job


class QRExpirationLifecycleTestCase(TestCase):
    """
    Exhaustive Test Suite for QR Redemption Expiration:
    1. QRCode.is_expired verification (expires_at and offer.end_date bounds)
    2. OfferRedemption.is_expired verification (15-min OTP window)
    3. create_or_refresh_offer_qr caps expiry at offer.end_date
    4. approve_redemption blocks and auto-cancels expired OTP requests
    5. approve_redemption blocks and auto-cancels when offer has ended
    6. initiate_redemption auto-cancels stale pending redemption and generates fresh OTP
    7. seller_pending_redemptions_api auto-cancels and excludes expired requests
    8. redemption_status_api auto-cancels expired requests and returns is_expired: True
    9. qr_token_landing_view rejects expired/used QR codes with user-friendly warning
    10. expire_stale_qrcodes_job background cron deactivates expired QRs and pending OTPs
    """

    def setUp(self):
        self.client = Client()
        self.category = ShopCategory.objects.create(name="Electronics", slug="electronics")

        # Buyer
        self.buyer = User.objects.create_user(
            username="buyer_qr_expiry@test.com",
            email="buyer_qr_expiry@test.com",
            password="testPassword123!",
            first_name="Rohan"
        )
        Profile.objects.create(user=self.buyer, role=Role.BUYER)
        BuyerProfile.objects.create(user=self.buyer, nickname="RohanG", public_unique_id="LL-EXP-001")
        self.buyer_reward_account = get_or_create_reward_account(self.buyer)

        # Seller
        self.seller = User.objects.create_user(
            username="seller_qr_expiry@test.com",
            email="seller_qr_expiry@test.com",
            password="testPassword123!",
            first_name="StoreBoss"
        )
        Profile.objects.create(user=self.seller, role=Role.SELLER)
        self.seller_wallet = get_or_create_wallet(self.seller)
        self.seller_wallet.balance_credits = 40
        self.seller_wallet.loyalty_points = 2
        self.seller_wallet.save()

        # Shop
        self.shop = Shop.objects.create(
            seller=self.seller,
            category=self.category,
            name="Apex Gadgets",
            slug="apex-gadgets",
            city="Bengaluru",
            is_approved=True
        )

        now = timezone.now()
        self.offer = Offer.objects.create(
            shop=self.shop,
            title="Flat ₹200 Off On Audio",
            offer_type=OfferType.FLAT,
            discount_value=Decimal('200.00'),
            min_purchase=Decimal('1000.00'),
            start_date=now - timedelta(days=2),
            end_date=now + timedelta(days=3),
            status=OfferStatus.ACTIVE
        )

    def test_qr_code_is_expired_property_and_campaign_capping(self):
        """QRCode.is_expired must trigger on expires_at and offer.end_date, and create_or_refresh caps expiry."""
        now = timezone.now()
        
        # 1. Standard QR with future expiry is NOT expired
        qr_active = QRCode.objects.create(
            offer=self.offer,
            code="test_code_active_1",
            expires_at=now + timedelta(hours=2)
        )
        self.assertFalse(qr_active.is_expired)

        # 2. QR with past expires_at IS expired
        qr_past = QRCode.objects.create(
            offer=self.offer,
            code="test_code_past_1",
            expires_at=now - timedelta(minutes=5)
        )
        self.assertTrue(qr_past.is_expired)

        # 3. QR with future expires_at but offer.end_date in the past IS expired
        offer_ended = Offer.objects.create(
            shop=self.shop,
            title="Past Offer",
            offer_type=OfferType.FLAT,
            discount_value=Decimal('50.00'),
            min_purchase=Decimal('500.00'),
            start_date=now - timedelta(days=5),
            end_date=now - timedelta(days=1),
            status=OfferStatus.ACTIVE
        )
        qr_offer_ended = QRCode.objects.create(
            offer=offer_ended,
            code="test_code_offer_ended_1",
            expires_at=now + timedelta(hours=5)
        )
        self.assertTrue(qr_offer_ended.is_expired)

        # 4. create_or_refresh_offer_qr caps expires_at at offer.end_date
        short_offer = Offer.objects.create(
            shop=self.shop,
            title="Flash Sale Ends in 2 Hours",
            offer_type=OfferType.FLAT,
            discount_value=Decimal('50.00'),
            min_purchase=Decimal('500.00'),
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=2),
            status=OfferStatus.ACTIVE
        )
        # Requesting 24-hour QR (1440 mins)
        new_qr, _ = create_or_refresh_offer_qr(short_offer, expiry_minutes=1440)
        self.assertLessEqual(new_qr.expires_at, short_offer.end_date)

    def test_offer_redemption_is_expired_property(self):
        """OfferRedemption.is_expired evaluates True only for pending approval past otp_expires_at."""
        now = timezone.now()
        # Pending and unexpired
        redemp_pending = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="1234",
            otp_expires_at=now + timedelta(minutes=10),
            idempotency_key="redemp_unexp_1"
        )
        self.assertFalse(redemp_pending.is_expired)

        # Pending and past otp_expires_at
        redemp_expired = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="5678",
            otp_expires_at=now - timedelta(seconds=1),
            idempotency_key="redemp_exp_1"
        )
        self.assertTrue(redemp_expired.is_expired)

        # Already successful redemption with past otp_expires_at is NOT considered expired
        redemp_success = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.SUCCESS,
            verification_otp="9999",
            otp_expires_at=now - timedelta(minutes=20),
            idempotency_key="redemp_succ_1"
        )
        self.assertFalse(redemp_success.is_expired)

    def test_approve_redemption_fails_and_cancels_when_expired(self):
        """Merchant approval of an expired pending redemption fails cleanly, cancels record, and protects credits."""
        now = timezone.now()
        initial_seller_credits = self.seller_wallet.balance_credits
        initial_buyer_points = self.buyer_reward_account.current_points

        # Pending redemption whose OTP expired 2 minutes ago
        stale_redemption = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="4321",
            otp_expires_at=now - timedelta(minutes=2),
            idempotency_key="stale_redemp_test"
        )

        success, msg, redemp = approve_redemption(stale_redemption.id, self.seller)

        self.assertFalse(success)
        self.assertIn("expired", msg.lower())
        self.assertEqual(redemp.status, RedemptionStatus.CANCELLED)

        # Confirm database state
        stale_redemption.refresh_from_db()
        self.assertEqual(stale_redemption.status, RedemptionStatus.CANCELLED)

        # Credits and Points must NOT have changed
        self.seller_wallet.refresh_from_db()
        self.buyer_reward_account.refresh_from_db()
        self.assertEqual(self.seller_wallet.balance_credits, initial_seller_credits)
        self.assertEqual(self.buyer_reward_account.current_points, initial_buyer_points)

    def test_approve_redemption_fails_when_offer_has_ended(self):
        """Merchant approval fails and cancels if campaign ended prior to cashier action."""
        now = timezone.now()
        stale_offer = Offer.objects.create(
            shop=self.shop,
            title="Expired Flash Sale",
            offer_type=OfferType.FLAT,
            discount_value=Decimal('100.00'),
            min_purchase=Decimal('500.00'),
            start_date=now - timedelta(days=3),
            end_date=now - timedelta(minutes=1),
            status=OfferStatus.ACTIVE
        )
        redemp = OfferRedemption.objects.create(
            offer=stale_offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="8888",
            otp_expires_at=now + timedelta(minutes=5),
            idempotency_key="stale_offer_redemp"
        )

        success, msg, updated_redemp = approve_redemption(redemp.id, self.seller)
        self.assertFalse(success)
        self.assertIn("no longer active or valid", msg)
        self.assertEqual(updated_redemp.status, RedemptionStatus.CANCELLED)

    def test_initiate_redemption_auto_cancels_stale_and_issues_new_token(self):
        """When a buyer previously had a pending redemption that expired, initiate_redemption cancels it and creates a fresh active token."""
        now = timezone.now()
        # Stale redemption from 20 minutes ago
        old_redemption = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="1111",
            otp_expires_at=now - timedelta(minutes=5),
            idempotency_key="old_stale_token_test"
        )

        # Buyer initiates again
        success, msg, new_redemption = initiate_redemption(
            buyer_user=self.buyer,
            offer=self.offer,
            bill_amount=Decimal('1000.00')
        )

        self.assertTrue(success)
        self.assertNotEqual(new_redemption.id, old_redemption.id)
        self.assertEqual(new_redemption.status, RedemptionStatus.PENDING_APPROVAL)
        self.assertGreater(new_redemption.otp_expires_at, now)

        # Old redemption is marked CANCELLED
        old_redemption.refresh_from_db()
        self.assertEqual(old_redemption.status, RedemptionStatus.CANCELLED)

    def test_seller_pending_redemptions_api_excludes_and_auto_cancels_expired(self):
        """Seller pending redemptions API excludes expired requests and auto-cancels them."""
        now = timezone.now()
        # 1 Active request
        active_redemp = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="2222",
            otp_expires_at=now + timedelta(minutes=10),
            idempotency_key="active_redemp_api"
        )
        # 1 Expired request
        expired_redemp = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="3333",
            otp_expires_at=now - timedelta(minutes=1),
            idempotency_key="expired_redemp_api"
        )

        self.client.force_login(self.seller)
        res = self.client.get('/api/seller/pending-redemptions/')
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data['count'], 1)
        self.assertEqual(data['requests'][0]['id'], active_redemp.id)
        self.assertGreater(data['requests'][0]['remaining_seconds'], 0)

        # Expired request was automatically marked CANCELLED in DB
        expired_redemp.refresh_from_db()
        self.assertEqual(expired_redemp.status, RedemptionStatus.CANCELLED)

    def test_redemption_status_api_auto_cancels_and_flags_expired(self):
        """Buyer status polling API detects expiration, transitions record to CANCELLED, and returns is_expired: True."""
        now = timezone.now()
        expired_redemp = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="7777",
            otp_expires_at=now - timedelta(minutes=2),
            idempotency_key="poll_expired_test"
        )

        self.client.force_login(self.buyer)
        res = self.client.get(f"/api/redemptions/{expired_redemp.id}/status/")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data['is_expired'])
        self.assertTrue(data['is_cancelled'])
        self.assertFalse(data['is_approved'])
        self.assertIn("expired", data['message'].lower())

        expired_redemp.refresh_from_db()
        self.assertEqual(expired_redemp.status, RedemptionStatus.CANCELLED)

    def test_qr_token_landing_view_expired_qr_redirects_with_warning(self):
        """Navigating to /qr/<token>/ for an expired QR redirects to store page with informative flash warning."""
        now = timezone.now()
        expired_qr = QRCode.objects.create(
            offer=self.offer,
            code="test_token_expired_landing",
            expires_at=now - timedelta(hours=1),
            is_used=False
        )

        res = self.client.get(f"/qr/{expired_qr.code}/", follow=True)
        self.assertEqual(res.status_code, 200)
        # Should have redirected to /store/<shop_slug>/qr/
        self.assertIn(f"/store/{self.shop.slug}/qr/", res.redirect_chain[0][0])
        # Informative warning message
        messages_text = [m.message for m in res.context['messages']]
        self.assertTrue(any("expired" in m.lower() for m in messages_text))

    def test_expire_stale_qrcodes_job_cleans_qrs_and_pending_redemptions(self):
        """Cron background task expire_stale_qrcodes_job cancels expired QRs and pending redemptions."""
        now = timezone.now()
        # Stale QR
        stale_qr = QRCode.objects.create(
            offer=self.offer,
            code="cron_stale_qr_test",
            expires_at=now - timedelta(hours=1),
            is_used=False
        )
        # Stale Redemption
        stale_redemp = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="9999",
            otp_expires_at=now - timedelta(minutes=10),
            idempotency_key="cron_stale_redemp_test"
        )

        result = expire_stale_qrcodes_job()
        self.assertEqual(result['status'], 'success')
        self.assertGreaterEqual(result['expired_count'], 1)
        self.assertGreaterEqual(result['expired_redemptions_count'], 1)

        stale_qr.refresh_from_db()
        stale_redemp.refresh_from_db()
        self.assertTrue(stale_qr.is_used)
        self.assertEqual(stale_redemp.status, RedemptionStatus.CANCELLED)

    def test_redemption_status_api_idor_security_authorization(self):
        """IDOR & Authorization Guardrail: Only participant buyer, seller, staff, or active session can view status."""
        now = timezone.now()
        redemp = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="5555",
            otp_expires_at=now + timedelta(minutes=10),
            idempotency_key="idor_test_redemp_key"
        )

        other_buyer = User.objects.create_user(
            username="other_intruder@test.com",
            email="other_intruder@test.com",
            password="testPassword123!"
        )

        # 1. Anonymous user without session -> 403 Forbidden
        self.client.logout()
        res_anon = self.client.get(f"/api/redemptions/{redemp.id}/status/")
        self.assertEqual(res_anon.status_code, 403)
        self.assertIn("error", res_anon.json())

        # 2. Anonymous user with matching active session token -> 200 OK
        session = self.client.session
        session['active_redemption_id'] = redemp.id
        session.save()
        res_session = self.client.get(f"/api/redemptions/{redemp.id}/status/")
        self.assertEqual(res_session.status_code, 200)
        self.assertEqual(res_session.json()['id'], redemp.id)

        # Clear session
        session = self.client.session
        session.pop('active_redemption_id', None)
        session.save()

        # 3. Third-party buyer -> 403 Forbidden
        self.client.force_login(other_buyer)
        res_other = self.client.get(f"/api/redemptions/{redemp.id}/status/")
        self.assertEqual(res_other.status_code, 403)

        # 4. Correct buyer -> 200 OK
        self.client.force_login(self.buyer)
        res_buyer = self.client.get(f"/api/redemptions/{redemp.id}/status/")
        self.assertEqual(res_buyer.status_code, 200)

        # 5. Correct seller -> 200 OK
        self.client.force_login(self.seller)
        res_seller = self.client.get(f"/api/redemptions/{redemp.id}/status/")
        self.assertEqual(res_seller.status_code, 200)

    def test_bug68_rejection_reason_saved_and_returned_in_api(self):
        """BUG-68: Rejection reason entered by merchant is saved in DB and returned in redemption_status_api."""
        now = timezone.now()
        redemp = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer,
            seller=self.seller,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="1234",
            otp_expires_at=now + timedelta(minutes=10),
            idempotency_key="bug68_test_key"
        )

        from services.redemption_service import reject_redemption
        success, msg, rejected_redemp = reject_redemption(
            redemp.id,
            self.seller,
            reason="Item out of stock"
        )
        self.assertTrue(success)
        self.assertEqual(rejected_redemp.status, RedemptionStatus.CANCELLED)
        self.assertEqual(rejected_redemp.rejection_reason, "Item out of stock")

        redemp.refresh_from_db()
        self.assertEqual(redemp.status, RedemptionStatus.CANCELLED)
        self.assertEqual(redemp.rejection_reason, "Item out of stock")

        # Now test the polling API as the buyer
        self.client.force_login(self.buyer)
        res = self.client.get(f"/api/redemptions/{redemp.id}/status/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], RedemptionStatus.CANCELLED)
        self.assertEqual(data['rejection_reason'], "Item out of stock")
        self.assertIn("Item out of stock", data['message'])
