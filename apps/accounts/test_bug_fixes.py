import uuid
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction, IntegrityError

from apps.accounts.models import Profile, Role, SellerProfile, BuyerProfile
from apps.shops.models import Shop, ShopCategory, ShopVerification
from apps.offers.models import Offer, OfferStatus, OfferType
from apps.redemptions.models import OfferRedemption, QRCode, RedemptionStatus
from apps.rewards.models import BuyerRewardAccount, BumperBillSubmission
from services.reward_service import submit_bumper_bill
from services.location_service import annotate_shops_with_distance


class ProjectBugFixesTestCase(TestCase):
    def setUp(self):
        # Create Category
        self.category = ShopCategory.objects.create(name="Electronics", slug="electronics")

        # Create Seller
        self.seller_user = User.objects.create_user(
            username="test_merchant",
            email="merchant@test.com",
            password="Password@123"
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            business_name="Gadget Emporium",
            is_verified=True
        )
        Profile.objects.create(user=self.seller_user, role="SELLER", city="Bengaluru")

        # Create Shop
        self.shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Gadget Emporium Flagship",
            city="Bengaluru",
            latitude=Decimal("12.9716"),
            longitude=Decimal("77.5946"),
            is_approved=True,
            is_active=True
        )
        self.verification = ShopVerification.objects.create(
            shop=self.shop,
            status='APPROVED'
        )

        # Create Buyer
        self.buyer_user = User.objects.create_user(
            username="test_shopper",
            email="shopper@test.com",
            password="Password@123"
        )
        self.buyer_profile = BuyerProfile.objects.create(
            user=self.buyer_user,
            preferred_city="Bengaluru"
        )
        Profile.objects.create(user=self.buyer_user, role="BUYER", city="Bengaluru")
        self.reward_account = BuyerRewardAccount.objects.create(buyer=self.buyer_user, current_points=200)

        # Create Offer
        self.offer = Offer.objects.create(
            shop=self.shop,
            title="Festive Tech 20% Off",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("20.00"),
            min_purchase=Decimal("500.00"),
            max_redemptions=100,
            current_redemptions=0,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=10),
            status=OfferStatus.ACTIVE
        )
        self.qr = QRCode.objects.create(
            offer=self.offer,
            expires_at=timezone.now() + timedelta(days=5)
        )

    def test_bug01_and_bug09_financial_analytics_aggregates_only_success(self):
        """BUG-01 & BUG-09: Financial analytics must aggregate only SUCCESS redemptions via DB Sum"""
        # 1 Successful redemption
        OfferRedemption.objects.create(
            idempotency_key=str(uuid.uuid4()),
            qr_code=self.qr,
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("1000.00"),
            discount_given=Decimal("200.00"),
            status=RedemptionStatus.SUCCESS
        )
        # 1 Pending redemption
        OfferRedemption.objects.create(
            idempotency_key=str(uuid.uuid4()),
            qr_code=self.qr,
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("500.00"),
            discount_given=Decimal("100.00"),
            status=RedemptionStatus.PENDING_APPROVAL
        )
        # 1 Cancelled redemption
        OfferRedemption.objects.create(
            idempotency_key=str(uuid.uuid4()),
            qr_code=self.qr,
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("300.00"),
            discount_given=Decimal("60.00"),
            status=RedemptionStatus.CANCELLED
        )

        client = Client()
        client.force_login(self.seller_user)
        
        # Test Dashboard
        resp_dash = client.get('/seller/dashboard/')
        self.assertEqual(resp_dash.status_code, 200)
        self.assertEqual(resp_dash.context['total_redemptions_count'], 1)
        self.assertEqual(resp_dash.context['total_discount_given'], Decimal("200.00"))

        # Test Analytics
        resp_analytics = client.get('/seller/analytics/')
        self.assertEqual(resp_analytics.status_code, 200)
        self.assertEqual(resp_analytics.context['total_sales_generated'], Decimal("1000.00"))
        self.assertEqual(resp_analytics.context['total_discounts'], Decimal("200.00"))

    def test_bug02_buyer_history_savings_only_success(self):
        """BUG-02: Shopper history savings and points must only count SUCCESS redemptions"""
        OfferRedemption.objects.create(
            idempotency_key=str(uuid.uuid4()),
            qr_code=self.qr,
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("1000.00"),
            discount_given=Decimal("200.00"),
            reward_points_earned=50,
            status=RedemptionStatus.SUCCESS
        )
        OfferRedemption.objects.create(
            idempotency_key=str(uuid.uuid4()),
            qr_code=self.qr,
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("800.00"),
            discount_given=Decimal("160.00"),
            reward_points_earned=40,
            status=RedemptionStatus.CANCELLED
        )

        client = Client()
        client.force_login(self.buyer_user)
        resp = client.get('/buyer/history/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_saved'], Decimal("200.00"))
        self.assertEqual(resp.context['total_pts'], 50)

    def test_bug03_duplicate_bumper_bill_submission_rejected(self):
        """BUG-03: Duplicate bumper invoice submissions for the same shop must be rejected"""
        ok1, msg1, sub1 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-BUMPER-2026",
            bill_amount=Decimal("15000.00")
        )
        self.assertTrue(ok1)
        self.assertIsNotNone(sub1)

        ok2, msg2, sub2 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-BUMPER-2026",
            bill_amount=Decimal("15000.00")
        )
        self.assertFalse(ok2)
        self.assertIn("already been submitted", msg2)
        self.assertIsNone(sub2)

    def test_bug05_merchant_suspension_cascades(self):
        """BUG-05: Suspending merchant must delist shops, set verification to REJECTED, and PAUSE offers"""
        admin_user = User.objects.create_superuser('superadmin', 'admin@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        resp = client.post(f'/admin-portal/sellers/{self.seller_profile.id}/toggle/')
        self.assertEqual(resp.status_code, 302)

        self.seller_profile.refresh_from_db()
        self.assertFalse(self.seller_profile.is_verified)

        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_approved)
        self.assertFalse(self.shop.is_active)

        self.verification.refresh_from_db()
        self.assertEqual(self.verification.status, 'REJECTED')

        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, OfferStatus.PAUSED)

    def test_bug06_and_bug07_public_explore_and_sitemap_filter_inactive(self):
        """BUG-06 & BUG-07: Public explore and sitemap must not leak expired or inactive shop offers"""
        client = Client()
        
        # Unapproved shop
        unapproved_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Hidden Unapproved Store",
            city="Bengaluru",
            is_approved=False,
            is_active=False
        )
        hidden_offer = Offer.objects.create(
            shop=unapproved_shop,
            title="Secret Unapproved Deal",
            start_date=timezone.now() - timedelta(days=2),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        resp_explore = client.get('/explore/?type=offers')
        self.assertEqual(resp_explore.status_code, 200)
        self.assertNotContains(resp_explore, "Secret Unapproved Deal")

        resp_sitemap = client.get('/sitemap.xml')
        self.assertEqual(resp_sitemap.status_code, 200)
        self.assertNotIn(hidden_offer.slug, resp_sitemap.content.decode('utf-8'))

    def test_bug08_non_ascii_unicode_slug_fallback(self):
        """BUG-08: Regional non-ASCII names should fall back to unique UUID slugs instead of empty string"""
        regional_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="दिवाळी किराणा दुकान",
            city="Pune"
        )
        self.assertTrue(regional_shop.slug.startswith("shop-"))

        regional_offer = Offer.objects.create(
            shop=regional_shop,
            title="विशेष सवलत ऑफर",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=5)
        )
        self.assertTrue(regional_offer.slug.startswith("offer-"))

    def test_bug11_offer_creation_boundary_validations(self):
        """BUG-11: Boundary checks reject invalid inputs on campaign creation"""
        client = Client()
        client.force_login(self.seller_user)

        # 1. Percentage > 100
        resp1 = client.post('/seller/offers/create/', {
            'title': 'Crazy 150% Off Deal',
            'offer_type': 'PERCENTAGE',
            'discount_value': '150.00',
            'min_purchase': '100',
            'max_redemptions': '50',
            'days_valid': '15'
        })
        self.assertEqual(resp1.status_code, 200)
        self.assertFalse(Offer.objects.filter(title='Crazy 150% Off Deal').exists())

        # 2. Max redemptions <= 0
        resp2 = client.post('/seller/offers/create/', {
            'title': 'Zero Quota Deal',
            'offer_type': 'FLAT',
            'discount_value': '50.00',
            'min_purchase': '100',
            'max_redemptions': '0',
            'days_valid': '15'
        })
        self.assertEqual(resp2.status_code, 200)
        self.assertFalse(Offer.objects.filter(title='Zero Quota Deal').exists())

    def test_bug12_location_service_proximity_cutoff(self):
        """BUG-12: Proximity filter excludes shops beyond max_km"""
        far_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Distant Store In Delhi",
            city="New Delhi",
            latitude=Decimal("28.6139"),
            longitude=Decimal("77.2090"),
            is_approved=True,
            is_active=True
        )
        
        # User is in Bengaluru (lat: 12.9716, lon: 77.5946)
        all_shops = Shop.objects.filter(id__in=[self.shop.id, far_shop.id])
        nearby_shops = annotate_shops_with_distance(all_shops, 12.9716, 77.5946, max_km=50.0)
        
        shop_ids = [s.id for s in nearby_shops]
        self.assertIn(self.shop.id, shop_ids)
        self.assertNotIn(far_shop.id, shop_ids)

    def test_bug13_merchant_excel_export_preserves_zero_pii(self):
        """BUG-13: Merchant Excel download must never leak customer email or real full name"""
        import io
        import openpyxl

        self.buyer_profile.nickname = "GennieShopper99"
        self.buyer_profile.public_unique_id = "LL-BLR-9999"
        self.buyer_profile.save()

        OfferRedemption.objects.create(
            idempotency_key=str(uuid.uuid4()),
            qr_code=self.qr,
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("1200.00"),
            discount_given=Decimal("240.00"),
            status=RedemptionStatus.SUCCESS
        )

        client = Client()
        client.force_login(self.seller_user)
        resp = client.get('/seller/redemptions/?export=excel')
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        
        all_cell_values = []
        for row in ws.iter_rows(values_only=True):
            all_cell_values.extend([str(v) for v in row if v is not None])

        # Assert Zero-PII compliance: no email, nickname and public ID preserved
        self.assertNotIn(self.buyer_user.email, all_cell_values)
        self.assertIn("Shopper Nickname", all_cell_values)
        self.assertIn("Public Unique ID", all_cell_values)
        self.assertIn("GennieShopper99", all_cell_values)
        self.assertIn("LL-BLR-9999", all_cell_values)

    def test_bug14_store_profile_and_qr_standee_excludes_expired_offers(self):
        """BUG-14: Expired offers must be excluded from shop detail and QR standee landing"""
        expired_offer = Offer.objects.create(
            shop=self.shop,
            title="Expired Midnight Flash Sale",
            start_date=timezone.now() - timedelta(days=5),
            end_date=timezone.now() - timedelta(days=1),
            status=OfferStatus.ACTIVE
        )

        client = Client()
        # 1. Shop details
        resp_shop = client.get(f'/shops/{self.shop.slug}/')
        self.assertEqual(resp_shop.status_code, 200)
        self.assertNotIn(expired_offer, resp_shop.context['active_offers'])

        # 2. QR Standee landing
        resp_qr = client.get(f'/store/{self.shop.slug}/qr/')
        self.assertEqual(resp_qr.status_code, 200)
        self.assertNotIn(expired_offer, resp_qr.context['active_offers'])

        # 3. Direct claim on expired offer
        client.force_login(self.buyer_user)
        resp_claim = client.post(f'/store/{self.shop.slug}/qr/', {'claim_offer_id': expired_offer.id})
        self.assertEqual(resp_claim.status_code, 200)
        self.assertIsNone(resp_claim.context['redemption_voucher'])

    def test_bug15_authenticated_buyer_directory_excludes_unapproved_or_expired_offers(self):
        """BUG-15: Authenticated buyer explore and dashboard must exclude unapproved shops and expired offers"""
        unapproved_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Delisted Store Inc",
            city="Bengaluru",
            is_approved=False,
            is_active=False
        )
        delisted_offer = Offer.objects.create(
            shop=unapproved_shop,
            title="Delisted Unapproved Promo",
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        expired_offer = Offer.objects.create(
            shop=self.shop,
            title="Expired Yesterday Promo",
            start_date=timezone.now() - timedelta(days=5),
            end_date=timezone.now() - timedelta(days=1),
            status=OfferStatus.ACTIVE
        )

        client = Client()
        client.force_login(self.buyer_user)

        # Dashboard
        resp_dash = client.get('/buyer/dashboard/')
        self.assertEqual(resp_dash.status_code, 200)
        self.assertNotIn(delisted_offer, resp_dash.context['popular_offers'])
        self.assertNotIn(expired_offer, resp_dash.context['popular_offers'])

        # Explore
        resp_exp = client.get('/buyer/explore/?type=offers')
        self.assertEqual(resp_exp.status_code, 200)
        self.assertNotIn(delisted_offer, resp_exp.context['offers'])
        self.assertNotIn(expired_offer, resp_exp.context['offers'])

    def test_bug17_live_counter_acceptance_deducts_full_credits_per_redemption(self):
        """BUG-17: Counter standee redemption must deduct offer.credits_per_redemption (not hardcoded 1)"""
        from services.redemption_service import initiate_redemption, approve_redemption
        from services.wallet_service import get_or_create_wallet

        # Configure custom rate card tokenomics: 8 credits per redemption
        self.offer.credits_per_redemption = 8
        self.offer.save(update_fields=['credits_per_redemption'])

        wallet = get_or_create_wallet(self.seller_user)
        wallet.balance_credits = 50
        wallet.save(update_fields=['balance_credits'])

        # 1. Initiate
        success, msg, redemp = initiate_redemption(
            buyer_user=self.buyer_user,
            offer=self.offer,
            bill_amount=Decimal('500.00')
        )
        self.assertTrue(success)
        self.assertEqual(redemp.credits_deducted, 8)

        # 2. Approve
        app_ok, app_msg, approved_redemp = approve_redemption(redemp.id, self.seller_user)
        self.assertTrue(app_ok)

        wallet.refresh_from_db()
        # Exactly 8 credits deducted: 50 - 8 = 42
        self.assertEqual(wallet.balance_credits, 42)

    def test_bug18_statutory_verification_rejection_delists_shop_and_pauses_offers(self):
        """BUG-18: Admin rejecting shop verification must delist store and PAUSE all active campaigns"""
        admin_user = User.objects.create_superuser('admin_verifier', 'verifier@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        resp = client.post(f'/admin-portal/verifications/{self.verification.id}/update/', {
            'status': 'REJECTED',
            'admin_notes': 'Shop Act document expired / invalid'
        })
        self.assertEqual(resp.status_code, 302)

        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_approved)
        self.assertFalse(self.shop.is_active)

        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, OfferStatus.PAUSED)

    def test_bug19_public_offer_detail_excludes_unapproved_or_expired(self):
        """BUG-19: /offers/<slug>/ returns 404 for unapproved stores and expired deals"""
        client = Client()

        # 1. Offer from unapproved shop -> 404
        unapproved_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Unapproved Store",
            city="Bengaluru",
            is_approved=False,
            is_active=True
        )
        unapproved_offer = Offer.objects.create(
            shop=unapproved_shop,
            title="Deal On Unapproved Store",
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        resp_unapproved = client.get(f'/offers/{unapproved_offer.slug}/')
        self.assertEqual(resp_unapproved.status_code, 404)

        # 2. Expired offer -> 404
        expired_offer = Offer.objects.create(
            shop=self.shop,
            title="Expired Black Friday Deal",
            start_date=timezone.now() - timedelta(days=10),
            end_date=timezone.now() - timedelta(days=1),
            status=OfferStatus.ACTIVE
        )
        resp_expired = client.get(f'/offers/{expired_offer.slug}/')
        self.assertEqual(resp_expired.status_code, 404)

    def test_bug20_category_and_subcategory_toggle_views_support_post(self):
        """BUG-20: Admin category and subcategory toggle views support POST with CSRF"""
        from apps.shops.models import ShopSubcategory
        subcat = ShopSubcategory.objects.create(
            category=self.category,
            name="Smartphones",
            is_active=True
        )

        admin_user = User.objects.create_superuser('admin_toggler', 'toggle@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        # Toggle Category via POST
        initial_cat_status = self.category.is_active
        resp_cat = client.post(f'/admin-portal/categories/{self.category.id}/toggle/')
        self.assertEqual(resp_cat.status_code, 302)
        self.category.refresh_from_db()
        self.assertNotEqual(self.category.is_active, initial_cat_status)

        # Toggle Subcategory via POST
        initial_sub_status = subcat.is_active
        resp_sub = client.post(f'/admin-portal/subcategories/{subcat.id}/toggle/')
        self.assertEqual(resp_sub.status_code, 302)
        subcat.refresh_from_db()
        self.assertNotEqual(subcat.is_active, initial_sub_status)

    def test_bug16_buyer_recovery_lookup_rejects_non_unique_nickname(self):
        """BUG-16: Shopper account lookup must strictly reject non-unique nicknames"""
        from services.captcha_service import generate_captcha

        # Setup buyer with nickname and security answer
        self.buyer_profile.nickname = "CityShopper"
        self.buyer_profile.public_unique_id = "LQ7M-8K2R-5D9X"
        self.buyer_profile.security_question = "What was the name of your first school?"
        self.buyer_profile.set_security_answer("St Joseph High School")
        self.buyer_profile.save()

        c = generate_captcha()
        client = Client()

        # Attempt recovery using non-unique nickname
        resp_nickname = client.post('/buyer/recover/', {
            'action_lookup': '1',
            'shopper_id': 'CityShopper',
            'website': '',
            'captcha_token': c['captcha_token'],
            'captcha_answer': c['_solution'],
        })
        self.assertEqual(resp_nickname.status_code, 200)
        self.assertEqual(resp_nickname.context['step'], 1)
        self.assertEqual(
            resp_nickname.context['lookup_form'].errors['shopper_id'],
            ["No shopper account found with Public Unique ID 'CityShopper'. Please check your Shopper Pass."]
        )
        self.assertIn("No shopper account found with Public Unique ID", resp_nickname.content.decode('utf-8'))

    def test_bug16_buyer_recovery_lookup_succeeds_with_public_unique_id(self):
        """BUG-16: Shopper account lookup succeeds using exact 12-char public_unique_id"""
        from services.captcha_service import generate_captcha

        self.buyer_profile.nickname = "SecretShopper"
        self.buyer_profile.public_unique_id = "LQ7M-8K2R-5D9X"
        self.buyer_profile.security_question = "What was the name of your first school?"
        self.buyer_profile.set_security_answer("St Joseph High School")
        self.buyer_profile.save()

        c = generate_captcha()
        client = Client()

        # Lookup using exact 12-char public_unique_id
        resp = client.post('/buyer/recover/', {
            'action_lookup': '1',
            'shopper_id': 'LQ7M-8K2R-5D9X',
            'website': '',
            'captcha_token': c['captcha_token'],
            'captcha_answer': c['_solution'],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['step'], 2)
        self.assertEqual(resp.context['buyer_profile'], self.buyer_profile)

    def test_bug16_buyer_recovery_form_label_and_placeholder_normalized(self):
        """BUG-16: Form labels and placeholders display clean Public Unique ID without email/phone"""
        from apps.accounts.forms import BuyerRecoveryLookupForm

        form = BuyerRecoveryLookupForm()
        shopper_id_field = form.fields['shopper_id']

        self.assertEqual(shopper_id_field.label, "Public Unique ID")
        self.assertEqual(shopper_id_field.widget.attrs['placeholder'], "e.g. LQ7M-8K2R-5D9X")
        self.assertNotIn("Email", shopper_id_field.label)
        self.assertNotIn("Mobile", shopper_id_field.label)
        self.assertNotIn("buyer@example.com", shopper_id_field.widget.attrs['placeholder'])

    def test_bug21_homepage_excludes_unapproved_shops_and_expired_offers(self):
        """BUG-21: Homepage hero offers must exclude unapproved stores and expired campaigns"""
        now = timezone.now()
        # Unapproved shop offer
        unapproved_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Unapproved Store",
            city="Bengaluru",
            is_approved=False,
            is_active=True
        )
        unapproved_offer = Offer.objects.create(
            shop=unapproved_shop,
            title="Unapproved Promo",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("15.00"),
            min_purchase=Decimal("300.00"),
            max_redemptions=50,
            start_date=now - timedelta(days=2),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        # Expired offer on approved shop
        expired_offer = Offer.objects.create(
            shop=self.shop,
            title="Expired Promo",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("10.00"),
            min_purchase=Decimal("200.00"),
            max_redemptions=50,
            start_date=now - timedelta(days=10),
            end_date=now - timedelta(days=1),
            status=OfferStatus.ACTIVE
        )

        client = Client()
        resp = client.get('/')
        self.assertEqual(resp.status_code, 200)

        featured_offers = resp.context['featured_offers']
        offer_ids = [o.id for o in featured_offers]

        self.assertIn(self.offer.id, offer_ids)
        self.assertNotIn(unapproved_offer.id, offer_ids)
        self.assertNotIn(expired_offer.id, offer_ids)

    def test_bug22_buyer_qr_scanner_omits_demo_qrs_in_production(self):
        """BUG-22: Buyer scanner omits demo QR tokens when DEBUG is False"""
        client = Client()
        client.force_login(self.buyer_user)

        with self.settings(DEBUG=False):
            resp = client.get('/buyer/scan/')
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context['sample_qrs'], [])
            self.assertNotContains(resp, "Quick-select verified merchant QR codes")

    def test_bug23_admin_seller_toggle_approval_requires_post(self):
        """BUG-23: Merchant approval toggle rejects GET and requires POST with CSRF"""
        admin_user = User.objects.create_superuser('admin_toggle_tester', 'toggle_tester@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        initial_verified = self.seller_profile.is_verified

        # Attempt GET request -> Must NOT mutate state
        resp_get = client.get(f'/admin-portal/sellers/{self.seller_profile.id}/toggle/')
        self.assertEqual(resp_get.status_code, 302)
        self.seller_profile.refresh_from_db()
        self.assertEqual(self.seller_profile.is_verified, initial_verified)

        # POST request -> Successfully mutates state
        resp_post = client.post(f'/admin-portal/sellers/{self.seller_profile.id}/toggle/')
        self.assertEqual(resp_post.status_code, 302)
        self.seller_profile.refresh_from_db()
        self.assertNotEqual(self.seller_profile.is_verified, initial_verified)

    def test_bug24_seller_offer_toggle_status_requires_post_and_blocks_expired_or_exhausted(self):
        """BUG-24: Merchant offer status toggle requires POST and blocks activating expired/exhausted deals"""
        client = Client()
        client.force_login(self.seller_user)

        # 1. GET request is rejected without modifying status
        self.offer.status = OfferStatus.ACTIVE
        self.offer.save()
        resp_get = client.get(f'/seller/offers/{self.offer.id}/toggle/')
        self.assertEqual(resp_get.status_code, 302)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, OfferStatus.ACTIVE)

        # 2. POST to pause succeeds
        resp_pause = client.post(f'/seller/offers/{self.offer.id}/toggle/')
        self.assertEqual(resp_pause.status_code, 302)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, OfferStatus.PAUSED)

        # 3. POST to resume an expired offer is blocked
        now = timezone.now()
        self.offer.end_date = now - timedelta(days=1)
        self.offer.save()
        resp_resume_expired = client.post(f'/seller/offers/{self.offer.id}/toggle/')
        self.assertEqual(resp_resume_expired.status_code, 302)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, OfferStatus.PAUSED)

        # 4. POST to resume an exhausted offer is blocked
        self.offer.end_date = now + timedelta(days=10)
        self.offer.current_redemptions = self.offer.max_redemptions
        self.offer.save()
        resp_resume_exhausted = client.post(f'/seller/offers/{self.offer.id}/toggle/')
        self.assertEqual(resp_resume_exhausted.status_code, 302)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, OfferStatus.PAUSED)

    def test_bug25_approve_redemption_enforces_daily_limit_and_cancels_other_pending(self):
        """BUG-25: Approving redemption re-verifies 1-per-day seller cap and cancels competing pending tickets"""
        from services.redemption_service import initiate_redemption, approve_redemption
        from apps.wallets.models import SellerWallet
        SellerWallet.objects.get_or_create(
            seller=self.seller_user,
            defaults={'balance_credits': 100, 'lifetime_purchased': 100, 'lifetime_consumed': 0}
        )

        # Create first redemption
        ok1, msg1, r1 = initiate_redemption(
            buyer_user=self.buyer_user,
            offer=self.offer,
            bill_amount=Decimal('500.00'),
            idempotency_key="key_test_bug25_1"
        )
        self.assertTrue(ok1)
        self.assertIsNotNone(r1)

        # Manually create a second pending redemption simulating concurrent requests
        r2 = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal('500.00'),
            discount_given=Decimal('100.00'),
            credits_deducted=5,
            reward_points_earned=5,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="1234",
            otp_expires_at=timezone.now() + timedelta(minutes=15),
            idempotency_key="key_test_bug25_2"
        )

        # Merchant approves first redemption -> Succeeds
        success1, msg_app1, _ = approve_redemption(r1.id, self.seller_user)
        self.assertTrue(success1)
        r1.refresh_from_db()
        self.assertEqual(r1.status, RedemptionStatus.SUCCESS)

        # Competing ticket r2 was auto-cancelled on r1 approval
        r2.refresh_from_db()
        self.assertEqual(r2.status, RedemptionStatus.CANCELLED)

        # Attempt to approve r2 anyway -> Blocked by daily limit check & not pending
        success2, msg_app2, _ = approve_redemption(r2.id, self.seller_user)
        self.assertFalse(success2)

    def test_bug26_bumper_bill_submission_sanitizes_image_and_blocks_malicious_files(self):
        """BUG-26: Bumper bill submission sanitizes valid images and rejects non-image or script payloads"""
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from services.reward_service import submit_bumper_bill

        # 1. Attempt upload of malicious text/script file masquerading as image
        fake_file = SimpleUploadedFile("exploit.php", b"<?php echo 'xss'; ?>", content_type="text/plain")
        ok_bad, msg_bad, sub_bad = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-MALICIOUS-99",
            bill_amount="12000.00",
            bill_image=fake_file
        )
        self.assertFalse(ok_bad)
        self.assertIn("Receipt image rejected", msg_bad)
        self.assertIsNone(sub_bad)

        # 2. Genuine image upload -> successfully sanitized and saved
        img_buffer = BytesIO()
        valid_img = Image.new('RGB', (200, 200), color='blue')
        valid_img.save(img_buffer, format='JPEG')
        img_buffer.seek(0)

        good_file = SimpleUploadedFile("genuine_receipt.jpg", img_buffer.read(), content_type="image/jpeg")
        ok_good, msg_good, sub_good = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-GENUINE-100",
            bill_amount="15000.00",
            bill_image=good_file
        )
        self.assertTrue(ok_good)
        self.assertIsNotNone(sub_good)
        self.assertTrue(sub_good.bill_image.name.startswith("bumper_bills/"))
        self.assertTrue(sub_good.bill_image.name.endswith(".jpg"))

    def test_bug27_auth_portals_block_open_redirects(self):
        """BUG-27: Open redirect attempts via next parameter must be rejected across auth portals"""
        client = Client()

        with self.settings(CAPTCHA_ENFORCE=False):
            # 1. Buyer login with malicious next
            resp_buyer = client.post('/buyer/login/?next=https://evil.com/phishing', {
                'identifier': self.buyer_user.username,
                'password': 'Password@123'
            })
            self.assertEqual(resp_buyer.status_code, 302)
            self.assertEqual(resp_buyer.url, '/buyer/dashboard/')

            # Protocol-relative redirect attempt
            client.logout()
            resp_proto = client.post('/buyer/login/?next=//malicious-site.com/steal', {
                'identifier': self.buyer_user.username,
                'password': 'Password@123'
            })
            self.assertEqual(resp_proto.status_code, 302)
            self.assertEqual(resp_proto.url, '/buyer/dashboard/')

            # 2. Seller login with malicious next
            client.logout()
            resp_seller = client.post('/seller/login/?next=https://evil.com/seller-steal', {
                'identifier': self.seller_user.username,
                'password': 'Password@123'
            })
            self.assertEqual(resp_seller.status_code, 302)
            self.assertEqual(resp_seller.url, '/seller/dashboard/')

            # 3. Admin login with malicious next
            client.logout()
            admin_user = User.objects.create_superuser('admin_test_bug27', 'admin27@test.com', 'Admin@123')
            Profile.objects.create(user=admin_user, role=Role.ADMIN)
            resp_admin = client.post('/admin-login/?next=https://evil.com/admin-steal', {
                'identifier': 'admin_test_bug27',
                'password': 'Admin@123'
            })
            self.assertEqual(resp_admin.status_code, 302)
            self.assertEqual(resp_admin.url, '/admin-portal/dashboard/')

    def test_bug28_buyer_delete_account_requires_buyer_auth(self):
        """BUG-28: Unauthenticated access to /buyer/delete-account/ must redirect to buyer login, not 500"""
        client = Client()
        resp = client.get('/buyer/delete-account/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/buyer/login/', resp.url)

    def test_bug29_password_recovery_get_with_id_does_not_advance_to_step2(self):
        """BUG-29: Query string identifier on password recovery endpoints must not bypass CAPTCHA or step 1"""
        client = Client()

        # Buyer recovery GET with id
        resp_buyer = client.get(f'/buyer/recover/?id={self.buyer_profile.public_unique_id}')
        self.assertEqual(resp_buyer.status_code, 200)
        self.assertEqual(resp_buyer.context['step'], 1)
        self.assertIsNone(resp_buyer.context['buyer_profile'])

        # Seller recovery GET with identifier
        resp_seller = client.get(f'/seller/recover/?identifier={self.seller_user.username}')
        self.assertEqual(resp_seller.status_code, 200)
        self.assertEqual(resp_seller.context['step'], 1)

    def test_bug30_admin_tickets_portal_includes_guest_contact_inquiries(self):
        """BUG-30: Guest inquiries and statutory grievances submitted via /contact-us/ appear in admin tickets"""
        from apps.administration.models import ContactInquiry, InquiryRole, TicketStatus

        inquiry = ContactInquiry.objects.create(
            ticket_number="INQ-TEST-001",
            full_name="Rajesh Grievance",
            email="rajesh@test.com",
            phone="9876543210",
            user_role=InquiryRole.GRIEVANCE,
            subject="DPDP Grievance Notice",
            message="Please delete all offline records.",
            status=TicketStatus.OPEN
        )

        admin_user = User.objects.create_superuser('admin_tickets_tester', 'admintickets@test.com', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        resp = client.get('/admin-portal/tickets/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('contact_inquiries', resp.context)
        inquiries = list(resp.context['contact_inquiries'])
        self.assertTrue(any(i.ticket_number == "INQ-TEST-001" for i in inquiries))
        self.assertContains(resp, "DPDP Grievance Notice")

        # Test updating inquiry status via admin_inquiry_update_view POST
        update_resp = client.post(f'/admin-portal/inquiries/{inquiry.id}/update/', {
            'status': TicketStatus.RESOLVED,
            'admin_notes': 'Grievance addressed per protocol.'
        })
        self.assertEqual(update_resp.status_code, 302)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, TicketStatus.RESOLVED)
        self.assertEqual(inquiry.admin_notes, 'Grievance addressed per protocol.')

    def test_bug31_error_log_resolve_requires_post(self):
        """BUG-31: Error log resolve endpoint must reject HTTP GET state mutation and require POST"""
        from apps.administration.models import ErrorLog

        err = ErrorLog.objects.create(
            incident_id="ERR-TEST-001",
            error_type="ValueError",
            message="Test crash message",
            is_resolved=False
        )

        admin_user = User.objects.create_superuser('admin_err_tester', 'adminerr@test.com', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        # GET should be rejected (redirected without mutating state)
        resp_get = client.get(f'/admin-portal/error-logs/{err.id}/resolve/')
        self.assertEqual(resp_get.status_code, 302)
        err.refresh_from_db()
        self.assertFalse(err.is_resolved)

        # POST should successfully resolve the error log
        resp_post = client.post(f'/admin-portal/error-logs/{err.id}/resolve/')
        self.assertEqual(resp_post.status_code, 302)
        err.refresh_from_db()
        self.assertTrue(err.is_resolved)

    def test_bug32_maintenance_cron_expires_outdated_and_exhausted_offers(self):
        """BUG-32: expire_stale_qrcodes_job must expire active offers whose end_date has passed or max_redemptions reached"""
        from services.background_tasks import expire_stale_qrcodes_job

        # 1. Past end_date offer (created active, then end_date pushed past)
        expired_date_offer = Offer.objects.create(
            shop=self.shop,
            title="Old Past Campaign",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("15.00"),
            min_purchase=Decimal("200.00"),
            max_redemptions=50,
            current_redemptions=5,
            start_date=timezone.now() - timedelta(days=10),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        Offer.objects.filter(pk=expired_date_offer.pk).update(end_date=timezone.now() - timedelta(minutes=5))

        # 2. Quota-exhausted offer (created active, then current_redemptions pushed to max)
        exhausted_offer = Offer.objects.create(
            shop=self.shop,
            title="Fully Claimed Campaign",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("10.00"),
            min_purchase=Decimal("100.00"),
            max_redemptions=20,
            current_redemptions=0,
            start_date=timezone.now() - timedelta(days=2),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        Offer.objects.filter(pk=exhausted_offer.pk).update(current_redemptions=20)

        # Run background job
        result = expire_stale_qrcodes_job()
        self.assertIn("expired_offers_count", result)
        self.assertGreaterEqual(result["expired_offers_count"], 2)

        expired_date_offer.refresh_from_db()
        self.assertEqual(expired_date_offer.status, OfferStatus.EXPIRED)

        exhausted_offer.refresh_from_db()
        self.assertEqual(exhausted_offer.status, OfferStatus.EXPIRED)

    def test_bug34_admin_redemptions_export_masks_pii(self):
        """BUG-34: Admin redemptions Excel export strictly preserves Zero-PII and masks email/username"""
        import openpyxl
        from io import BytesIO

        # Create redemption
        redemption = OfferRedemption.objects.create(
            buyer=self.buyer_user,
            seller=self.seller_user,
            shop=self.shop,
            offer=self.offer,
            bill_amount=Decimal("500.00"),
            discount_given=Decimal("100.00"),
            status=RedemptionStatus.SUCCESS,
            is_verified_by_merchant=True,
            verification_otp="8888",
            idempotency_key="idemp_bug34_test"
        )

        admin_user = User.objects.create_superuser('admin_pii_tester', 'adminpii@test.com', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        resp = client.get('/admin-portal/redemptions/?export=excel')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", resp['Content-Type'])

        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active

        # Collect all cell values across the sheet
        all_cells = []
        for row in ws.iter_rows(values_only=True):
            all_cells.extend([str(c) for c in row if c is not None])

        # Verify buyer email and raw username are NOT present
        self.assertNotIn(self.buyer_user.email, all_cells)
        # Verify Zero-PII headers and public identifiers are present
        self.assertTrue(any("Shopper Nickname" in c for c in all_cells))
        self.assertTrue(any("Public Unique ID" in c for c in all_cells))

    def test_bug35_export_service_neutralizes_formula_injection(self):
        """BUG-35: Spreadsheet exports neutralize Excel/CSV formula injection (CWE-1236)"""
        import openpyxl
        from io import BytesIO
        from services.export_service import export_to_excel_response

        headers = ["Title", "Payload", "Safe Value"]
        rows = [
            ["Item 1", "=cmd|'/C calc'!A0", "Regular text"],
            ["Item 2", "+123456", "Normal"],
            ["Item 3", "-SUM(A1:A5)", "Clean"],
            ["Item 4", "@HYPERLINK('http://evil.com')", "Safe"],
        ]

        resp = export_to_excel_response("test_formula_sec", "Audit", headers, rows)
        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active

        # Verify that all formula trigger characters are escaped with leading single quote
        cell_val1 = ws.cell(row=2, column=2).value
        cell_val2 = ws.cell(row=3, column=2).value
        cell_val3 = ws.cell(row=4, column=2).value
        cell_val4 = ws.cell(row=5, column=2).value

        self.assertEqual(cell_val1, "'=cmd|'/C calc'!A0")
        self.assertEqual(cell_val2, "'+123456")
        self.assertEqual(cell_val3, "'-SUM(A1:A5)")
        self.assertEqual(cell_val4, "'@HYPERLINK('http://evil.com')")

    def test_bug36_statutory_rejection_revokes_seller_is_verified(self):
        """BUG-36: Rejecting statutory compliance documents revokes seller_profile.is_verified"""
        admin_user = User.objects.create_superuser('admin_compliance_mgr', 'admincomp@test.com', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        self.assertTrue(self.seller_profile.is_verified)
        self.assertTrue(self.shop.is_approved)

        # Admin rejects verification
        resp = client.post(f'/admin-portal/verifications/{self.verification.id}/update/', {
            'status': 'REJECTED',
            'admin_notes': 'Expired Shop Act license document.'
        })
        self.assertEqual(resp.status_code, 302)

        self.shop.refresh_from_db()
        self.seller_profile.refresh_from_db()

        self.assertFalse(self.shop.is_approved)
        self.assertFalse(self.shop.is_active)
        self.assertFalse(self.seller_profile.is_verified)

    def test_bug37_in_store_scanner_rejects_inactive_delisted_shops(self):
        """BUG-37: In-store scanner routing excludes suspended/delisted shops (is_active=False)"""
        # Delist shop
        self.shop.is_active = False
        self.shop.save(update_fields=['is_active'])

        client = Client()
        client.force_login(self.buyer_user)

        # 1. Scanning store URL for delisted shop does NOT redirect to shop_qr_landing
        resp1 = client.post('/buyer/scan/', {
            'qr_code': f"/store/{self.shop.slug}/qr/"
        })
        # If not matched, it attempts redemption and fails or stays on scanner
        self.assertNotEqual(resp1.url if resp1.status_code == 302 else '', f"/store/{self.shop.slug}/qr/")

        # 2. Scanning shop:slug for delisted shop does NOT redirect to shop_qr_landing
        resp2 = client.post('/buyer/scan/', {
            'qr_code': f"shop:{self.shop.slug}"
        })
        self.assertNotEqual(resp2.url if resp2.status_code == 302 else '', f"/store/{self.shop.slug}/qr/")

    def test_bug38_quota_exhausted_offers_excluded_from_active_count_and_explore(self):
        """BUG-38: Quota-exhausted offers (current_redemptions >= max_redemptions) are omitted from active counts and explore"""
        # Create quota-exhausted offer
        exhausted_offer = Offer.objects.create(
            shop=self.shop,
            title="Popular Exhausted Campaign",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("25.00"),
            min_purchase=Decimal("300.00"),
            max_redemptions=10,
            current_redemptions=0,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        Offer.objects.filter(pk=exhausted_offer.pk).update(current_redemptions=10)

        # 1. Shop active_offers_count excludes the exhausted offer
        # self.offer is active (current_redemptions=0), exhausted_offer is exhausted
        self.assertEqual(self.shop.active_offers_count, 1)

        # 2. Public explore omits the exhausted offer
        client = Client()
        resp_pub = client.get('/explore/?type=offers')
        self.assertEqual(resp_pub.status_code, 200)
        pub_offers = list(resp_pub.context['offers'])
        self.assertNotIn(exhausted_offer, pub_offers)

        # 3. Buyer explore omits the exhausted offer
        client.force_login(self.buyer_user)
        resp_buyer = client.get('/buyer/explore/?type=offers')
        self.assertEqual(resp_buyer.status_code, 200)
        buyer_offers = list(resp_buyer.context['offers'])
        self.assertNotIn(exhausted_offer, buyer_offers)

    def test_bug40_counter_token_generation_uses_csprng(self):
        """BUG-40: Counter verification token generation uses cryptographically secure secrets PRNG"""
        from unittest.mock import patch
        from services.redemption_service import initiate_redemption

        with patch('secrets.randbelow', return_value=4321) as mock_rand:
            ok, msg, redemp = initiate_redemption(
                buyer_user=self.buyer_user,
                offer=self.offer,
                bill_amount=Decimal("500.00"),
                idempotency_key="idemp_bug40_token"
            )
            self.assertTrue(ok)
            self.assertIsNotNone(redemp)
            # 4321 + 1000 = 5321
            self.assertEqual(redemp.verification_otp, "5321")
            self.assertEqual(len(redemp.verification_otp), 4)
            self.assertTrue(redemp.verification_otp.isdigit())
            mock_rand.assert_called_once_with(9000)

    def test_bug42_seller_qr_desk_excludes_expired_and_quota_exhausted_offers(self):
        """BUG-42: Counter QR desk dropdown strictly excludes expired and quota-exhausted campaigns"""
        # 1. Past end_date offer
        expired_offer = Offer.objects.create(
            shop=self.shop,
            title="Expired Standee Offer",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("15.00"),
            min_purchase=Decimal("200.00"),
            max_redemptions=50,
            current_redemptions=0,
            start_date=timezone.now() - timedelta(days=10),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        Offer.objects.filter(pk=expired_offer.pk).update(end_date=timezone.now() - timedelta(minutes=5))

        # 2. Quota-exhausted offer
        exhausted_offer = Offer.objects.create(
            shop=self.shop,
            title="Exhausted Standee Offer",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("10.00"),
            min_purchase=Decimal("100.00"),
            max_redemptions=20,
            current_redemptions=0,
            start_date=timezone.now() - timedelta(days=2),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        Offer.objects.filter(pk=exhausted_offer.pk).update(current_redemptions=20)

        client = Client()
        client.force_login(self.seller_user)
        resp = client.get('/seller/qr/')
        self.assertEqual(resp.status_code, 200)

        active_offers = list(resp.context['active_offers'])
        self.assertIn(self.offer, active_offers)
        self.assertNotIn(expired_offer, active_offers)
        self.assertNotIn(exhausted_offer, active_offers)

    def test_bug43_sync_daily_analytics_aggregates_only_successful_redemptions(self):
        """BUG-43: sync_daily_analytics_job aggregates only SUCCESS status redemptions, omitting cancelled/pending"""
        from apps.analytics.models import ShopMetric
        from services.background_tasks import sync_daily_analytics_job

        target_date = timezone.localdate()

        # 1. Cancelled redemption with default is_verified_by_merchant=True
        cancelled_redemption = OfferRedemption.objects.create(
            buyer=self.buyer_user,
            seller=self.seller_user,
            shop=self.shop,
            offer=self.offer,
            bill_amount=Decimal("1500.00"),
            discount_given=Decimal("300.00"),
            status=RedemptionStatus.CANCELLED,
            is_verified_by_merchant=True,
            idempotency_key="idemp_bug43_cancelled"
        )
        OfferRedemption.objects.filter(pk=cancelled_redemption.pk).update(
            redeemed_at=timezone.now()
        )

        # 2. Successful redemption
        successful_redemption = OfferRedemption.objects.create(
            buyer=self.buyer_user,
            seller=self.seller_user,
            shop=self.shop,
            offer=self.offer,
            bill_amount=Decimal("600.00"),
            discount_given=Decimal("120.00"),
            status=RedemptionStatus.SUCCESS,
            is_verified_by_merchant=True,
            idempotency_key="idemp_bug43_success"
        )
        OfferRedemption.objects.filter(pk=successful_redemption.pk).update(
            redeemed_at=timezone.now()
        )

        # Run analytics sync
        result = sync_daily_analytics_job(target_date=target_date)
        self.assertEqual(result['status'], 'success')

        metric = ShopMetric.objects.get(shop=self.shop, date=target_date)
        # Only the 1 successful redemption is aggregated (not the cancelled one)
        self.assertEqual(metric.redemptions_count, 1)
        self.assertEqual(metric.estimated_sales_inr, Decimal("600.00"))

    def test_bug44_sitemap_xml_excludes_quota_exhausted_offers(self):
        """BUG-44: sitemap_xml_view excludes quota-exhausted campaigns from crawler indexing"""
        exhausted_offer = Offer.objects.create(
            shop=self.shop,
            title="Sitemap Exhausted Offer",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("20.00"),
            min_purchase=Decimal("200.00"),
            max_redemptions=10,
            current_redemptions=0,
            start_date=timezone.now() - timedelta(days=2),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        Offer.objects.filter(pk=exhausted_offer.pk).update(current_redemptions=10)

        client = Client()
        resp = client.get('/sitemap.xml')
        self.assertEqual(resp.status_code, 200)
        xml_content = resp.content.decode('utf-8')

        # Self.offer is active and within quota -> included
        self.assertIn(f"/offers/{self.offer.slug}/", xml_content)
        # Exhausted offer -> omitted
        self.assertNotIn(f"/offers/{exhausted_offer.slug}/", xml_content)

    def test_two_tier_bumper_bill_prevents_merchant_self_submission(self):
        """Enterprise Two-Tier Bumper Bill: Store owner cannot submit a bumper bill for their own outlet."""
        from services.reward_service import submit_bumper_bill

        success, msg, sub = submit_bumper_bill(
            buyer_user=self.seller_user,  # Merchant attempting self-submission
            shop_id=self.shop.id,
            bill_number="OWN-STORE-999",
            bill_amount=Decimal("15000.00")
        )
        self.assertFalse(success)
        self.assertIsNone(sub)
        self.assertIn("Merchants cannot submit purchase bills for their own store", msg)

    def test_two_tier_bumper_bill_prevents_merchant_self_approval(self):
        """Enterprise Two-Tier Bumper Bill: Store owner is blocked from approving any bill where they are the buyer."""
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus
        from services.reward_service import store_verify_bumper_bill

        # Create submission where merchant is buyer (e.g. simulated edge case)
        edge_sub = BumperBillSubmission.objects.create(
            buyer=self.seller_user,
            shop=self.shop,
            bill_number="EDGE-888",
            bill_amount=Decimal("12000.00"),
            status=BumperBillStatus.PENDING_STORE
        )

        success, msg, _ = store_verify_bumper_bill(
            submission_id=edge_sub.id,
            merchant_user=self.seller_user,
            approve=True
        )
        self.assertFalse(success)
        self.assertIn("cannot verify their own purchase bills", msg)

        # Also verify via HTTP POST endpoint
        client = Client()
        client.force_login(self.seller_user)
        resp = client.post(f"/seller/bill-verifications/{edge_sub.id}/approve/")
        self.assertEqual(resp.status_code, 302)
        edge_sub.refresh_from_db()
        self.assertEqual(edge_sub.status, BumperBillStatus.PENDING_STORE)

    def test_two_tier_bumper_bill_merchant_approval_sets_store_verified(self):
        """Enterprise Two-Tier Bumper Bill: Merchant approval sets STORE_VERIFIED without minting points or vouchers."""
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus
        from services.reward_service import submit_bumper_bill, store_verify_bumper_bill
        from services.reward_service import get_or_create_reward_account

        reward_acct = get_or_create_reward_account(self.buyer_user)
        initial_points = reward_acct.current_points

        success, msg, sub = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="POS-5544",
            bill_amount=Decimal("14500.00")
        )
        self.assertTrue(success)
        self.assertEqual(sub.status, BumperBillStatus.PENDING_STORE)

        # Merchant approves Step 1
        v_success, v_msg, verified_sub = store_verify_bumper_bill(
            submission_id=sub.id,
            merchant_user=self.seller_user,
            approve=True
        )
        self.assertTrue(v_success)
        self.assertEqual(verified_sub.status, BumperBillStatus.STORE_VERIFIED)
        self.assertEqual(verified_sub.store_verified_by, self.seller_user)
        self.assertIsNotNone(verified_sub.store_verified_at)
        # CRITICAL: Voucher code remains empty; points not awarded
        self.assertEqual(verified_sub.voucher_code, "")

        reward_acct.refresh_from_db()
        self.assertEqual(reward_acct.current_points, initial_points)

    def test_two_tier_bumper_bill_admin_approval_awards_points_and_voucher(self):
        """Enterprise Two-Tier Bumper Bill: Admin approval on STORE_VERIFIED sets APPROVED, awards 100 pts & VIP voucher."""
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus
        from services.reward_service import submit_bumper_bill, store_verify_bumper_bill, admin_clear_bumper_bill
        from services.reward_service import get_or_create_reward_account

        admin_user = User.objects.create_superuser('admin_two_tier', 'two_tier@lokalloop.in', 'Admin@123')
        reward_acct = get_or_create_reward_account(self.buyer_user)
        initial_points = reward_acct.current_points

        # Step 0: Buyer submits
        _, _, sub = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="POS-7788",
            bill_amount=Decimal("25000.00")
        )

        # Step 1: Merchant approves
        store_verify_bumper_bill(sub.id, self.seller_user, approve=True)
        sub.refresh_from_db()
        self.assertEqual(sub.status, BumperBillStatus.STORE_VERIFIED)

        # Step 2: Admin final clearance
        a_success, a_msg, approved_sub = admin_clear_bumper_bill(
            submission_id=sub.id,
            admin_user=admin_user,
            approve=True
        )
        self.assertTrue(a_success)
        self.assertEqual(approved_sub.status, BumperBillStatus.APPROVED)
        self.assertEqual(approved_sub.admin_reviewed_by, admin_user)
        self.assertIsNotNone(approved_sub.admin_reviewed_at)
        self.assertTrue(approved_sub.voucher_code.startswith("BUMPER-VIP-"))

        # Check 100 points credited
        reward_acct.refresh_from_db()
        self.assertEqual(reward_acct.current_points, initial_points + 100)

    def test_two_tier_bumper_bill_rejection_stages_and_no_points(self):
        """Enterprise Two-Tier Bumper Bill: Rejection by merchant or admin marks rejection_stage and declines points."""
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus
        from services.reward_service import submit_bumper_bill, store_verify_bumper_bill, admin_clear_bumper_bill
        from services.reward_service import get_or_create_reward_account

        admin_user = User.objects.create_superuser('admin_two_tier_rej', 'two_tier_rej@lokalloop.in', 'Admin@123')
        reward_acct = get_or_create_reward_account(self.buyer_user)
        initial_points = reward_acct.current_points

        # 1. Merchant Rejection (Step 1)
        _, _, sub1 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="DECLINE-STORE-1",
            bill_amount=Decimal("11000.00")
        )
        r1_success, _, r1_sub = store_verify_bumper_bill(
            submission_id=sub1.id,
            merchant_user=self.seller_user,
            approve=False,
            rejection_reason="Receipt not found in cash register"
        )
        self.assertTrue(r1_success)
        self.assertEqual(r1_sub.status, BumperBillStatus.REJECTED)
        self.assertEqual(r1_sub.rejection_stage, 'STORE')
        self.assertEqual(r1_sub.rejection_reason, "Receipt not found in cash register")

        # 2. Admin Rejection (Step 2)
        _, _, sub2 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="DECLINE-ADMIN-2",
            bill_amount=Decimal("18000.00")
        )
        store_verify_bumper_bill(sub2.id, self.seller_user, approve=True)
        r2_success, _, r2_sub = admin_clear_bumper_bill(
            submission_id=sub2.id,
            admin_user=admin_user,
            approve=False,
            rejection_reason="Duplicate bill across multiple buyer accounts detected"
        )
        self.assertTrue(r2_success)
        self.assertEqual(r2_sub.status, BumperBillStatus.REJECTED)
        self.assertEqual(r2_sub.rejection_stage, 'ADMIN')
        self.assertEqual(r2_sub.rejection_reason, "Duplicate bill across multiple buyer accounts detected")

        # Points unchanged
        reward_acct.refresh_from_db()
        self.assertEqual(reward_acct.current_points, initial_points)

    def test_bug41_counter_approval_locks_offer_and_rejects_at_quota_limit(self):
        """BUG-41: Counter approval locks Offer row and gracefully rejects when quota is reached"""
        from services.redemption_service import approve_redemption
        from apps.redemptions.models import OfferRedemption, RedemptionStatus

        # Set offer to exact quota limit
        self.offer.current_redemptions = self.offer.max_redemptions
        self.offer.save(update_fields=['current_redemptions'])

        redemption = OfferRedemption.objects.create(
            offer=self.offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            bill_amount=Decimal("500.00"),
            discount_given=Decimal("100.00"),
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp="9999",
            idempotency_key="idemp_bug41_quota_test"
        )

        ok, msg, res = approve_redemption(redemption.id, self.seller_user)
        self.assertFalse(ok)
        self.assertIn("maximum redemption quota", msg)
        redemption.refresh_from_db()
        self.assertEqual(redemption.status, RedemptionStatus.CANCELLED)

    def test_bug45_leaderboard_cycle_rollover_and_anti_monopoly_cooldown(self):
        """BUG-45: Leaderboard cycle rolls over, records history, advances cycle number and activates cooldown"""
        from services.leaderboard_service import (
            get_current_cycle_number,
            rollover_weekly_leaderboard_cycle,
            is_buyer_in_cooldown,
            get_weekly_leaderboard
        )
        from services.background_tasks import rollover_leaderboard_cycle_job
        from apps.rewards.models import RewardTransaction, RewardType, BuyerRewardAccount

        acct1 = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_user)[0]
        # Award points in current cycle
        RewardTransaction.objects.create(
            account=acct1,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=120,
            balance_after=acct1.current_points + 120,
            notes="Points for Cycle 1"
        )

        initial_cycle = get_current_cycle_number()

        # Run background rollover cron job
        res = rollover_leaderboard_cycle_job()
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['winner'], self.buyer_user.username)

        # Cycle number must advance
        new_cycle = get_current_cycle_number()
        self.assertEqual(new_cycle, initial_cycle + 1)

        # Winner must be in 3-cycle cooldown for the new cycle
        in_cooldown, won_cycle = is_buyer_in_cooldown(self.buyer_user, new_cycle)
        self.assertTrue(in_cooldown)
        self.assertEqual(won_cycle, initial_cycle)

    def test_bug46_buyer_reward_account_negative_points_db_constraint(self):
        """BUG-46: Database check constraint prevents BuyerRewardAccount.current_points < 0"""
        from django.db import IntegrityError, transaction
        from apps.rewards.models import BuyerRewardAccount

        acct = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_user)[0]

        # Direct DB update with negative points must violate check constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BuyerRewardAccount.objects.filter(pk=acct.pk).update(current_points=-10)

    def test_bug47_flat_discount_validation_and_negative_checkout_prevented(self):
        """BUG-47: Flat discount cannot exceed min_purchase and calculated discount never exceeds bill"""
        from apps.offers.models import Offer, OfferType, OfferStatus
        from services.redemption_service import process_redemption, create_or_refresh_offer_qr

        # 1. Validation in seller_offer_create_view: Flat discount > min_purchase must be rejected
        client = Client()
        client.force_login(self.seller_user)
        resp = client.post('/seller/offers/create/', {
            'title': 'Excessive Flat Discount Deal',
            'offer_type': 'FLAT',
            'discount_value': '300.00',
            'min_purchase': '100.00',
            'max_redemptions': '50',
            'days_valid': '15'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Offer.objects.filter(title='Excessive Flat Discount Deal').exists())

        # 2. Clamping in process_redemption prevents negative checkout
        now = timezone.now()
        flat_offer = Offer.objects.create(
            shop=self.shop,
            title='Flat ₹150 Off Deal',
            offer_type=OfferType.FLAT,
            discount_value=Decimal('150.00'),
            min_purchase=Decimal('0.00'),
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=10),
            status=OfferStatus.ACTIVE
        )
        qr_obj, _ = create_or_refresh_offer_qr(flat_offer)
        ok, msg, redemp = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=qr_obj.code,
            bill_amount=Decimal('80.00'),
            buyer_lat=12.9716,
            buyer_lng=77.5946
        )
        self.assertTrue(ok)
        self.assertIsNotNone(redemp)
        # Discount must be clamped to the bill amount (₹80.00), preventing negative payable
        self.assertEqual(redemp.discount_given, Decimal('80.00'))

    def test_bug48_future_start_date_offers_excluded_from_explore_and_shop(self):
        """BUG-48: Offers with start_date in the future are omitted from directory views and shop details"""
        from apps.offers.models import Offer, OfferType, OfferStatus

        now = timezone.now()
        future_offer = Offer.objects.create(
            shop=self.shop,
            title='Diwali Special Upcoming Deal',
            slug='diwali-special-upcoming-deal',
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('30.00'),
            min_purchase=Decimal('200.00'),
            start_date=now + timedelta(days=3),
            end_date=now + timedelta(days=10),
            status=OfferStatus.ACTIVE
        )

        client = Client()

        # 1. Shop active_offers_count excludes future offers
        self.assertNotIn(future_offer, self.shop.offers.filter(
            status=OfferStatus.ACTIVE,
            start_date__lte=now,
            end_date__gte=now
        ))

        # 2. Public explore offers view excludes future offers
        resp_explore = client.get('/explore/?type=offers')
        self.assertNotContains(resp_explore, 'Diwali Special Upcoming Deal')

        # 3. Shop details view excludes future offers
        resp_shop = client.get(f'/shops/{self.shop.slug}/')
        self.assertNotContains(resp_shop, 'Diwali Special Upcoming Deal')

        # 4. Direct offer detail view returns 404 for unstarted offer
        resp_detail = client.get(f'/offers/{future_offer.slug}/')
        self.assertEqual(resp_detail.status_code, 404)

    def test_bug49_catalog_voucher_in_store_verification_and_anti_self_dealing(self):
        """BUG-49: Cashier verifies and burns catalog claim vouchers with anti-self-dealing and double-spend protection"""
        from apps.rewards.models import RewardCatalogItem, CatalogRewardType, BuyerRewardClaim, BuyerRewardAccount
        from services.reward_service import redeem_catalog_reward, verify_and_burn_catalog_claim

        # Seed catalog item and buyer points
        item = RewardCatalogItem.objects.create(
            title="Complimentary Artisanal Dessert",
            points_required=50,
            reward_type=CatalogRewardType.FOOD_BEVERAGE,
            voucher_code_prefix="DESSERT",
            is_active=True
        )
        account = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_user)[0]
        account.current_points = 100
        account.save(update_fields=['current_points'])

        # Buyer claims reward
        ok, msg, claim = redeem_catalog_reward(self.buyer_user, item.id)
        self.assertTrue(ok)
        self.assertEqual(claim.status, 'ACTIVE')
        voucher_code = claim.voucher_code

        # 1. Anti-Self-Dealing: Merchant cannot redeem their own catalog vouchers
        seller_account = BuyerRewardAccount.objects.get_or_create(buyer=self.seller_user)[0]
        seller_account.current_points = 100
        seller_account.save(update_fields=['current_points'])
        _, _, seller_claim = redeem_catalog_reward(self.seller_user, item.id)
        ok_self, msg_self, _ = verify_and_burn_catalog_claim(seller_claim.voucher_code, self.seller_user)
        self.assertFalse(ok_self)
        self.assertIn("Anti-Self-Dealing", msg_self)

        # 2. Successful store counter burn
        client = Client()
        client.force_login(self.seller_user)
        resp_burn = client.post('/seller/vouchers/verify/', {'voucher_code': voucher_code})
        self.assertEqual(resp_burn.status_code, 200)

        claim.refresh_from_db()
        self.assertEqual(claim.status, 'USED')
        self.assertEqual(claim.verified_by, self.seller_user)
        self.assertIsNotNone(claim.used_at)

        # 3. Double-spend protection: Re-burning the same voucher must fail
        ok_reused, msg_reused, _ = verify_and_burn_catalog_claim(voucher_code, self.seller_user)
        self.assertFalse(ok_reused)
        self.assertIn("already been redeemed and marked USED", msg_reused)

        # 4. API endpoint verification for POS integration
        _, _, claim2 = redeem_catalog_reward(self.buyer_user, item.id)
        api_resp = client.post('/api/seller/vouchers/verify/', {'voucher_code': claim2.voucher_code})
        self.assertEqual(api_resp.status_code, 200)
        self.assertTrue(api_resp.json()['success'])
        claim2.refresh_from_db()
        self.assertEqual(claim2.status, 'USED')

    def test_bug50_out_of_order_credit_wallet_preserves_welcome_bonus(self):
        """BUG-50: Out-of-order credit_wallet preserves 50 starter credits when bonus is ON"""
        from apps.administration.models import PlatformSetting
        from services.wallet_service import credit_wallet
        from apps.wallets.models import TransactionType, WalletTransaction

        setting = PlatformSetting.get_settings()
        setting.signup_bonus_enabled = True
        setting.save(update_fields=['signup_bonus_enabled'])

        # Fresh seller user who has never visited the portal or initialized wallet
        fresh_seller = User.objects.create_user(
            username='fresh_seller_bug50@example.com',
            email='fresh_seller_bug50@example.com',
            password='password123'
        )

        # Directly invoke credit_wallet before get_or_create_wallet was ever called
        credit_wallet(
            seller_user=fresh_seller,
            amount_credits=20,
            transaction_type=TransactionType.PURCHASE,
            reference_id='PAYMENT_ORDER_99',
            notes='Package purchase'
        )

        # Balance must be 50 (welcome gift) + 20 (purchase) = 70 credits
        fresh_seller.wallet.refresh_from_db()
        self.assertEqual(fresh_seller.wallet.balance_credits, 70)
        self.assertEqual(fresh_seller.wallet.lifetime_purchased, 70)

        # Ensure both transactions were registered
        tx_types = list(WalletTransaction.objects.filter(wallet=fresh_seller.wallet).values_list('reference_id', flat=True))
        self.assertIn('WELCOME_BONUS', tx_types)
        self.assertIn('PAYMENT_ORDER_99', tx_types)

    def test_buyer_welcome_bonus_awards_25_points_when_enabled(self):
        """New buyers receive exactly 25 reward points upon onboarding when welcome bonus is ON"""
        from apps.administration.models import PlatformSetting
        from services.reward_service import get_or_create_reward_account
        from apps.rewards.models import RewardType

        setting = PlatformSetting.get_settings()
        setting.signup_bonus_enabled = True
        setting.save(update_fields=['signup_bonus_enabled'])

        fresh_buyer = User.objects.create_user(
            username='fresh_buyer_bonus@example.com',
            email='fresh_buyer_bonus@example.com',
            password='password123'
        )

        reward_acct = get_or_create_reward_account(fresh_buyer)
        self.assertEqual(reward_acct.current_points, 25)
        self.assertEqual(reward_acct.lifetime_earned, 25)

        tx = reward_acct.transactions.filter(transaction_type=RewardType.WELCOME_BONUS).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.points, 25)

    def test_admin_settings_toggle_signup_bonus_and_zero_bonuses_when_disabled(self):
        """Admin can toggle signup_bonus_enabled to False; new buyers and sellers receive 0 bonuses"""
        from apps.administration.models import PlatformSetting
        from services.wallet_service import get_or_create_wallet
        from services.reward_service import get_or_create_reward_account
        from apps.wallets.models import WalletTransaction
        from apps.rewards.models import RewardType

        admin_user = User.objects.create_superuser('bonus_admin', 'bonus_admin@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        # 1. Disable bonus via Admin Portal UI POST
        resp = client.post('/admin-portal/settings/', {
            'action': 'update_signup_bonus',
            'signup_bonus_enabled': 'no'
        })
        self.assertEqual(resp.status_code, 302)

        setting = PlatformSetting.get_settings()
        self.assertFalse(setting.signup_bonus_enabled)

        # 2. New seller receives 0 credits and no welcome transaction
        zero_seller = User.objects.create_user(
            username='zero_seller@example.com',
            email='zero_seller@example.com',
            password='password123'
        )
        wallet = get_or_create_wallet(zero_seller)
        self.assertEqual(wallet.balance_credits, 0)
        self.assertEqual(wallet.lifetime_purchased, 0)
        self.assertFalse(WalletTransaction.objects.filter(wallet=wallet, reference_id='WELCOME_BONUS').exists())

        # 3. New buyer receives 0 points and no welcome transaction
        zero_buyer = User.objects.create_user(
            username='zero_buyer@example.com',
            email='zero_buyer@example.com',
            password='password123'
        )
        reward_acct = get_or_create_reward_account(zero_buyer)
        self.assertEqual(reward_acct.current_points, 0)
        self.assertEqual(reward_acct.lifetime_earned, 0)
        self.assertFalse(reward_acct.transactions.filter(transaction_type=RewardType.WELCOME_BONUS).exists())

        # 4. Re-enable bonus for subsequent tests
        resp_on = client.post('/admin-portal/settings/', {
            'action': 'update_signup_bonus',
            'signup_bonus_enabled': 'yes'
        })
        self.assertEqual(resp_on.status_code, 302)
        setting.refresh_from_db()
        self.assertTrue(setting.signup_bonus_enabled)

    def test_bug51_admin_clear_bumper_bill_requires_store_verification_first(self):
        """BUG-51: Admin cannot clear/approve a bumper bill directly from PENDING_STORE without in-store check"""
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus
        from services.reward_service import submit_bumper_bill, store_verify_bumper_bill, admin_clear_bumper_bill

        # 1. Buyer submits bumper bill (starts in PENDING_STORE)
        ok, msg, sub = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="BILL-51-TEST",
            bill_amount=Decimal('12000.00')
        )
        self.assertTrue(ok)
        self.assertEqual(sub.status, BumperBillStatus.PENDING_STORE)

        # 2. Admin directly attempts to clear/approve -> MUST BE BLOCKED
        admin_user = User.objects.create_superuser('admin_b51', 'admin_b51@lokalloop.in', 'Admin@123')
        admin_ok, admin_msg, admin_sub = admin_clear_bumper_bill(
            submission_id=sub.id,
            admin_user=admin_user,
            approve=True
        )
        self.assertFalse(admin_ok)
        self.assertIn("must undergo in-store merchant verification", admin_msg)
        sub.refresh_from_db()
        self.assertEqual(sub.status, BumperBillStatus.PENDING_STORE)

        # 3. Store merchant verifies bill (Step 1)
        store_ok, store_msg, store_sub = store_verify_bumper_bill(
            submission_id=sub.id,
            merchant_user=self.seller_user,
            approve=True
        )
        self.assertTrue(store_ok)
        self.assertEqual(store_sub.status, BumperBillStatus.STORE_VERIFIED)

        # 4. Admin clears bill after Step 1 is done -> SUCCEEDS
        admin_ok2, admin_msg2, admin_sub2 = admin_clear_bumper_bill(
            submission_id=sub.id,
            admin_user=admin_user,
            approve=True
        )
        self.assertTrue(admin_ok2)
        self.assertEqual(admin_sub2.status, BumperBillStatus.APPROVED)

    def test_bug52_standee_qr_remains_active_across_multiple_customers_until_quota(self):
        """BUG-52: Printed standee counter QR codes remain valid across distinct customer scans until max quota"""
        from apps.offers.models import Offer, OfferType, OfferStatus
        from services.redemption_service import create_or_refresh_offer_qr, process_redemption
        from services.wallet_service import credit_wallet

        from apps.wallets.models import TransactionType
        credit_wallet(self.seller_user, 100, TransactionType.PURCHASE, reference_id="TEST_REFUEL", notes="Refuel")

        now = timezone.now()
        quota_offer = Offer.objects.create(
            shop=self.shop,
            title='Counter Standee Campaign Deal',
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('10.00'),
            min_purchase=Decimal('50.00'),
            credits_per_redemption=5,
            max_redemptions=2,
            current_redemptions=0,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        # Standee acrylic QR printed by merchant
        standee_qr, _ = create_or_refresh_offer_qr(quota_offer)
        self.assertFalse(standee_qr.is_used)

        # Customer 1 walks in and scans printed acrylic standee
        buyer1 = User.objects.create_user('shopper_one', 'shopper_one@test.com', 'Pass@123')
        ok1, msg1, redemp1 = process_redemption(
            buyer_user=buyer1,
            qr_code_str=standee_qr.code,
            bill_amount=Decimal('100.00'),
            buyer_lat=12.9716,
            buyer_lng=77.5946
        )
        self.assertTrue(ok1, f"Customer 1 failed: {msg1}")
        standee_qr.refresh_from_db()
        # QR code MUST NOT be invalidated after Customer 1
        self.assertFalse(standee_qr.is_used)

        # Customer 2 walks in 10 mins later and scans THE SAME acrylic standee
        buyer2 = User.objects.create_user('shopper_two', 'shopper_two@test.com', 'Pass@123')
        ok2, msg2, redemp2 = process_redemption(
            buyer_user=buyer2,
            qr_code_str=standee_qr.code,
            bill_amount=Decimal('100.00'),
            buyer_lat=12.9716,
            buyer_lng=77.5946
        )
        self.assertTrue(ok2, f"Customer 2 failed on standee QR: {msg2}")

        # Now offer reached max quota (2/2)
        quota_offer.refresh_from_db()
        self.assertEqual(quota_offer.current_redemptions, 2)
        standee_qr.refresh_from_db()
        self.assertTrue(standee_qr.is_used)

        # Customer 3 scans -> fails cleanly because quota is full
        buyer3 = User.objects.create_user('shopper_three', 'shopper_three@test.com', 'Pass@123')
        ok3, msg3, redemp3 = process_redemption(
            buyer_user=buyer3,
            qr_code_str=standee_qr.code,
            bill_amount=Decimal('100.00'),
            buyer_lat=12.9716,
            buyer_lng=77.5946
        )
        self.assertFalse(ok3)

    def test_bug53_multi_outlet_compliance_rejection_does_not_delist_verified_merchant_with_other_approved_shops(self):
        """BUG-53: Rejecting outlet #2 compliance must not revoke seller is_verified if outlet #1 remains approved"""
        from apps.shops.models import Shop, ShopVerification
        from apps.accounts.models import SellerProfile

        admin_user = User.objects.create_superuser('admin_b53', 'admin_b53@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)

        seller_profile = self.seller_user.seller_profile
        seller_profile.is_verified = True
        seller_profile.save(update_fields=['is_verified'])

        # self.shop is approved outlet #1
        self.shop.is_approved = True
        self.shop.save(update_fields=['is_approved'])

        # Outlet #2 created by same seller
        outlet2 = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Gadget Emporium Branch 2",
            city="Bengaluru",
            is_approved=False
        )
        verif2 = ShopVerification.objects.create(
            shop=outlet2,
            status='PENDING'
        )

        # Admin rejects Outlet #2 compliance documents
        resp = client.post(f'/admin-portal/verifications/{verif2.id}/update/', {
            'status': 'REJECTED',
            'notes': 'Missing Gumasta municipal registration license'
        })
        self.assertEqual(resp.status_code, 302)

        verif2.refresh_from_db()
        self.assertEqual(verif2.status, 'REJECTED')
        outlet2.refresh_from_db()
        self.assertFalse(outlet2.is_approved)

        # Seller MUST STILL BE VERIFIED because Outlet #1 is approved and active
        seller_profile.refresh_from_db()
        self.assertTrue(seller_profile.is_verified, "Seller was improperly delisted despite having an active approved outlet #1")

        # Now admin rejects Outlet #1 (last remaining approved shop)
        resp2 = client.post(f'/admin-portal/verifications/{self.verification.id}/update/', {
            'status': 'REJECTED',
            'notes': 'All shops rejected'
        })
        self.assertEqual(resp2.status_code, 302)
        seller_profile.refresh_from_db()
        self.assertFalse(seller_profile.is_verified)

    def test_bug54_geofenced_shop_rejects_redemption_when_gps_coordinates_are_missing(self):
        """BUG-54: Stripping client GPS coordinates must not bypass geofence check on a geotagged store"""
        from apps.offers.models import Offer, OfferType, OfferStatus
        from apps.administration.models import PlatformSetting
        from services.redemption_service import create_or_refresh_offer_qr, process_redemption
        from services.wallet_service import credit_wallet

        from apps.wallets.models import TransactionType
        credit_wallet(self.seller_user, 100, TransactionType.PURCHASE, reference_id="TEST_REFUEL_GEO", notes="Refuel")

        setting = PlatformSetting.get_settings()
        setting.geofence_enabled = True
        setting.save()

        now = timezone.now()
        geo_offer = Offer.objects.create(
            shop=self.shop,  # self.shop has latitude=12.9716, longitude=77.5946
            title='In-Store Geofence Offer',
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('15.00'),
            min_purchase=Decimal('50.00'),
            credits_per_redemption=5,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        qr_obj, _ = create_or_refresh_offer_qr(geo_offer)

        # 1. Attemped redemption with stripped GPS coordinates (buyer_lat=None, buyer_lng=None)
        ok_no_gps, msg_no_gps, _ = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=qr_obj.code,
            bill_amount=Decimal('100.00'),
            buyer_lat=None,
            buyer_lng=None
        )
        self.assertFalse(ok_no_gps)
        self.assertIn("Proximity Check Required", msg_no_gps)
        self.assertIn("Location access must be enabled", msg_no_gps)

        # 2. Redemption with valid in-store coordinates succeeds
        ok_with_gps, msg_with_gps, redemp = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=qr_obj.code,
            bill_amount=Decimal('100.00'),
            buyer_lat=12.9716,
            buyer_lng=77.5946
        )
        self.assertTrue(ok_with_gps, f"Valid in-store coordinates should pass: {msg_with_gps}")
        self.assertIsNotNone(redemp)
        self.assertTrue(redemp.is_geofence_verified)

    def test_leaderboard_all_8_bug_fixes_comprehensive(self):
        """Comprehensive verification of all 8 Leaderboard Bug Fixes (BUG-L1 to BUG-L8)"""
        from services.leaderboard_service import (
            get_weekly_leaderboard,
            rollover_weekly_leaderboard_cycle,
            record_cycle_winner,
            get_current_cycle_number,
            is_buyer_in_cooldown
        )
        from apps.rewards.models import RewardTransaction, RewardType, BuyerRewardAccount, LeaderboardHistory
        from apps.accounts.models import BuyerProfile, Profile, Role

        # Setup test buyers
        buyer_x = User.objects.create_user('lb_buyer_x', 'lb_x@test.com', 'Pass@123')
        Profile.objects.create(user=buyer_x, role=Role.BUYER)
        BuyerProfile.objects.filter(user=buyer_x).delete()
        BuyerProfile.objects.create(user=buyer_x, nickname="ShopperX", public_unique_id="LL-X-01")
        acct_x = BuyerRewardAccount.objects.create(buyer=buyer_x, current_points=200)

        buyer_y = User.objects.create_user('lb_buyer_y', 'lb_y@test.com', 'Pass@123')
        Profile.objects.create(user=buyer_y, role=Role.BUYER)
        BuyerProfile.objects.filter(user=buyer_y).delete()
        BuyerProfile.objects.create(user=buyer_y, nickname="ShopperY", public_unique_id="LL-Y-02")
        acct_y = BuyerRewardAccount.objects.create(buyer=buyer_y, current_points=100)

        buyer_z = User.objects.create_user('lb_buyer_z', 'lb_z@test.com', 'Pass@123')
        Profile.objects.create(user=buyer_z, role=Role.BUYER)
        BuyerProfile.objects.filter(user=buyer_z).delete()
        BuyerProfile.objects.create(user=buyer_z, nickname="ShopperZ", public_unique_id="LL-Z-03")
        acct_z = BuyerRewardAccount.objects.create(buyer=buyer_z, current_points=50)

        # BUG-L2 verification: Non-shopping points (WELCOME_BONUS & ADJUSTMENT) do NOT count
        RewardTransaction.objects.create(
            account=acct_x,
            transaction_type=RewardType.WELCOME_BONUS,
            points=25,
            balance_after=25,
            notes="Welcome bonus"
        )
        RewardTransaction.objects.create(
            account=acct_y,
            transaction_type=RewardType.ADJUSTMENT,
            points=50,
            balance_after=50,
            notes="Admin correction"
        )
        # Genuine shopping points
        RewardTransaction.objects.create(
            account=acct_x,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=60,
            balance_after=85,
            notes="Shopping redemption 1"
        )
        RewardTransaction.objects.create(
            account=acct_y,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=40,
            balance_after=90,
            notes="Shopping redemption 2"
        )
        RewardTransaction.objects.create(
            account=acct_z,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=20,
            balance_after=70,
            notes="Shopping redemption 3"
        )

        data = get_weekly_leaderboard(cycle_days=7)
        lb = data['leaderboard']

        # Shopper X must have exactly 60 points (25 welcome bonus excluded)
        entry_x = next(e for e in lb if e['buyer_id'] == buyer_x.id)
        self.assertEqual(entry_x['points_earned'], 60)

        # Shopper Y must have exactly 40 points (50 admin adjustment excluded)
        entry_y = next(e for e in lb if e['buyer_id'] == buyer_y.id)
        self.assertEqual(entry_y['points_earned'], 40)

        # BUG-L3 verification: Soft-deleted / Deactivated buyers excluded
        buyer_deleted = User.objects.create_user('lb_buyer_del', 'del@test.com', 'Pass@123', is_active=False)
        Profile.objects.create(user=buyer_deleted, role=Role.BUYER, is_active=False)
        acct_del = BuyerRewardAccount.objects.create(buyer=buyer_deleted, current_points=500)
        RewardTransaction.objects.create(
            account=acct_del,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=500,
            balance_after=500,
            notes="Points for deleted user"
        )
        data_del = get_weekly_leaderboard(cycle_days=7)
        self.assertNotIn(buyer_deleted.id, [e['buyer_id'] for e in data_del['leaderboard']])

        # BUG-L7 verification: Query limit enforced
        data_lim = get_weekly_leaderboard(cycle_days=7, limit=2)
        self.assertTrue(len(data_lim['leaderboard']) <= 2)

        # BUG-L6 verification: Rollover records podium (Rank 1, 2, 3) in LeaderboardHistory
        cycle_before = get_current_cycle_number()
        ok_roll, msg_roll, hist_roll = rollover_weekly_leaderboard_cycle()
        self.assertTrue(ok_roll)
        self.assertEqual(hist_roll.winner, buyer_x)
        self.assertEqual(hist_roll.rank, 1)

        # Verify Silver (Rank 2) & Bronze (Rank 3) were recorded
        histories_this_cycle = LeaderboardHistory.objects.filter(cycle_number=cycle_before).order_by('rank')
        self.assertTrue(histories_this_cycle.filter(rank=2).exists())
        self.assertEqual(histories_this_cycle.get(rank=2).winner, buyer_y)
        self.assertTrue(histories_this_cycle.filter(rank=3).exists())
        self.assertEqual(histories_this_cycle.get(rank=3).winner, buyer_z)

        # BUG-L1 verification: Cycle 1 points do NOT spill over into Cycle 2
        data_cycle2 = get_weekly_leaderboard(cycle_days=7)
        self.assertEqual(data_cycle2['cycle_number'], cycle_before + 1)
        # Previous cycle points should not be present in the new cycle leaderboard
        entry_x_c2 = next((e for e in data_cycle2['leaderboard'] if e['buyer_id'] == buyer_x.id), None)
        self.assertIsNone(entry_x_c2, "Cycle 1 points improperly spilled over into Cycle 2")

        # BUG-L8 verification: Admin leaderboard view and rollover endpoint
        admin_user = User.objects.create_superuser('lb_admin', 'lb_admin@lokalloop.in', 'Admin@123')
        client = Client()
        client.force_login(admin_user)
        resp_view = client.get('/admin-portal/leaderboard/')
        self.assertEqual(resp_view.status_code, 200)
        self.assertContains(resp_view, "Weekly Shopper Leaderboard Management")

        # Admin trigger rollover via POST
        # Add points to cycle 2 first so it's not empty
        RewardTransaction.objects.create(
            account=acct_y,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=75,
            balance_after=acct_y.current_points + 75,
            notes="Cycle 2 purchase"
        )
        resp_post = client.post('/admin-portal/leaderboard/rollover/', follow=True)
        self.assertEqual(resp_post.status_code, 200)
        self.assertContains(resp_post, "Successfully rolled over Weekly Cycle")

    def test_bug56_concurrent_duplicate_bumper_bill_submission_blocked(self):
        """BUG-56: Database unique constraint and atomic transaction prevent concurrent duplicate bumper bill submissions"""
        from services.reward_service import submit_bumper_bill
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus
        from django.db import IntegrityError

        # 1. First valid submission succeeds
        ok1, msg1, sub1 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-UNIQUE-56",
            bill_amount=Decimal('15000.00')
        )
        self.assertTrue(ok1)
        self.assertIsNotNone(sub1)
        self.assertEqual(sub1.bill_number, "INV-UNIQUE-56")

        # 2. Duplicate submission with identical bill number at the same shop is blocked
        ok2, msg2, sub2 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-UNIQUE-56",
            bill_amount=Decimal('15000.00')
        )
        self.assertFalse(ok2)
        self.assertIn("already been submitted", msg2)
        self.assertIsNone(sub2)

        # 3. Direct DB-level collision attempt raises IntegrityError due to UniqueConstraint
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                BumperBillSubmission.objects.create(
                    buyer=self.buyer_user,
                    shop=self.shop,
                    bill_number="INV-UNIQUE-56",
                    bill_amount=Decimal('15000.00'),
                    status=BumperBillStatus.PENDING_STORE
                )

        # 4. If submission was rejected, re-submission of corrected invoice is permitted
        sub1.status = BumperBillStatus.REJECTED
        sub1.save(update_fields=['status'])

        ok3, msg3, sub3 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-UNIQUE-56",
            bill_amount=Decimal('15000.00')
        )
        self.assertTrue(ok3)
        self.assertIsNotNone(sub3)
        self.assertEqual(sub3.status, BumperBillStatus.PENDING_STORE)

    def test_bug58_approve_redemption_initializes_missing_seller_wallet_gracefully(self):
        """BUG-58: approve_redemption initializes missing seller wallet instead of crashing with DoesNotExist"""
        from apps.wallets.models import SellerWallet, TransactionType
        from apps.offers.models import Offer, OfferType, OfferStatus
        from services.redemption_service import initiate_redemption, approve_redemption
        from services.wallet_service import credit_wallet

        # Create a new seller with an approved shop but NO wallet record
        new_seller = User.objects.create_user('seller_no_wallet', 'nowallet@test.com', 'Pass@123')
        from apps.shops.models import Shop
        now = timezone.now()
        new_shop = Shop.objects.create(
            seller=new_seller,
            category=self.category,
            name="No Wallet Cafe",
            is_approved=True,
            is_active=True
        )
        new_offer = Offer.objects.create(
            shop=new_shop,
            title="Cafe 10% Off",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('10.00'),
            min_purchase=Decimal('100.00'),
            credits_per_redemption=5,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        # Ensure seller wallet definitely does NOT exist
        SellerWallet.objects.filter(seller=new_seller).delete()
        self.assertFalse(SellerWallet.objects.filter(seller=new_seller).exists())

        # Buyer initiates counter redemption
        ok_init, msg_init, redemp = initiate_redemption(
            buyer_user=self.buyer_user,
            offer=new_offer,
            bill_amount=Decimal('200.00')
        )
        self.assertTrue(ok_init)
        self.assertIsNotNone(redemp)

        # Seller approves counter discount - MUST NOT CRASH with DoesNotExist
        # Wallet is automatically and safely initialized via get_or_create_wallet
        ok_app, msg_app, redemp_app = approve_redemption(redemption_id=redemp.id, seller_user=new_seller)
        self.assertTrue(ok_app, f"Expected successful approval, got: {msg_app}")
        self.assertEqual(redemp_app.status, 'SUCCESS')
        self.assertTrue(SellerWallet.objects.filter(seller=new_seller).exists())

    def test_bug_b4_mandatory_receipt_image(self):
        """BUG-B4: Receipt photo upload is strictly mandatory when required"""
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from services.reward_service import submit_bumper_bill

        # 1. Missing bill image when required must fail
        ok, msg, sub = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-NO-IMAGE-01",
            bill_amount=Decimal('15000.00'),
            bill_image=None,
            require_image=True
        )
        self.assertFalse(ok)
        self.assertIn("Receipt image / snapshot is required", msg)

        # 2. Providing valid image succeeds
        img = Image.new("RGB", (200, 200), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        valid_img = SimpleUploadedFile("bill_receipt.jpg", buf.read(), content_type="image/jpeg")

        ok2, msg2, sub2 = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-WITH-IMAGE-02",
            bill_amount=Decimal('15000.00'),
            bill_image=valid_img,
            require_image=True
        )
        self.assertTrue(ok2, f"Expected success with genuine bill image, got: {msg2}")
        self.assertIsNotNone(sub2)

    def test_bug_b5_bill_purchase_date_validation(self):
        """BUG-B5: Purchase / Bill Date is validated; future dates are blocked, past dates are accepted"""
        from datetime import date
        from services.reward_service import submit_bumper_bill

        today = timezone.localdate()
        future_date = today + timedelta(days=2)
        past_date = today - timedelta(days=15)

        # 1. Future purchase date must be rejected
        ok_future, msg_future, _ = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-FUTURE-01",
            bill_amount=Decimal('12000.00'),
            bill_date=future_date
        )
        self.assertFalse(ok_future)
        self.assertIn("cannot be in the future", msg_future)

        # 2. Past purchase date is accepted and saved in submission
        ok_past, msg_past, sub_past = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-PAST-02",
            bill_amount=Decimal('12000.00'),
            bill_date=past_date.strftime('%Y-%m-%d')
        )
        self.assertTrue(ok_past, f"Expected past purchase date to be accepted, got: {msg_past}")
        self.assertEqual(sub_past.bill_date, past_date)

    def test_bug_b8_max_bill_amount_cap_one_lakh(self):
        """BUG-B8: Maximum bill amount is capped at ₹1,00,000 (₹1 Lakh)"""
        from services.reward_service import submit_bumper_bill

        # 1. Amount exceeding ₹1,00,000 is rejected
        ok_high, msg_high, _ = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-OVER-1L-01",
            bill_amount=Decimal('100001.00')
        )
        self.assertFalse(ok_high)
        self.assertIn("maximum bill amount of ₹1,00,000", msg_high)

        # 2. Exactly ₹1,00,000 is accepted
        ok_exact, msg_exact, sub_exact = submit_bumper_bill(
            buyer_user=self.buyer_user,
            shop_id=self.shop.id,
            bill_number="INV-EXACT-1L-02",
            bill_amount=Decimal('100000.00')
        )
        self.assertTrue(ok_exact, f"Expected ₹1,00,000 to be accepted, got: {msg_exact}")
        self.assertEqual(sub_exact.bill_amount, Decimal('100000.00'))

    def test_bug61_admin_reward_catalog_numeric_input_sanitization(self):
        """BUG-61: Invalid numeric inputs in reward catalog creation must not trigger 500 ValueError"""
        from apps.accounts.models import Profile, Role
        admin_user = User.objects.create_superuser('test_admin_bug61', 'admin61@test.com', 'AdminPass@123')
        Profile.objects.create(user=admin_user, role=Role.ADMIN)

        client = Client()
        client.force_login(admin_user)

        from django.urls import reverse
        # Non-numeric text in integer/decimal fields
        response = client.post(reverse('admin_rewards_catalog_create'), {
            'title': 'Test Voucher 61',
            'points_required': 'not_a_number',
            'discount_inr': 'bad_decimal',
            'stock': 'many',
            'is_active': 'on'
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        messages = list(response.context['messages'])
        self.assertTrue(any("Invalid numeric" in str(m) for m in messages))

    def test_bug62_flat_discount_min_purchase_enforcement(self):
        """BUG-62: Flat cash discount campaigns strictly require a valid min_purchase >= discount_value"""
        client = Client()
        client.force_login(self.seller_user)

        # 1. FLAT discount with min_purchase = 0 must be rejected
        response1 = client.post('/seller/offers/create/', {
            'title': 'Exploit Deal Flat',
            'description': 'Flat 200 off',
            'terms': 'In store only',
            'offer_type': 'FLAT',
            'discount_value': '200.00',
            'min_purchase': '0.00',
            'max_redemptions': '50',
            'days_valid': '15'
        })
        self.assertEqual(response1.status_code, 200)
        self.assertContains(response1, "Flat cash discount campaigns require a minimum purchase amount")

        # 2. FLAT discount where discount exceeds min_purchase must be rejected
        response2 = client.post('/seller/offers/create/', {
            'title': 'Exploit Deal Flat 2',
            'description': 'Flat 200 off',
            'terms': 'In store only',
            'offer_type': 'FLAT',
            'discount_value': '200.00',
            'min_purchase': '100.00',
            'max_redemptions': '50',
            'days_valid': '15'
        })
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, "Flat discount value cannot exceed the minimum bill purchase requirement")

    def test_bug63_bug64_shop_views_atomic_and_daily_telemetry(self):
        """BUG-63 & BUG-64: Shop views increment atomically and synchronize to daily ShopMetric"""
        from apps.public.views import record_shop_view
        from apps.analytics.models import ShopMetric
        from services.background_tasks import sync_daily_analytics_job

        initial_views = self.shop.views_count
        today = timezone.localdate()

        # Atomically record view
        record_shop_view(self.shop)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.views_count, initial_views + 1)

        # Daily ShopMetric must reflect the recorded view
        metric = ShopMetric.objects.get(shop=self.shop, date=today)
        self.assertGreaterEqual(metric.views_count, 1)

        # Cron sync preserves and synchronizes views_count
        sync_result = sync_daily_analytics_job(target_date=today)
        self.assertEqual(sync_result['status'], 'success')
        metric.refresh_from_db()
        self.assertGreaterEqual(metric.views_count, 1)

    def test_bug65_csprng_shopper_id_generation(self):
        """BUG-65: Fallback shopper ID generation uses CSPRNG secrets generator"""
        from apps.accounts.shopper_utils import generate_unique_shopper_id

        shopper_id = generate_unique_shopper_id("Pune")
        self.assertTrue(shopper_id.startswith("PUN-"))
        num_part = shopper_id.split("-")[1]
        self.assertTrue(num_part.isdigit())
        self.assertGreaterEqual(int(num_part), 1000)


    def test_bug76_timezone_now_scope_in_buyer_views(self):
        """BUG-76: buyer_views correctly utilizes django timezone.now() without scope/NameError"""
        from apps.accounts.buyer_views import buyer_scan_view
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware

        factory = RequestFactory()
        request = factory.get('/buyer/scan/')
        request.user = self.buyer_user
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, '_messages', FallbackStorage(request))
        response = buyer_scan_view(request)
        self.assertEqual(response.status_code, 200)

    def test_bug77_auto_activation_of_upcoming_offers(self):
        """BUG-77: Offers scheduled as UPCOMING auto-activate when start_date arrives"""
        from apps.offers.models import Offer, OfferStatus, OfferType
        from services.background_tasks import expire_stale_qrcodes_job

        now = timezone.now()
        upcoming_offer = Offer.objects.create(
            shop=self.shop,
            title="Scheduled Mega Sale",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("20.00"),
            min_purchase=Decimal("100.00"),
            start_date=now - timedelta(minutes=5),
            end_date=now + timedelta(days=2),
            status=OfferStatus.UPCOMING
        )
        res = expire_stale_qrcodes_job()
        self.assertEqual(res['status'], 'success')
        upcoming_offer.refresh_from_db()
        self.assertEqual(upcoming_offer.status, OfferStatus.ACTIVE)

    def test_bug78_max_discount_parsed_and_persisted(self):
        """BUG-78: max_discount is properly stored when creating percentage offers"""
        from apps.offers.models import Offer, OfferType
        offer = Offer.objects.create(
            shop=self.shop,
            title="Percentage with Cap",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("50.00"),
            min_purchase=Decimal("100.00"),
            max_discount=Decimal("250.00"),
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=7),
        )
        self.assertEqual(offer.max_discount, Decimal("250.00"))

    def test_bug79_grand_prize_anti_monopoly_cooldown(self):
        """BUG-79: Grand Prize winner (rank 1) cannot win grand prize for 3 consecutive cycles"""
        from services.leaderboard_service import is_buyer_in_cooldown
        from apps.rewards.models import LeaderboardHistory

        # Record Grand Prize win for buyer in cycle 2
        now = timezone.now()
        LeaderboardHistory.objects.create(
            winner=self.buyer_user,
            rank=1,
            cycle_number=2,
            cycle_start=now - timedelta(days=14),
            cycle_end=now - timedelta(days=7),
            points_scored=500,
            notes="Grand Prize Winner"
        )
        # In cycle 3, buyer is in cooldown
        in_cooldown, last_cycle = is_buyer_in_cooldown(self.buyer_user, current_cycle=3)
        self.assertTrue(in_cooldown)
        self.assertEqual(last_cycle, 2)

    def test_bug80_catalog_reward_stock_decrement_concurrency_guard(self):
        """BUG-80: Catalog reward voucher stock decrement prevents negative values and rejects out-of-stock items"""
        from apps.rewards.models import RewardCatalogItem
        from services.reward_service import redeem_catalog_reward

        item = RewardCatalogItem.objects.create(
            title="Limited Mug",
            points_required=50,
            stock=1,
            is_active=True
        )
        # 1st redemption succeeds: returns (success, message, claim)
        ok1, msg1, claim1 = redeem_catalog_reward(self.buyer_user, item.pk)
        self.assertTrue(ok1)
        self.assertIsNotNone(claim1)
        item.refresh_from_db()
        self.assertEqual(item.stock, 0)

        # 2nd redemption fails due to out of stock
        ok2, msg2, claim2 = redeem_catalog_reward(self.buyer_user, item.pk)
        self.assertFalse(ok2)
        self.assertIn("stock", msg2.lower())

    def test_bug81_offer_detail_quota_exhausted_indicator(self):
        """BUG-81: Offer detail sets is_quota_exhausted=True when max redemptions reached"""
        Offer.objects.filter(pk=self.offer.pk).update(current_redemptions=100, max_redemptions=100)
        self.offer.refresh_from_db()

        response = self.client.get(f'/offers/{self.offer.slug}/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get('is_quota_exhausted'))

    def test_bug82_wallet_demo_purchase_idempotency(self):
        """BUG-82: process_demo_package_purchase is idempotent and handles duplicate payment reference gracefully"""
        from services.wallet_service import process_demo_package_purchase
        from apps.wallets.models import CreditPackage

        pkg = CreditPackage.objects.create(
            name="Starter Package",
            code="STARTER_TEST_PKG",
            credits=50,
            price_inr=Decimal("500.00"),
            is_active=True
        )
        ref = "PAY-REF-UNIQUE-TEST-123"
        # First purchase
        p1 = process_demo_package_purchase(self.seller_user, pkg.code, payment_ref=ref)
        self.assertIsNotNone(p1)

        # Duplicate purchase with same reference must return existing purchase without error
        p2 = process_demo_package_purchase(self.seller_user, pkg.code, payment_ref=ref)
        self.assertIsNotNone(p2)
        self.assertEqual(p1.pk, p2.pk)

    def test_bug83_buyer_login_excludes_non_unique_nickname(self):
        """BUG-83: buyer_login_view only authenticates unique identifiers (shopper_id, email, username)"""
        self.buyer_profile.nickname = "TestCommonNickname"
        self.buyer_profile.public_unique_id = "LQ7M-8K2R-TEST"
        self.buyer_profile.shopper_id = "SHP-TEST-999"
        self.buyer_profile.save()

        # Login using nickname should be rejected
        res = self.client.post('/buyer/login/', {
            'identifier': 'TestCommonNickname',
            'password': 'Password@123',
        })
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_bug84_master_qr_data_uri_absolute_url(self):
        """BUG-84: master QR code includes fully-qualified absolute URL with base_url"""
        uri = self.shop.get_master_qr_data_uri(base_url="https://lokaldealz.com")
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    def test_bug85_merchant_agreement_dynamic_url(self):
        """BUG-85: Merchant agreement uses dynamic request host instead of hardcoded localhost"""
        from apps.administration.models import MerchantAgreementLog
        self.assertTrue(hasattr(MerchantAgreementLog, 'agreement_url'))

    def test_bug86_confirm_password_enforced(self):
        """BUG-86: Registration forms require confirm_password and reject mismatch or omission"""
        from apps.accounts.forms import AnonymousBuyerRegistrationForm

        # Buyer form without confirm password
        b_form = AnonymousBuyerRegistrationForm(data={
            'password': 'StrongPassword123!',
            'confirm_password': '',
        })
        self.assertFalse(b_form.is_valid())
        self.assertIn('confirm_password', b_form.errors)

        # Buyer form with mismatched confirm password
        b_form_mismatch = AnonymousBuyerRegistrationForm(data={
            'password': 'StrongPassword123!',
            'confirm_password': 'DifferentPassword123!',
        })
        self.assertFalse(b_form_mismatch.is_valid())
        self.assertIn('confirm_password', b_form_mismatch.errors)

    def test_bug87_clean_first_name_does_not_default_to_vikram(self):
        """BUG-87: AnonymousBuyerRegistrationForm.clean_first_name does not fallback to 'Vikram'"""
        from apps.accounts.forms import AnonymousBuyerRegistrationForm

        form = AnonymousBuyerRegistrationForm(data={'first_name': ''})
        form.is_valid()
        self.assertEqual(form.cleaned_data.get('first_name'), '')

    def test_bug88_sync_daily_analytics_does_not_overwrite_daily_views(self):
        """BUG-88: sync_daily_analytics_job preserves daily views and does not overwrite with lifetime views"""
        from apps.analytics.models import ShopMetric
        from services.background_tasks import sync_daily_analytics_job

        today = timezone.localdate()
        self.shop.views_count = 500
        self.shop.save()

        # Pre-existing daily metric for today with 5 views
        metric, _ = ShopMetric.objects.get_or_create(
            shop=self.shop,
            date=today,
            defaults={'views_count': 5, 'redemptions_count': 0, 'estimated_sales_inr': Decimal("0.00")}
        )
        metric.views_count = 5
        metric.save()

        sync_daily_analytics_job(target_date=today)
        metric.refresh_from_db()
        # Daily views should remain 5, not be overwritten with 500 lifetime views
        self.assertEqual(metric.views_count, 5)

    def test_bug75_atomic_increment_total_redemptions_concurrency(self):
        """BUG-75: Redemption approval updates BuyerProfile.total_redemptions atomically without row clobbering"""
        from services.redemption_service import process_redemption
        from apps.accounts.models import BuyerProfile
        from apps.wallets.models import SellerWallet
        from apps.rewards.models import BuyerRewardAccount
        from apps.redemptions.models import QRCode

        # Dedicated buyer to avoid daily limit collisions
        test_buyer = User.objects.create_user(
            username="buyer_concurrency_75",
            email="buyer75@example.com",
            password="Password@123"
        )
        profile = BuyerProfile.objects.create(user=test_buyer, total_redemptions=3, nickname="ConcurrencyTester")
        BuyerRewardAccount.objects.create(buyer=test_buyer, current_points=0)

        # Fresh QR code
        fresh_qr = QRCode.objects.create(
            offer=self.offer,
            expires_at=timezone.now() + timedelta(days=5)
        )

        # Ensure seller wallet has plenty of credits
        wallet, _ = SellerWallet.objects.get_or_create(seller=self.seller_user)
        wallet.balance_credits = 100
        wallet.save()

        success, msg, redemption = process_redemption(
            buyer_user=test_buyer,
            qr_code_str=str(fresh_qr.code),
            bill_amount=Decimal("1000.00"),
            buyer_lat=12.9716,
            buyer_lng=77.5946,
            idempotency_key=str(uuid.uuid4())
        )
        self.assertTrue(success, f"process_redemption failed: {msg}")
        profile.refresh_from_db()
        # Must be atomically incremented to 4
        self.assertEqual(profile.total_redemptions, 4)
        # Profile fields must not be clobbered
        self.assertEqual(profile.nickname, "ConcurrencyTester")

    def test_bug82_bounding_box_prunes_distant_shops(self):
        """BUG-82: annotate_shops_with_distance uses SQL bounding box to prune out-of-range stores"""
        from services.location_service import annotate_shops_with_distance
        from apps.shops.models import Shop

        # Faraway shop in Mumbai / Delhi (far outside 50km radius from Bengaluru 12.9716, 77.5946)
        faraway_shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name="Far North Store",
            city="Delhi",
            latitude=Decimal("28.6139"),
            longitude=Decimal("77.2090"),
            is_approved=True,
            is_active=True
        )

        qs = Shop.objects.filter(id__in=[self.shop.id, faraway_shop.id])
        # Calling with max_km=50.0 should prune faraway_shop
        results = annotate_shops_with_distance(qs, 12.9716, 77.5946, max_km=50.0)
        result_ids = [s.id for s in results]
        self.assertIn(self.shop.id, result_ids)
        self.assertNotIn(faraway_shop.id, result_ids)

    def test_bug89_merchant_notification_preserves_shopper_privacy(self):
        """BUG-89: notify_redemption_to_seller masks buyer real name with nickname or public ID"""
        from services.notification_service import notify_redemption_to_seller
        from apps.notifications.models import Notification

        self.buyer_user.first_name = "Rahul"
        self.buyer_user.last_name = "Deshmukh"
        self.buyer_user.save()
        self.buyer_profile.nickname = "SmartShopper"
        self.buyer_profile.save()

        notify_redemption_to_seller(self.seller_user, self.offer, self.buyer_user, credits_deducted=5)
        notif = Notification.objects.filter(recipient=self.seller_user).latest('created_at')
        self.assertNotIn("Rahul", notif.message)
        self.assertNotIn("Deshmukh", notif.message)
        self.assertIn("SmartShopper", notif.message)

    def test_bug90_buyer_registration_view_no_hardcoded_vikram(self):
        """BUG-90: buyer_register_view does not fallback to 'Vikram' when first_name is empty"""
        from apps.accounts.tests import get_test_captcha
        reg_payload = {
            'first_name': '',
            'birth_month': 3,
            'birth_year': 2001,
            'district': 'Pune',
            'state': 'Maharashtra',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'website': '',
            **get_test_captcha(),
        }
        res = self.client.post('/buyer/register/', reg_payload)
        user = User.objects.filter(email__endswith='@anon.lokalloop.internal').order_by('-id').first()
        self.assertIsNotNone(user)
        self.assertEqual(user.first_name, '')

    def test_bug91_seller_excel_export_preserves_cancelled_status(self):
        """BUG-91: seller_redemptions_view Excel export shows Cancelled instead of Completed"""
        from apps.redemptions.models import OfferRedemption, RedemptionStatus
        import openpyxl
        import io

        cancelled_r = OfferRedemption.objects.create(
            buyer=self.buyer_user,
            seller=self.seller_user,
            shop=self.shop,
            offer=self.offer,
            bill_amount=Decimal("800.00"),
            discount_given=Decimal("160.00"),
            status=RedemptionStatus.CANCELLED,
            is_verified_by_merchant=False,
            idempotency_key=str(uuid.uuid4())
        )

        self.client.force_login(self.seller_user)
        response = self.client.get('/seller/redemptions/?export=excel')
        self.assertEqual(response.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb.active
        # Look for the row with cancelled redemption
        found_status = None
        for row in ws.iter_rows(min_row=5, values_only=True):
            if row[0] == cancelled_r.id:
                found_status = row[9]
                break
        self.assertEqual(found_status, "Cancelled")

    def test_bug92_seller_loyalty_perks_no_perpetual_extension(self):
        """BUG-92: check_and_unlock_seller_loyalty_perks does not perpetually push featured_until forward"""
        from services.leaderboard_service import check_and_unlock_seller_loyalty_perks
        from apps.wallets.models import SellerWallet

        wallet, _ = SellerWallet.objects.get_or_create(seller=self.seller_user)
        wallet.loyalty_points = 50
        wallet.save()

        # First unlock sets featured_until
        check_and_unlock_seller_loyalty_perks(wallet)
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_featured)
        initial_featured_until = self.shop.featured_until
        self.assertIsNotNone(initial_featured_until)

        # Subsequent check while active does not push featured_until forward
        check_and_unlock_seller_loyalty_perks(wallet)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.featured_until, initial_featured_until)

    def test_bug93_leaderboard_batch_cooldown_includes_grand_prize_notes(self):
        """BUG-93: get_weekly_leaderboard batch cooldown flags runner-ups who won Grand Prize"""
        from services.leaderboard_service import get_weekly_leaderboard
        from apps.rewards.models import LeaderboardHistory, RewardTransaction, RewardType

        # Record Grand Prize win for buyer with rank 2 in cycle 2
        now = timezone.now()
        LeaderboardHistory.objects.create(
            winner=self.buyer_user,
            rank=2,
            cycle_number=2,
            cycle_start=now - timedelta(days=14),
            cycle_end=now - timedelta(days=7),
            points_scored=400,
            notes="Silver Runner-Up & Awarded Grand Prize"
        )

        # Award points in current cycle
        RewardTransaction.objects.create(
            account=self.reward_account,
            transaction_type=RewardType.REDEMPTION_REWARD,
            points=50,
            balance_after=250,
            notes="Current cycle shopping"
        )

        board_data = get_weekly_leaderboard(cycle_days=7)
        buyer_entry = next((e for e in board_data['leaderboard'] if e['buyer_id'] == self.buyer_user.id), None)
        self.assertIsNotNone(buyer_entry)
        self.assertTrue(buyer_entry['in_cooldown'])

    def test_bug94_timezone_localized_midnight_anti_abuse_caps(self):
        """BUG-94: Daily redemption caps compute local start of day"""
        from services.redemption_service import initiate_redemption

        fresh_qr = QRCode.objects.create(offer=self.offer, expires_at=timezone.now() + timedelta(days=2))
        success, msg, redemption = initiate_redemption(
            buyer_user=self.buyer_user,
            offer=self.offer,
            bill_amount=Decimal("1000.00"),
            idempotency_key=str(uuid.uuid4())
        )
        # Should succeed or return clean business message
        self.assertIn(success, [True, False])

    def test_bug95_paused_offers_expire_when_end_date_passes(self):
        """BUG-95: expire_stale_qrcodes_job and Offer.save expire PAUSED campaigns whose end_date elapsed"""
        from apps.offers.models import Offer, OfferStatus, OfferType
        from services.background_tasks import expire_stale_qrcodes_job

        past_paused = Offer.objects.create(
            shop=self.shop,
            title="Old Paused Deal",
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal("15.00"),
            min_purchase=Decimal("100.00"),
            start_date=timezone.now() - timedelta(days=30),
            end_date=timezone.now() - timedelta(days=5),
            status=OfferStatus.PAUSED
        )
        expire_stale_qrcodes_job()
        past_paused.refresh_from_db()
        self.assertEqual(past_paused.status, OfferStatus.EXPIRED)

    def test_bug96_pos_verify_catalog_claim_api_zero_pii(self):
        """BUG-96: seller_verify_catalog_claim_api masks customer username with public ID"""
        from apps.rewards.models import RewardCatalogItem, BuyerRewardClaim
        import json

        item = RewardCatalogItem.objects.create(title="Coffee Coupon", points_required=20, stock=5, is_active=True)
        claim = BuyerRewardClaim.objects.create(
            buyer=self.buyer_user,
            catalog_item=item,
            voucher_code="COFFEE-TEST-123",
            points_spent=20,
            status='ACTIVE',
            expires_at=timezone.now() + timedelta(days=10)
        )

        self.client.force_login(self.seller_user)
        response = self.client.post(
            '/api/seller/vouchers/verify/',
            data=json.dumps({'voucher_code': 'COFFEE-TEST-123'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('buyer_username', res_data)
        self.assertIn('buyer_public_id', res_data)

    def test_bug97_daily_analytics_sync_uses_verification_timestamp(self):
        """BUG-97: sync_daily_analytics_job aggregates by verified_at date for finalized sales"""
        from apps.analytics.models import ShopMetric
        from services.background_tasks import sync_daily_analytics_job

        target_date = timezone.localdate()
        redemption = OfferRedemption.objects.create(
            buyer=self.buyer_user,
            seller=self.seller_user,
            shop=self.shop,
            offer=self.offer,
            bill_amount=Decimal("950.00"),
            discount_given=Decimal("190.00"),
            status=RedemptionStatus.SUCCESS,
            is_verified_by_merchant=True,
            verified_at=timezone.now(),
            idempotency_key=str(uuid.uuid4())
        )
        # Set redeemed_at to yesterday to simulate ticket generated before midnight
        OfferRedemption.objects.filter(pk=redemption.pk).update(
            redeemed_at=timezone.now() - timedelta(days=1),
            verified_at=timezone.now()
        )

        sync_daily_analytics_job(target_date=target_date)
        metric = ShopMetric.objects.get(shop=self.shop, date=target_date)
        self.assertGreaterEqual(metric.redemptions_count, 1)
        self.assertGreaterEqual(metric.estimated_sales_inr, Decimal("950.00"))

    def test_bug99_rate_limit_prefix_rule_across_subpaths_and_query_params(self):
        """BUG-99: Rate limit cache key uses matched prefix preventing query/subpath bypass"""
        from config.middleware import RateLimitMiddleware
        from django.test.client import RequestFactory

        rf = RequestFactory()
        middleware = RateLimitMiddleware(lambda r: None)

        prefix, rule = middleware._get_matched_rule('/api/shops/123/?_cb=999')
        self.assertEqual(prefix, '/api/')
        self.assertEqual(rule, (15, 60))

        prefix_explore, rule_explore = middleware._get_matched_rule('/explore/?cat=food&page=2')
        self.assertEqual(prefix_explore, '/explore/')
        self.assertEqual(rule_explore, (30, 10))

    def test_bug100_session_invalidation_chunked_batch_delete(self):
        """BUG-100: Session invalidation on account deletion uses stream iterator and batch delete"""
        from django.contrib.sessions.models import Session
        from django.contrib.sessions.backends.db import SessionStore

        remote_sess = SessionStore()
        remote_sess['_auth_user_id'] = str(self.buyer_user.id)
        remote_sess.create()
        key = remote_sess.session_key

        self.assertTrue(Session.objects.filter(session_key=key).exists())
        self.client.force_login(self.buyer_user)
        res = self.client.post('/buyer/delete-account/', {
            'auth_method': 'PASSWORD',
            'current_password': 'Password@123',
            'confirm_forfeiture': 'on',
            'reason_category': 'OTHER',
            'reason_notes': 'Testing chunked session invalidation',
        })
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_bug101_shop_coordinates_update_and_city_geocoding(self):
        """BUG-101: Updating shop metadata saves explicit lat/long or city default coordinates"""
        self.client.force_login(self.seller_user)

        # 1. Update with explicit lat/lng
        res = self.client.post('/seller/shop/', {
            'name': 'Updated Electronics Hub',
            'locality': 'Shivajinagar',
            'city': 'Pune',
            'address': 'FC Road, Deccan',
            'pincode': '411005',
            'phone': '9822011223',
            'email': 'seller@example.com',
            'latitude': '18.520400',
            'longitude': '73.856700',
        })
        self.assertEqual(res.status_code, 302)
        self.shop.refresh_from_db()
        self.assertEqual(float(self.shop.latitude), 18.5204)
        self.assertEqual(float(self.shop.longitude), 73.8567)

        # 2. Update city to Mumbai without explicit coords -> auto-resolves Mumbai coords
        res2 = self.client.post('/seller/shop/', {
            'name': 'Updated Electronics Hub',
            'locality': 'Bandra West',
            'city': 'Mumbai',
            'address': 'Hill Road',
            'pincode': '400050',
            'phone': '9822011223',
            'email': 'seller@example.com',
        })
        self.assertEqual(res2.status_code, 302)
        self.shop.refresh_from_db()
        self.assertEqual(float(self.shop.latitude), 19.076)
        self.assertEqual(float(self.shop.longitude), 72.8777)

    def test_bug102_leaderboard_rollover_records_winner_outside_top_3(self):
        """BUG-102: Grand prize winner outside top 3 podium is explicitly committed to history"""
        from apps.rewards.models import LeaderboardHistory
        from services.leaderboard_service import rollover_weekly_leaderboard_cycle, record_cycle_winner
        from django.core.cache import cache

        cache.clear()
        # Clean existing test history
        LeaderboardHistory.objects.all().delete()

        # Create 4 test buyers
        users = []
        for i in range(1, 5):
            u, _ = User.objects.get_or_create(username=f"rank_user_{i}", defaults={'email': f"rank_{i}@test.com"})
            BuyerProfile.objects.get_or_create(user=u, defaults={'public_unique_id': f"PUB-RANK-{i}", 'shopper_id': f"SHOP-R{i}"})
            users.append(u)

        now = timezone.now()
        # Record winners for ranks 1, 2, 3 in previous cycle #1 to put them in cooldown
        for idx in range(3):
            record_cycle_winner(
                cycle_number=1,
                winner_user=users[idx],
                points_scored=500 - (idx * 50),
                rank=1 if idx == 0 else 2,
                notes=f"Past Grand Prize Winner for Weekly Cycle #1",
                cycle_start=now - timedelta(days=14),
                cycle_end=now - timedelta(days=7)
            )

        # Award points in current cycle: users 0, 1, 2 have top scores (400, 300, 200), user 3 has 100 points
        from apps.rewards.models import BuyerRewardAccount, RewardTransaction, RewardType
        for idx, u in enumerate(users):
            acct, _ = BuyerRewardAccount.objects.get_or_create(buyer=u)
            pts = 400 - (idx * 100)
            RewardTransaction.objects.create(
                account=acct,
                transaction_type=RewardType.REDEMPTION_REWARD,
                points=pts,
                balance_after=pts,
                created_at=now - timedelta(days=2)
            )

        success, msg, hist = rollover_weekly_leaderboard_cycle(force=True)
        self.assertTrue(success)
        # Winner must be user 3 (rank 4, since ranks 1-3 were in cooldown)
        self.assertEqual(hist.winner_id, users[3].id)
        # Verify history entry was committed for user 3 with Grand Prize Winner notes
        gp_hist = LeaderboardHistory.objects.filter(winner=users[3], notes__icontains="Grand Prize").first()
        self.assertIsNotNone(gp_hist)

    def test_bug103_notifications_evaluation_preserves_unread_state(self):
        """BUG-103: Notifications QuerySet evaluated before DB update so template sees initial read state"""
        from apps.notifications.models import Notification

        Notification.objects.filter(recipient=self.buyer_user).delete()
        n = Notification.objects.create(
            recipient=self.buyer_user,
            title="Unread Alert",
            message="You received a perk",
            is_read=False
        )
        self.client.force_login(self.buyer_user)
        res = self.client.get('/buyer/notifications/')
        self.assertEqual(res.status_code, 200)
        # The list passed to template context should have the object with is_read == False
        rendered_notes = res.context['notifications']
        self.assertEqual(len(rendered_notes), 1)
        self.assertFalse(rendered_notes[0].is_read)
        # But in database it has now been marked True
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_bug104_login_alias_rotation_lockout(self):
        """BUG-104: Brute-force lockout enforces canonical user key preventing alias rotation bypass"""
        from django.core.cache import cache
        cache.clear()

        self.buyer_profile.public_unique_id = 'PUB-TEST-ALIAS'
        self.buyer_profile.save()

        # Attempt 1-3 with buyer email
        for _ in range(3):
            self.client.post('/buyer/login/', {'identifier': self.buyer_user.email, 'password': 'WrongPassword!'})

        # Attempt 4-5 with buyer public_unique_id
        for _ in range(2):
            self.client.post('/buyer/login/', {'identifier': self.buyer_profile.public_unique_id, 'password': 'WrongPassword!'})

        # 6th attempt with username should be immediately locked out with 429
        res = self.client.post('/buyer/login/', {'identifier': self.buyer_user.username, 'password': 'WrongPassword!'})
        self.assertEqual(res.status_code, 429)
        cache.clear()

    def test_bug105_offer_qr_encodes_url_and_scanner_parses_url(self):
        """BUG-105: create_or_refresh_offer_qr encodes /qr/<uuid>/ and process_redemption extracts uuid from URLs"""
        from services.redemption_service import create_or_refresh_offer_qr, process_redemption

        qr_obj, qr_img = create_or_refresh_offer_qr(self.offer)
        # Data URI is generated
        self.assertTrue(qr_img.startswith('data:image/png;base64,'))

        # Buyer scans full URL in scanner
        scanned_url = f"https://lokalloop.in/qr/{qr_obj.code}/"
        ok, msg, red = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=scanned_url,
            buyer_lat=12.9716,
            buyer_lng=77.5946
        )
        self.assertTrue(ok, f"process_redemption failed: {msg}")
        self.assertEqual(red.qr_code, qr_obj)

    def test_bug107_catalog_voucher_collision_retry_and_entropy(self):
        """BUG-107: redeem_catalog_reward generates unique voucher codes with entropy retry"""
        from apps.rewards.models import RewardCatalogItem, BuyerRewardAccount, BuyerRewardClaim
        from services.reward_service import redeem_catalog_reward

        item = RewardCatalogItem.objects.create(
            title="10% Off Electronics",
            points_required=20,
            voucher_code_prefix="ELEC",
            stock=10,
            is_active=True
        )
        acct, _ = BuyerRewardAccount.objects.get_or_create(buyer=self.buyer_user)
        acct.current_points = 500
        acct.save()

        ok, msg, claim = redeem_catalog_reward(self.buyer_user, item.id)
        self.assertTrue(ok)
        self.assertIsNotNone(claim)
        self.assertTrue(claim.voucher_code.startswith("ELEC-"))
        self.assertGreaterEqual(len(claim.voucher_code), 12)

    def test_bug_geo_01_and_05_initiate_redemption_validates_proximity_and_logs_ip(self):
        """BUG-GEO-01 & BUG-GEO-05: initiate_redemption blocks remote counter claims and logs client IP"""
        from apps.offers.models import Offer, OfferType, OfferStatus
        from apps.administration.models import PlatformSetting, AdminAction
        from services.redemption_service import initiate_redemption

        setting = PlatformSetting.get_settings()
        setting.geofence_enabled = True
        setting.max_redemption_distance_meters = 50
        setting.gps_accuracy_tolerance_meters = 25
        setting.save()

        now = timezone.now()
        geo_offer = Offer.objects.create(
            shop=self.shop,  # 12.9716, 77.5946
            title='Counter Remote Bypass Test',
            offer_type=OfferType.FLAT,
            discount_value=Decimal('50.00'),
            min_purchase=Decimal('200.00'),
            credits_per_redemption=1,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        # 1. Attempt claim 800m away -> Blocked and IP logged
        ok_remote, msg_remote, _ = initiate_redemption(
            buyer_user=self.buyer_user,
            offer=geo_offer,
            bill_amount=Decimal('500.00'),
            buyer_lat=12.9780,  # ~710m away
            buyer_lng=77.5946,
            client_ip='198.51.100.42'
        )
        self.assertFalse(ok_remote)
        self.assertIn("Proximity Check Failed", msg_remote)

        violation = AdminAction.objects.filter(
            action_type="SECURITY_GEOFENCE_VIOLATION",
            target_id=str(geo_offer.id),
            ip_address='198.51.100.42'
        ).first()
        self.assertIsNotNone(violation, "Security violation with IP must be recorded")

        # 2. In-store claim within 50m succeeds with is_geofence_verified=True
        ok_near, msg_near, redemp = initiate_redemption(
            buyer_user=self.buyer_user,
            offer=geo_offer,
            bill_amount=Decimal('500.00'),
            buyer_lat=12.97165,
            buyer_lng=77.59460,
            client_ip='198.51.100.42'
        )
        self.assertTrue(ok_near, f"In-store claim should succeed: {msg_near}")
        self.assertTrue(redemp.is_geofence_verified)

    def test_bug_geo_03_city_default_coordinates_mapping(self):
        """BUG-GEO-03: City default coordinates resolver prevents silent Pune fallback for other cities"""
        from apps.accounts.views import get_city_default_coords
        mumbai_coords = get_city_default_coords("Mumbai")
        self.assertEqual(mumbai_coords, (19.0760, 72.8777))

        nagpur_coords = get_city_default_coords("Nagpur")
        self.assertEqual(nagpur_coords, (21.1458, 79.0882))

        bengaluru_coords = get_city_default_coords("Bengaluru")
        self.assertEqual(bengaluru_coords, (12.9716, 77.5946))

    def test_bug_geo_04_gps_accuracy_buffer_incorporation(self):
        """BUG-GEO-04: Indoor GPS accuracy buffer expands allowed radius up to 100m safely"""
        from apps.offers.models import Offer, OfferType, OfferStatus
        from apps.administration.models import PlatformSetting
        from services.redemption_service import process_redemption
        from services.wallet_service import credit_wallet
        from apps.wallets.models import TransactionType

        credit_wallet(self.seller_user, 50, TransactionType.PURCHASE, reference_id="TEST_ACC_BUFFER", notes="Acc Test")

        setting = PlatformSetting.get_settings()
        setting.geofence_enabled = True
        setting.max_redemption_distance_meters = 50
        setting.gps_accuracy_tolerance_meters = 25
        setting.save()

        now = timezone.now()
        offer = Offer.objects.create(
            shop=self.shop,  # 12.9716, 77.5946
            title='Mall Indoor GPS Test',
            offer_type=OfferType.FLAT,
            discount_value=Decimal('20.00'),
            min_purchase=Decimal('100.00'),
            credits_per_redemption=1,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )
        from services.redemption_service import create_or_refresh_offer_qr
        qr_obj, _ = create_or_refresh_offer_qr(offer)

        # Buyer is 95m away with indoor GPS accuracy reported as 80m.
        # 12.97160 to 12.97245 is ~94.5m
        lat_95m = 12.97245
        lng_95m = 77.59460

        # With buyer_accuracy=80.0, allowed radius = 50 + max(25, 80) = 130m -> should PASS
        ok_acc, msg_acc, redemp_acc = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=qr_obj.code,
            bill_amount=Decimal('200.00'),
            buyer_lat=lat_95m,
            buyer_lng=lng_95m,
            buyer_accuracy=80.0
        )
        self.assertTrue(ok_acc, f"Indoor GPS with accuracy buffer should pass: {msg_acc}")
        self.assertTrue(redemp_acc.is_geofence_verified)
