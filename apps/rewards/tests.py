from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import Profile, Role
from apps.shops.models import Shop, ShopCategory
from apps.offers.models import Offer, OfferType, OfferStatus
from services.reward_service import (
    calculate_offer_reward_points,
    award_points,
    spend_points,
    get_or_create_reward_account
)


class CoreRewardCalculationTests(TestCase):
    """
    Unit tests for Dynamic 1% Point Calculation on offer's min_purchase (capped at 25 points).
    """

    def test_one_percent_points_calculation_rules(self):
        """
        Verify explicit requirements:
        - ₹200 min_purchase -> 2 points
        - ₹300 min_purchase -> 3 points
        - ₹1,000 min_purchase -> 10 points
        - ₹3,000+ min_purchase -> Capped at maximum 25 points
        """
        self.assertEqual(calculate_offer_reward_points(Decimal('200.00')), 2)
        self.assertEqual(calculate_offer_reward_points(Decimal('300.00')), 3)
        self.assertEqual(calculate_offer_reward_points(Decimal('1000.00')), 10)
        self.assertEqual(calculate_offer_reward_points(Decimal('3000.00')), 25)
        self.assertEqual(calculate_offer_reward_points(Decimal('5000.00')), 25)

    def test_edge_cases_and_caps(self):
        """
        Verify edge cases: 0, small amounts, strings, floats, and large caps.
        """
        # ₹0 or negative
        self.assertEqual(calculate_offer_reward_points(Decimal('0.00')), 0)
        self.assertEqual(calculate_offer_reward_points(0), 0)
        self.assertEqual(calculate_offer_reward_points(None), 0)
        self.assertEqual(calculate_offer_reward_points(Decimal('-50.00')), 0)

        # Less than ₹100
        self.assertEqual(calculate_offer_reward_points(Decimal('50.00')), 0)
        self.assertEqual(calculate_offer_reward_points(Decimal('99.00')), 0)

        # ₹100 min_purchase -> 1 point
        self.assertEqual(calculate_offer_reward_points(Decimal('100.00')), 1)

        # ₹250 min_purchase -> 2 points
        self.assertEqual(calculate_offer_reward_points(Decimal('250.00')), 2)

        # Large amounts capped at 25 points
        self.assertEqual(calculate_offer_reward_points(Decimal('2500.00')), 25)
        self.assertEqual(calculate_offer_reward_points(Decimal('10000.00')), 25)

    def test_offer_model_dynamic_points_integration(self):
        """
        Verify Offer model automatically calculates and stores 1% reward points on save.
        """
        seller = User.objects.create_user(username="merch1@example.com", password="pass123Password!")
        Profile.objects.create(user=seller, role=Role.SELLER)

        category = ShopCategory.objects.create(name="Apparel", slug="apparel")
        shop = Shop.objects.create(
            seller=seller,
            category=category,
            name="Pune Fashion Hub",
            city="Pune"
        )

        now = timezone.now()
        offer = Offer.objects.create(
            shop=shop,
            title="Monsoon Festive Sale",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('15.00'),
            min_purchase=Decimal('1000.00'),
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=10),
            max_redemptions=100
        )

        # Verify dynamic points property and stored field
        self.assertEqual(offer.calculate_reward_points(), 10)
        self.assertEqual(offer.dynamic_reward_points, 10)
        self.assertEqual(offer.reward_points_per_redemption, 10)

        # Update min_purchase to ₹3500 (should cap at 25 points)
        offer.min_purchase = Decimal('3500.00')
        offer.save()
        self.assertEqual(offer.calculate_reward_points(), 25)
        self.assertEqual(offer.reward_points_per_redemption, 25)


class RewardCatalogAndBumperTests(TestCase):
    """
    Unit tests for:
    - Dynamic Admin Reward Burn Catalog (create, toggle, burn points, generate voucher code)
    - Bumper Rewards for ₹10,000+ purchases (submission, validation, counter/admin approval, 100-pt reward)
    """

    def setUp(self):
        from django.test import Client
        self.client = Client()

        # 1. Admin
        self.admin = User.objects.create_superuser(
            username="admin_test@example.com",
            email="admin_test@example.com",
            password="adminPass123!"
        )
        Profile.objects.create(user=self.admin, role=Role.ADMIN)

        # 2. Seller & Shop
        self.seller = User.objects.create_user(
            username="seller_bumper@example.com",
            email="seller_bumper@example.com",
            password="sellerPass123!"
        )
        Profile.objects.create(user=self.seller, role=Role.SELLER)
        self.category = ShopCategory.objects.create(name="Jewellery & Luxury", slug="jewellery-luxury")
        self.shop = Shop.objects.create(
            seller=self.seller,
            category=self.category,
            name="Royal Diamonds & Gold",
            city="Mumbai",
            slug="royal-diamonds-gold"
        )

        # 3. Buyer
        self.buyer = User.objects.create_user(
            username="buyer_vip@example.com",
            email="buyer_vip@example.com",
            password="buyerPass123!"
        )
        Profile.objects.create(user=self.buyer, role=Role.BUYER)
        self.reward_account = get_or_create_reward_account(self.buyer)
        self.reward_account.current_points = 200
        self.reward_account.lifetime_earned = 200
        self.reward_account.save()

    def test_admin_catalog_item_creation_and_toggle(self):
        """Admin creates catalog items and toggles active status dynamically."""
        from apps.rewards.models import RewardCatalogItem, CatalogRewardType

        item = RewardCatalogItem.objects.create(
            title="₹50 In-Store Voucher",
            points_required=50,
            description="Flat ₹50 off across partner stores",
            reward_type=CatalogRewardType.DISCOUNT_VOUCHER,
            voucher_code_prefix="SAVE50",
            discount_inr=Decimal('50.00'),
            is_active=True
        )

        self.assertEqual(item.points_required, 50)
        self.assertTrue(item.is_active)
        self.assertTrue(item.is_in_stock)

        # Admin toggle view
        self.client.force_login(self.admin)
        res_toggle = self.client.post(f"/admin-portal/rewards/{item.id}/toggle/")
        self.assertEqual(res_toggle.status_code, 302)

        item.refresh_from_db()
        self.assertFalse(item.is_active)

    def test_buyer_point_redemption_debit_and_voucher_generation(self):
        """Buyer burns points to redeem a catalog reward: points debited, voucher code generated."""
        from apps.rewards.models import RewardCatalogItem, CatalogRewardType, BuyerRewardClaim, RewardType
        from services.reward_service import redeem_catalog_reward

        item = RewardCatalogItem.objects.create(
            title="Free Gourmet Coffee",
            points_required=75,
            reward_type=CatalogRewardType.FOOD_BEVERAGE,
            voucher_code_prefix="COFFEE",
            is_active=True
        )

        initial_points = self.reward_account.current_points  # 200

        success, msg, claim = redeem_catalog_reward(self.buyer, item.id)
        self.assertTrue(success)
        self.assertIsNotNone(claim)
        self.assertTrue(claim.voucher_code.startswith("COFFEE-"))
        self.assertEqual(claim.points_spent, 75)
        self.assertEqual(claim.status, 'ACTIVE')

        # Check ledger
        self.reward_account.refresh_from_db()
        self.assertEqual(self.reward_account.current_points, initial_points - 75)
        self.assertEqual(self.reward_account.lifetime_spent, 75)

        tx = self.reward_account.transactions.first()
        self.assertEqual(tx.transaction_type, RewardType.CATALOG_REDEMPTION)
        self.assertEqual(tx.points, -75)

    def test_buyer_insufficient_points_redemption_rejected(self):
        """Buyer cannot burn points if balance is below required points."""
        from apps.rewards.models import RewardCatalogItem
        from services.reward_service import redeem_catalog_reward

        expensive_item = RewardCatalogItem.objects.create(
            title="Luxury Handbag Voucher",
            points_required=500,  # Buyer only has 200
            is_active=True
        )

        success, msg, claim = redeem_catalog_reward(self.buyer, expensive_item.id)
        self.assertFalse(success)
        self.assertIsNone(claim)
        self.assertIn("Insufficient", msg)

        # Balance remains unchanged
        self.reward_account.refresh_from_db()
        self.assertEqual(self.reward_account.current_points, 200)

    def test_bumper_bill_submission_validation(self):
        """Bumper bills require minimum ₹10,000 purchase."""
        from services.reward_service import submit_bumper_bill

        # 1. Less than ₹10,000 -> rejected
        success_low, msg_low, sub_low = submit_bumper_bill(
            buyer_user=self.buyer,
            shop_id=self.shop.id,
            bill_number="INV-4001",
            bill_amount=Decimal('8500.00')
        )
        self.assertFalse(success_low)
        self.assertIsNone(sub_low)
        self.assertIn("10,000", msg_low)

        # 2. ₹10,000+ -> accepted in PENDING_STORE status
        success_high, msg_high, sub_high = submit_bumper_bill(
            buyer_user=self.buyer,
            shop_id=self.shop.id,
            bill_number="INV-4002",
            bill_amount=Decimal('15200.00')
        )
        self.assertTrue(success_high)
        self.assertIsNotNone(sub_high)
        self.assertIn(sub_high.status, ['PENDING_STORE', 'PENDING'])
        self.assertEqual(sub_high.points_awarded, 100)

    def test_bumper_bill_approval_workflow(self):
        """Two-tier bumper bill workflow: merchant verifies (Step 1), admin clears (Step 2) granting points and VIP voucher."""
        from services.reward_service import submit_bumper_bill, store_verify_bumper_bill, admin_clear_bumper_bill
        from apps.rewards.models import BumperBillStatus

        initial_points = self.reward_account.current_points

        # Buyer submits ₹12,500 bill
        _, _, sub = submit_bumper_bill(
            buyer_user=self.buyer,
            shop_id=self.shop.id,
            bill_number="GOLD-8821",
            bill_amount=Decimal('12500.00')
        )

        # Step 1: Store merchant verifies receipt (does NOT mint points)
        store_success, store_msg, store_sub = store_verify_bumper_bill(
            submission_id=sub.id,
            merchant_user=self.seller,
            approve=True
        )
        self.assertTrue(store_success)
        self.assertEqual(store_sub.status, BumperBillStatus.STORE_VERIFIED)
        self.assertEqual(store_sub.store_verified_by, self.seller)
        self.assertIsNotNone(store_sub.store_verified_at)

        # Points not yet credited
        self.reward_account.refresh_from_db()
        self.assertEqual(self.reward_account.current_points, initial_points)

        # Step 2: Platform Central Admin final clearance
        admin_success, admin_msg, approved_sub = admin_clear_bumper_bill(
            submission_id=sub.id,
            admin_user=self.admin,
            approve=True
        )

        self.assertTrue(admin_success)
        self.assertEqual(approved_sub.status, BumperBillStatus.APPROVED)
        self.assertTrue(approved_sub.voucher_code.startswith("BUMPER-VIP-"))
        self.assertEqual(approved_sub.admin_reviewed_by, self.admin)

        # Buyer receives 100 bonus points
        self.reward_account.refresh_from_db()
        self.assertEqual(self.reward_account.current_points, initial_points + 100)

    def test_bumper_bill_rejection_workflow(self):
        """Rejecting bumper bill records rejection reason without awarding points."""
        from services.reward_service import submit_bumper_bill, verify_bumper_bill

        initial_points = self.reward_account.current_points

        _, _, sub = submit_bumper_bill(
            buyer_user=self.buyer,
            shop_id=self.shop.id,
            bill_number="FAKE-001",
            bill_amount=Decimal('20000.00')
        )

        rej_success, rej_msg, rejected_sub = verify_bumper_bill(
            submission_id=sub.id,
            reviewer_user=self.seller,
            approve=False,
            rejection_reason="Duplicate receipt detected"
        )

        self.assertTrue(rej_success)
        self.assertEqual(rejected_sub.status, 'REJECTED')
        self.assertEqual(rejected_sub.rejection_reason, "Duplicate receipt detected")

        # Points must NOT be added
        self.reward_account.refresh_from_db()
        self.assertEqual(self.reward_account.current_points, initial_points)

    def test_seller_and_admin_review_endpoints(self):
        """Test HTTP review endpoints in Seller & Admin portals."""
        from services.reward_service import submit_bumper_bill

        _, _, sub = submit_bumper_bill(
            buyer_user=self.buyer,
            shop_id=self.shop.id,
            bill_number="BILL-9988",
            bill_amount=Decimal('11000.00')
        )

        # 1. Seller desk view & store verification (Step 1)
        self.client.force_login(self.seller)
        resp_seller = self.client.get('/seller/bill-verifications/')
        self.assertEqual(resp_seller.status_code, 200)
        self.assertContains(resp_seller, "BILL-9988")
        resp_seller_approve = self.client.post(f"/seller/bill-verifications/{sub.id}/approve/")
        self.assertEqual(resp_seller_approve.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'STORE_VERIFIED')

        # 2. Admin desk view
        self.client.force_login(self.admin)
        resp_admin = self.client.get('/admin-portal/bumper-bills/')
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "BILL-9988")

        # 3. Admin approves
        resp_approve = self.client.post(f"/admin-portal/bumper-bills/{sub.id}/approve/")
        self.assertEqual(resp_approve.status_code, 302)

        sub.refresh_from_db()
        self.assertEqual(sub.status, 'APPROVED')


class LeaderboardAndAntiAbuseTests(TestCase):
    """
    Tests for Weekly Shopper Leaderboard, 3-Cycle Anti-Monopoly Cooldown,
    IP Cluster Fraud Guardrail, and Seller Loyalty Perks.
    """

    def setUp(self):
        from apps.accounts.models import BuyerProfile, Profile, Role
        from apps.shops.models import Shop, ShopCategory
        from apps.wallets.models import SellerWallet

        # Create Category
        self.category = ShopCategory.objects.create(name="Clothing & Apparel", slug="clothing-apparel")

        # Create Sellers
        self.seller1 = User.objects.create_user(username="seller1", email="seller1@lokalloop.in", password="Password@123")
        Profile.objects.create(user=self.seller1, role=Role.SELLER)
        self.seller_wallet = SellerWallet.objects.create(seller=self.seller1, balance_credits=50, loyalty_points=0)
        self.shop = Shop.objects.create(
            seller=self.seller1,
            name="Alpha Fashion Store",
            slug="alpha-fashion",
            category=self.category,
            address="123 Main Street",
            is_active=True
        )

        # Create Buyers with distinct profiles
        self.buyer_a = User.objects.create_user(username="buyer_alpha", email="alpha@secret.com", first_name="John", last_name="Doe", password="Password@123")
        Profile.objects.create(user=self.buyer_a, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_a).delete()
        self.profile_a = BuyerProfile.objects.create(user=self.buyer_a, nickname="SonicShopper", public_unique_id="LL-SONIC-01")

        self.buyer_b = User.objects.create_user(username="buyer_beta", email="beta@secret.com", first_name="Jane", last_name="Smith", password="Password@123")
        Profile.objects.create(user=self.buyer_b, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_b).delete()
        self.profile_b = BuyerProfile.objects.create(user=self.buyer_b, nickname="CyberPanda", public_unique_id="LL-PANDA-02")

        self.buyer_c = User.objects.create_user(username="buyer_gamma", email="gamma@secret.com", first_name="Alice", last_name="Wonder", password="Password@123")
        Profile.objects.create(user=self.buyer_c, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_c).delete()
        self.profile_c = BuyerProfile.objects.create(user=self.buyer_c, nickname="VelvetFox", public_unique_id="LL-FOX-03")

        self.buyer_old = User.objects.create_user(username="buyer_old", email="old@secret.com", password="Password@123")
        Profile.objects.create(user=self.buyer_old, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_old).delete()
        self.profile_old = BuyerProfile.objects.create(user=self.buyer_old, nickname="TimeTraveler", public_unique_id="LL-TIME-99")

        # Reward accounts
        from apps.rewards.models import BuyerRewardAccount
        self.acc_a, _ = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_a)
        self.acc_b, _ = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_b)
        self.acc_c, _ = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_c)
        self.acc_old, _ = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_old)

    def test_weekly_leaderboard_7_day_calculation_and_privacy(self):
        """
        Verify:
        1. Only points earned within last 7 days are aggregated.
        2. Correct descending ranking.
        3. Zero personal info leakage (no real names or emails).
        """
        from apps.rewards.models import RewardTransaction, RewardType
        from services.leaderboard_service import get_weekly_leaderboard
        from datetime import timedelta
        from django.utils import timezone

        now = timezone.now()

        # Buyer A: 50 points 2 days ago
        tx1 = RewardTransaction.objects.create(
            account=self.acc_a,
            points=50,
            balance_after=50,
            transaction_type=RewardType.REDEMPTION_REWARD,
            notes="Offer 1"
        )
        RewardTransaction.objects.filter(id=tx1.id).update(created_at=now - timedelta(days=2))

        # Buyer B: 30 points 1 day ago
        tx2 = RewardTransaction.objects.create(
            account=self.acc_b,
            points=30,
            balance_after=30,
            transaction_type=RewardType.REDEMPTION_REWARD,
            notes="Offer 2"
        )
        RewardTransaction.objects.filter(id=tx2.id).update(created_at=now - timedelta(days=1))

        # Buyer C: 15 points 4 days ago
        tx3 = RewardTransaction.objects.create(
            account=self.acc_c,
            points=15,
            balance_after=15,
            transaction_type=RewardType.REDEMPTION_REWARD,
            notes="Offer 3"
        )
        RewardTransaction.objects.filter(id=tx3.id).update(created_at=now - timedelta(days=4))

        # Buyer Old: 100 points 12 days ago (should be EXCLUDED from 7-day leaderboard)
        tx_old = RewardTransaction.objects.create(
            account=self.acc_old,
            points=100,
            balance_after=100,
            transaction_type=RewardType.REDEMPTION_REWARD,
            notes="Ancient Offer"
        )
        RewardTransaction.objects.filter(id=tx_old.id).update(created_at=now - timedelta(days=12))

        data = get_weekly_leaderboard(cycle_days=7)
        leaderboard = data['leaderboard']

        # Ensure Buyer Old is not in the active 7-day leaderboard
        buyer_ids = [entry['buyer_id'] for entry in leaderboard]
        self.assertNotIn(self.buyer_old.id, buyer_ids)
        self.assertEqual(len(leaderboard), 3)

        # Ranks must be #1 Buyer A (50), #2 Buyer B (30), #3 Buyer C (15)
        self.assertEqual(leaderboard[0]['gennie_nickname'], "SonicShopper")
        self.assertEqual(leaderboard[0]['points_earned'], 50)
        self.assertEqual(leaderboard[0]['rank'], 1)
        self.assertEqual(leaderboard[0]['badge_icon'], "🥇")

        self.assertEqual(leaderboard[1]['gennie_nickname'], "CyberPanda")
        self.assertEqual(leaderboard[1]['points_earned'], 30)
        self.assertEqual(leaderboard[1]['rank'], 2)
        self.assertEqual(leaderboard[1]['badge_icon'], "🥈")

        self.assertEqual(leaderboard[2]['gennie_nickname'], "VelvetFox")
        self.assertEqual(leaderboard[2]['points_earned'], 15)
        self.assertEqual(leaderboard[2]['rank'], 3)
        self.assertEqual(leaderboard[2]['badge_icon'], "🥉")

        # Privacy verification: check that no email or real name appears in dictionary
        for entry in leaderboard:
            self.assertNotIn("alpha@secret.com", str(entry))
            self.assertNotIn("John", str(entry))
            self.assertNotIn("Doe", str(entry))
            self.assertNotIn("beta@secret.com", str(entry))
            self.assertNotIn("Jane", str(entry))

    def test_three_cycle_cooldown_blocks_repeat_grand_prize(self):
        """
        If Buyer A won Cycle 1, and in Cycle 2 Buyer A has the highest points,
        Buyer A remains visible at Rank #1 with Cooldown Active, but cannot win Grand Prize.
        Grand Prize rolls to Buyer B.
        """
        from apps.rewards.models import RewardTransaction, RewardType, LeaderboardHistory
        from services.leaderboard_service import get_weekly_leaderboard, record_cycle_winner, is_buyer_in_cooldown

        # Record Buyer A as winner of Cycle 1
        record_cycle_winner(cycle_number=1, winner_user=self.buyer_a, points_scored=80, rank=1)

        # We are now in Cycle 2
        in_cd, last_win = is_buyer_in_cooldown(self.buyer_a, current_cycle=2)
        self.assertTrue(in_cd)
        self.assertEqual(last_win, 1)

        # Buyer B is NOT in cooldown
        in_cd_b, _ = is_buyer_in_cooldown(self.buyer_b, current_cycle=2)
        self.assertFalse(in_cd_b)

        # Add points for Cycle 2: Buyer A = 70 pts, Buyer B = 40 pts
        RewardTransaction.objects.create(
            account=self.acc_a,
            points=70,
            balance_after=120,
            transaction_type=RewardType.REDEMPTION_REWARD,
            notes="Cycle 2 purchase"
        )
        RewardTransaction.objects.create(
            account=self.acc_b,
            points=40,
            balance_after=70,
            transaction_type=RewardType.REDEMPTION_REWARD,
            notes="Cycle 2 purchase"
        )

        data = get_weekly_leaderboard(cycle_days=7)
        leaderboard = data['leaderboard']

        # Buyer A is Rank 1 by points, but has in_cooldown=True and is_grand_prize_winner=False
        entry_a = next(e for e in leaderboard if e['buyer_id'] == self.buyer_a.id)
        self.assertEqual(entry_a['rank'], 1)
        self.assertTrue(entry_a['in_cooldown'])
        self.assertFalse(entry_a['is_grand_prize_winner'])

        # Buyer B is Rank 2 by points, but is awarded the Grand Prize
        entry_b = next(e for e in leaderboard if e['buyer_id'] == self.buyer_b.id)
        self.assertEqual(entry_b['rank'], 2)
        self.assertFalse(entry_b['in_cooldown'])
        self.assertTrue(entry_b['is_grand_prize_winner'])

        # After 3 cycles (e.g. Cycle 5), cooldown must expire for Buyer A
        in_cd_c5, _ = is_buyer_in_cooldown(self.buyer_a, current_cycle=5)
        self.assertFalse(in_cd_c5)

    def test_ip_fraud_guardrail_rate_limits_cluster(self):
        """
        Verify:
        - 3 distinct accounts redeeming from external IP 203.0.113.88 are allowed.
        - 4th distinct account from the same IP within 10 minutes is blocked.
        - Security anomaly audit log is recorded in AdminAction.
        """
        from apps.redemptions.models import OfferRedemption, RedemptionStatus
        from apps.offers.models import Offer, OfferType, OfferStatus
        from apps.administration.models import AdminAction
        from services.leaderboard_service import check_ip_fraud_guardrail
        from django.utils import timezone

        test_ip = "203.0.113.88"
        offer = Offer.objects.create(
            shop=self.shop,
            title="Special Festive Treat",
            slug="special-festive-treat",
            offer_type=OfferType.FLAT,
            discount_value=Decimal('50.00'),
            min_purchase=Decimal('500.00'),
            end_date=timezone.now() + timedelta(days=30),
            status=OfferStatus.ACTIVE
        )

        # Create 3 prior redemptions from 3 distinct buyer accounts on this IP within 5 minutes
        for idx, buyer in enumerate([self.buyer_a, self.buyer_b, self.buyer_c]):
            OfferRedemption.objects.create(
                offer=offer,
                shop=self.shop,
                buyer=buyer,
                seller=self.seller1,
                idempotency_key=f"idem-test-{idx}",
                status=RedemptionStatus.SUCCESS,
                ip_address=test_ip,
                device_token=f"device-uuid-{idx}",
                redeemed_at=timezone.now()
            )

        # 4th distinct buyer attempts redemption from the same IP
        allowed, msg = check_ip_fraud_guardrail(test_ip, self.buyer_old.id)
        self.assertFalse(allowed)
        self.assertIn("Rate limit exceeded", msg)

        # Verify AdminAction log recorded
        action = AdminAction.objects.filter(
            action_type="SECURITY_ANOMALY_IP_CLUSTER",
            ip_address=test_ip
        ).first()
        self.assertIsNotNone(action)
        self.assertIn("IP Cluster Guardrail", action.details)

        # But existing buyer from the cluster (self.buyer_a) doesn't get blocked by their own account
        allowed_own, _ = check_ip_fraud_guardrail(test_ip, self.buyer_a.id)
        # Because other distinct accounts = buyer_b and buyer_c (2 accounts), count < 3 -> allowed
        self.assertTrue(allowed_own)

    def test_seller_loyalty_perks_unlock_50_and_100_points(self):
        """
        Verify:
        - Reaching 50 loyalty points unlocks 1-week Featured / Priority Store badge.
        - Reaching 100 loyalty points unlocks 2-week Top Ranked Outlet Spotlight.
        """
        from services.leaderboard_service import check_and_unlock_seller_loyalty_perks

        # 1. 25 points -> No perks yet
        self.seller_wallet.loyalty_points = 25
        self.seller_wallet.save()
        perks_25 = check_and_unlock_seller_loyalty_perks(self.seller_wallet)
        self.assertEqual(len(perks_25), 0)
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_featured_active)
        self.assertFalse(self.shop.is_spotlight_active)

        # 2. 50 points -> Unlocks Featured for 7 days
        self.seller_wallet.loyalty_points = 50
        self.seller_wallet.save()
        perks_50 = check_and_unlock_seller_loyalty_perks(self.seller_wallet)
        self.assertEqual(len(perks_50), 1)
        self.assertIn("Featured", perks_50[0])

        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_featured_active)
        self.assertFalse(self.shop.is_spotlight_active)
        self.assertIsNotNone(self.shop.featured_until)

        # 3. 100 points -> Unlocks Spotlight for 14 days
        self.seller_wallet.loyalty_points = 100
        self.seller_wallet.save()
        perks_100 = check_and_unlock_seller_loyalty_perks(self.seller_wallet)
        self.assertEqual(len(perks_100), 1)
        self.assertIn("Spotlight", perks_100[0])

        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_featured_active)
        self.assertTrue(self.shop.is_spotlight_active)
        self.assertIsNotNone(self.shop.spotlight_until)

    def test_leaderboard_view_renders_successfully(self):
        """Test HTTP GET /buyer/leaderboard/ renders 200 with anonymous shopper details."""
        self.client.force_login(self.buyer_a)
        response = self.client.get('/buyer/leaderboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Weekly Shopper Leaderboard")
        self.assertContains(response, "SonicShopper")
        # Ensure email or real name is NEVER present in HTML output
        self.assertNotContains(response, "alpha@secret.com")
        self.assertNotContains(response, "John Doe")


class SellerLoyaltyTierUITests(TestCase):
    """
    Unit tests for Seller Dashboard Merchant Loyalty Tier Banner
    and 4th KPI Card in Wallet & Credits view.
    """

    def setUp(self):
        from apps.wallets.models import SellerWallet
        from apps.shops.models import Shop, ShopCategory
        from django.test import Client

        self.client = Client()
        self.seller_user = User.objects.create_user(
            username='loyalty_merchant@test.com',
            email='loyalty_merchant@test.com',
            password='Test#Merchant123!'
        )
        Profile.objects.create(user=self.seller_user, role=Role.SELLER, city='Pune')
        self.wallet = SellerWallet.objects.create(
            seller=self.seller_user,
            balance_credits=100,
            loyalty_points=0
        )
        self.category = ShopCategory.objects.create(name='Electronics', slug='electronics', icon='bi-laptop')
        self.shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Pune Electro Hub',
            locality='FC Road',
            city='Pune'
        )

    def test_seller_wallet_loyalty_properties(self):
        # 1. 0 Points -> Standard Store Tier
        self.assertEqual(self.wallet.loyalty_tier_name, "Standard Store Tier")
        self.assertEqual(self.wallet.points_to_next_perk, 50)
        self.assertEqual(self.wallet.loyalty_progress_percent, 0)
        self.assertFalse(self.wallet.is_featured_store)
        self.assertFalse(self.wallet.is_top_ranked_outlet)

        # 2. 65 Points -> Featured Store Active
        self.wallet.loyalty_points = 65
        self.wallet.save()
        self.assertEqual(self.wallet.loyalty_tier_name, "Featured Store Active")
        self.assertEqual(self.wallet.points_to_next_perk, 35)
        self.assertEqual(self.wallet.loyalty_progress_percent, 65)
        self.assertTrue(self.wallet.is_featured_store)
        self.assertFalse(self.wallet.is_top_ranked_outlet)

        # 3. 120 Points -> Top Ranked Outlet Active
        self.wallet.loyalty_points = 120
        self.wallet.save()
        self.assertEqual(self.wallet.loyalty_tier_name, "Top Ranked Outlet Active")
        self.assertEqual(self.wallet.points_to_next_perk, 0)
        self.assertEqual(self.wallet.loyalty_progress_percent, 100)
        self.assertTrue(self.wallet.is_featured_store)
        self.assertTrue(self.wallet.is_top_ranked_outlet)

    def test_seller_dashboard_loyalty_banner_rendering(self):
        self.client.force_login(self.seller_user)

        # A: Initial 0 points -> Standard Store Tier
        resp = self.client.get('/seller/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Merchant Loyalty Rewards")
        self.assertContains(resp, "0</span> Merchant Loyalty Points Earned")
        self.assertContains(resp, "Standard Store Tier")
        self.assertContains(resp, "50</strong> more counter redemptions to unlock")
        self.assertContains(resp, "50 Pts Milestone")
        self.assertContains(resp, "100 Pts Milestone")

        # B: 55 points -> Featured Store Active
        self.wallet.loyalty_points = 55
        self.wallet.save()
        resp_55 = self.client.get('/seller/dashboard/')
        self.assertEqual(resp_55.status_code, 200)
        self.assertContains(resp_55, "55</span> Merchant Loyalty Points Earned")
        self.assertContains(resp_55, "Featured Store Active")
        self.assertContains(resp_55, "45</strong> more counter redemptions to unlock")

        # C: 105 points -> Top Ranked Outlet Active
        self.wallet.loyalty_points = 105
        self.wallet.save()
        resp_105 = self.client.get('/seller/dashboard/')
        self.assertEqual(resp_105.status_code, 200)
        self.assertContains(resp_105, "105</span> Merchant Loyalty Points Earned")
        self.assertContains(resp_105, "Top Ranked Outlet Active")
        self.assertContains(resp_105, "Maximum Visibility Unlocked")

    def test_seller_credits_4th_kpi_card_rendering(self):
        self.client.force_login(self.seller_user)

        # Set 35 points
        self.wallet.loyalty_points = 35
        self.wallet.save()

        resp = self.client.get('/seller/credits/')
        self.assertEqual(resp.status_code, 200)
        # Check 4-card grid class
        self.assertContains(resp, "d-grid grid-4 gap-3")
        # Check 4th KPI card content
        self.assertContains(resp, "Merchant Loyalty Points")
        self.assertContains(resp, "35 <span style=\"font-size: 1rem; color: var(--text-muted); font-weight: 600;\">Pts</span>")
        self.assertContains(resp, "15 pts</strong> remaining to Featured Store (50 Pts)")

class LeaderboardBugFixesTests(TestCase):
    """
    Dedicated regression test suite for Leaderboard & Merchant Loyalty Perks fixes:
    - BUG-67: Sweeping expired featured and spotlight store status
    - BUG-68: Homepage query prioritizing featured and spotlight stores
    - BUG-69: Podium rollover deduplication when rank 1 is in cooldown
    - BUG-70: Equal points tie-breaker using earliest transaction timestamp
    - BUG-73: Premature rollover prevention without force
    - BUG-74: Accurate rank computation for shoppers outside top 100
    """
    def setUp(self):
        from django.core.cache import cache
        from django.test import Client
        from apps.accounts.models import Profile, BuyerProfile, Role
        from apps.rewards.models import BuyerRewardAccount
        cache.clear()
        self.client = Client()

        self.seller = User.objects.create_user('lb_seller_fix', 'seller_fix@lb.com', 'Pass@123')
        Profile.objects.create(user=self.seller, role=Role.SELLER)

        self.category = ShopCategory.objects.create(name="Retail & Shopping", slug="retail-shopping")

        self.buyer_a = User.objects.create_user('lb_buyer_fix_a', 'a_fix@lb.com', 'Pass@123')
        Profile.objects.create(user=self.buyer_a, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_a).delete()
        BuyerProfile.objects.create(user=self.buyer_a, nickname="ShopperAlpha", public_unique_id="ALPHA-001")
        self.acc_a = BuyerRewardAccount.objects.create(buyer=self.buyer_a, current_points=100)

        self.buyer_b = User.objects.create_user('lb_buyer_fix_b', 'b_fix@lb.com', 'Pass@123')
        Profile.objects.create(user=self.buyer_b, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_b).delete()
        BuyerProfile.objects.create(user=self.buyer_b, nickname="ShopperBeta", public_unique_id="BETA-002")
        self.acc_b = BuyerRewardAccount.objects.create(buyer=self.buyer_b, current_points=100)

        self.buyer_c = User.objects.create_user('lb_buyer_fix_c', 'c_fix@lb.com', 'Pass@123')
        Profile.objects.create(user=self.buyer_c, role=Role.BUYER)
        BuyerProfile.objects.filter(user=self.buyer_c).delete()
        BuyerProfile.objects.create(user=self.buyer_c, nickname="ShopperGamma", public_unique_id="GAMMA-003")
        self.acc_c = BuyerRewardAccount.objects.create(buyer=self.buyer_c, current_points=100)

    def test_bug67_expired_merchant_perks_swept_by_background_job(self):
        """BUG-67: Background task sweeps expired featured and spotlight store status."""
        from apps.shops.models import Shop
        from services.background_tasks import expire_stale_qrcodes_job
        from django.utils import timezone
        from datetime import timedelta

        past = timezone.now() - timedelta(hours=2)

        shop_exp = Shop.objects.create(
            seller=self.seller,
            category=self.category,
            name="Expired Perks Store",
            is_approved=True,
            is_active=True,
            is_featured=True,
            featured_until=past,
            is_spotlight=True,
            spotlight_until=past,
        )

        # Run background sweep task
        result = expire_stale_qrcodes_job()
        self.assertEqual(result['status'], 'success')

        shop_exp.refresh_from_db()
        self.assertFalse(shop_exp.is_featured)
        self.assertFalse(shop_exp.is_spotlight)

    def test_bug68_landing_page_prioritizes_featured_and_spotlight_stores(self):
        """BUG-68: Landing page query orders spotlight and featured shops above regular stores."""
        from apps.shops.models import Shop

        s_regular = Shop.objects.create(seller=self.seller, category=self.category, name="Z Regular Store", is_approved=True, is_active=True)
        s_featured = Shop.objects.create(seller=self.seller, category=self.category, name="A Featured Store", is_approved=True, is_active=True, is_featured=True)
        s_spotlight = Shop.objects.create(seller=self.seller, category=self.category, name="B Spotlight Store", is_approved=True, is_active=True, is_spotlight=True)

        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        shops = list(res.context['featured_shops'])

        # Spotlight should be prioritized before Featured, and Featured before regular
        self.assertEqual(shops[0].id, s_spotlight.id)
        self.assertEqual(shops[1].id, s_featured.id)

    def test_bug69_podium_rollover_no_duplicates_when_rank1_in_cooldown(self):
        """BUG-69: When rank 1 is in cooldown, eligible winner is not duplicated on podium."""
        from services.leaderboard_service import (
            rollover_weekly_leaderboard_cycle,
            record_cycle_winner,
            get_current_cycle_number
        )
        from apps.rewards.models import RewardTransaction, RewardType, LeaderboardHistory
        from django.utils import timezone

        cycle = get_current_cycle_number()
        # Put buyer_a into cooldown
        record_cycle_winner(cycle_number=cycle - 1, winner_user=self.buyer_a, points_scored=200, rank=1)

        now = timezone.now()
        # buyer_a earns 150 points (top points, but in cooldown)
        RewardTransaction.objects.create(account=self.acc_a, transaction_type=RewardType.REDEMPTION_REWARD, points=150, balance_after=150, created_at=now)
        # buyer_b earns 100 points (rank 2, eligible for Grand Prize)
        RewardTransaction.objects.create(account=self.acc_b, transaction_type=RewardType.REDEMPTION_REWARD, points=100, balance_after=100, created_at=now)
        # buyer_c earns 50 points (rank 3)
        RewardTransaction.objects.create(account=self.acc_c, transaction_type=RewardType.REDEMPTION_REWARD, points=50, balance_after=50, created_at=now)

        ok, msg, hist = rollover_weekly_leaderboard_cycle(force=True)
        self.assertTrue(ok)

        # Check podium records for this cycle
        entries = LeaderboardHistory.objects.filter(cycle_number=cycle)
        user_ids = list(entries.values_list('winner_id', flat=True))
        # Ensure no user is recorded twice
        self.assertEqual(len(user_ids), len(set(user_ids)))
        self.assertEqual(entries.get(rank=1).winner, self.buyer_a)
        self.assertEqual(entries.get(rank=2).winner, self.buyer_b)
        self.assertEqual(entries.get(rank=3).winner, self.buyer_c)

    def test_bug70_leaderboard_tie_breaker_uses_earliest_transaction(self):
        """BUG-70: Equal points tie-breaker ranks buyer with earlier transaction ahead."""
        from services.leaderboard_service import get_weekly_leaderboard
        from apps.rewards.models import RewardTransaction, RewardType
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.now()
        # buyer_c earns 40 points 3 hours ago
        tx_c = RewardTransaction.objects.create(
            account=self.acc_c,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=40,
            balance_after=40
        )
        RewardTransaction.objects.filter(id=tx_c.id).update(created_at=now - timedelta(hours=3))

        # buyer_a earns 40 points 1 hour ago
        tx_a = RewardTransaction.objects.create(
            account=self.acc_a,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=40,
            balance_after=40
        )
        RewardTransaction.objects.filter(id=tx_a.id).update(created_at=now - timedelta(hours=1))

        data = get_weekly_leaderboard(cycle_days=7)
        lb = data['leaderboard']

        entry_c = next(e for e in lb if e['buyer_id'] == self.buyer_c.id)
        entry_a = next(e for e in lb if e['buyer_id'] == self.buyer_a.id)

        # buyer_c was earlier, so buyer_c rank must be ahead of buyer_a
        self.assertTrue(entry_c['rank'] < entry_a['rank'])

    def test_bug73_premature_rollover_prevented_without_force(self):
        """BUG-73: Rollover is rejected if less than 6 days have elapsed since last cycle."""
        from services.leaderboard_service import (
            rollover_weekly_leaderboard_cycle,
            record_cycle_winner,
            get_current_cycle_number
        )
        from django.utils import timezone
        from datetime import timedelta

        cycle = get_current_cycle_number()
        # Record previous cycle closed 2 days ago
        record_cycle_winner(
            cycle_number=cycle,
            winner_user=self.buyer_a,
            points_scored=50,
            rank=1,
            cycle_end=timezone.now() - timedelta(days=2)
        )

        ok, msg, hist = rollover_weekly_leaderboard_cycle(force=False)
        self.assertFalse(ok)
        self.assertIn("still active", msg)

    def test_bug74_buyer_outside_top_100_receives_exact_rank(self):
        """BUG-74: Off-leaderboard buyer calculation determines exact rank position."""
        from services.leaderboard_service import get_buyer_current_cycle_rank
        from apps.rewards.models import RewardTransaction, RewardType

        # Create transactions for buyer_a (50 pts) and buyer_b (20 pts)
        RewardTransaction.objects.create(account=self.acc_a, transaction_type=RewardType.REDEMPTION_REWARD, points=50, balance_after=50)
        RewardTransaction.objects.create(account=self.acc_b, transaction_type=RewardType.REDEMPTION_REWARD, points=20, balance_after=20)

        standing_b = get_buyer_current_cycle_rank(self.buyer_b, cycle_days=7)
        self.assertIsNotNone(standing_b)
        self.assertEqual(standing_b['rank'], 2)
        self.assertEqual(standing_b['points_earned'], 20)




    def test_bug69_image_security_detects_mz_header(self):
        """BUG-69: Dangerous byte scan correctly matches uppercase 'MZ' Windows PE header."""
        import io
        from services.image_security_service import validate_image_security, ImageSecurityException

        # Valid PNG signature followed by MZ executable header
        fake_payload = io.BytesIO(b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A' + b'MZ\x90\x00\x03\x00\x00\x00' + b'\x00' * 150)
        fake_payload.name = "malicious_file.png"
        with self.assertRaises(ImageSecurityException) as ctx:
            validate_image_security(fake_payload)
        self.assertIn("executable pattern detected", str(ctx.exception))
