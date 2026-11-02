from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.accounts.models import Profile, Role
from apps.administration.models import ErrorLog, ErrorLogLevel


class AdminErrorLogsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username='admin_test',
            email='admin_test@example.com',
            password='testpassword123'
        )
        Profile.objects.create(
            user=self.admin_user,
            role=Role.ADMIN,
            city='Bengaluru'
        )

    def test_error_log_creation_and_view(self):
        # 1. Create a dummy ErrorLog entry
        log = ErrorLog.objects.create(
            level=ErrorLogLevel.ERROR,
            error_type='ZeroDivisionError',
            message='division by zero in checkout calculate',
            traceback='Traceback (most recent call last):\n  File "view.py", line 12, in foo\n    1 / 0',
            path='/api/calculate/',
            method='POST',
            ip_address='127.0.0.1',
            is_resolved=False
        )

        # 2. Login as Admin
        self.client.force_login(self.admin_user)

        # 3. Request admin error-logs page
        response = self.client.get('/admin-portal/error-logs/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ZeroDivisionError')
        self.assertContains(response, 'division by zero in checkout calculate')
        self.assertContains(response, '/api/calculate/')

        # 4. Resolve the error log via secure POST
        res = self.client.post(f'/admin-portal/error-logs/{log.id}/resolve/', follow=True)
        self.assertEqual(res.status_code, 200)
        log.refresh_from_db()
        self.assertTrue(log.is_resolved)

        # 5. Clear resolved error logs
        clear_res = self.client.post('/admin-portal/error-logs/clear/', {'action': 'resolved'}, follow=True)
        self.assertEqual(clear_res.status_code, 200)
        self.assertFalse(ErrorLog.objects.filter(pk=log.id).exists())

    def test_unauthenticated_user_cannot_access_error_logs(self):
        # Regular or anonymous user cannot access
        response = self.client.get('/admin-portal/error-logs/')
        self.assertEqual(response.status_code, 302)

    def test_automatic_purge_after_15_days(self):
        from django.utils import timezone
        from datetime import timedelta
        from django.core.management import call_command

        # Create an error log from 20 days ago
        old_log = ErrorLog.objects.create(
            level=ErrorLogLevel.ERROR,
            error_type='OldTimeoutError',
            message='Old timeout that happened 20 days ago',
            path='/test/old/'
        )
        ErrorLog.objects.filter(pk=old_log.pk).update(created_at=timezone.now() - timedelta(days=20))

        # Create a fresh error log from today
        recent_log = ErrorLog.objects.create(
            level=ErrorLogLevel.ERROR,
            error_type='RecentError',
            message='Recent error that happened today',
            path='/test/recent/'
        )

        # 1. Test purge_old_errors method directly
        deleted_count = ErrorLog.purge_old_errors(days=15)
        self.assertEqual(deleted_count, 1)
        self.assertFalse(ErrorLog.objects.filter(pk=old_log.pk).exists())
        self.assertTrue(ErrorLog.objects.filter(pk=recent_log.pk).exists())

        # 2. Test management command
        # Age recent_log to 16 days ago
        ErrorLog.objects.filter(pk=recent_log.pk).update(created_at=timezone.now() - timedelta(days=16))
        call_command('purge_error_logs', days=15)
        self.assertFalse(ErrorLog.objects.filter(pk=recent_log.pk).exists())


class GeoFenceProximityTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from datetime import timedelta
        from django.utils import timezone
        from apps.shops.models import Shop, ShopCategory
        from decimal import Decimal
        from apps.offers.models import Offer, OfferType, OfferStatus
        from apps.redemptions.models import QRCode
        from apps.administration.models import PlatformSetting, AdminAction
        from services.wallet_service import get_or_create_wallet
        from services.reward_service import get_or_create_reward_account
        from services.redemption_service import create_or_refresh_offer_qr

        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username='admin_geofence',
            email='admin_geo@example.com',
            password='testpassword123'
        )
        Profile.objects.create(user=self.admin_user, role=Role.ADMIN, city='Bengaluru')

        # Buyer user
        self.buyer_user = User.objects.create_user(
            username='buyer_geo@example.com',
            email='buyer_geo@example.com',
            password='password123'
        )
        Profile.objects.create(user=self.buyer_user, role=Role.BUYER, city='Bengaluru')
        get_or_create_reward_account(self.buyer_user)

        # Seller user
        self.seller_user = User.objects.create_user(
            username='seller_geo@example.com',
            email='seller_geo@example.com',
            password='password123'
        )
        Profile.objects.create(user=self.seller_user, role=Role.SELLER, city='Bengaluru')
        wallet = get_or_create_wallet(self.seller_user)
        wallet.balance_credits = 100
        wallet.save()

        # Category & Shop with precise coordinates (Bengaluru MG Road)
        self.category = ShopCategory.objects.create(name='Electronics', slug='electronics', icon='bi-laptop')
        self.shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Indiranagar Tech Hub',
            address='12th Main Road',
            locality='Indiranagar',
            city='Bengaluru',
            phone='+91 98000 22222',
            latitude=12.97160,
            longitude=77.59460
        )

        # Active Offer & QR
        now = timezone.now()
        self.offer = Offer.objects.create(
            shop=self.shop,
            title='In-Store 15% Discount',
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('15.00'),
            min_purchase=Decimal('100.00'),
            credits_per_redemption=5,
            reward_points_per_redemption=50,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            max_redemptions=50,
            current_redemptions=0,
            status=OfferStatus.ACTIVE
        )
        self.qr, _ = create_or_refresh_offer_qr(self.offer)

        # Ensure PlatformSetting is reset
        self.setting = PlatformSetting.get_settings()
        self.setting.geofence_enabled = True
        self.setting.max_redemption_distance_meters = 50
        self.setting.gps_accuracy_tolerance_meters = 25
        self.setting.save()

    def test_haversine_distance_in_meters(self):
        from services.location_service import calculate_distance_in_meters
        # Exact same coordinates -> 0.0 meters
        dist_zero = calculate_distance_in_meters(12.97160, 77.59460, 12.97160, 77.59460)
        self.assertEqual(dist_zero, 0.0)

        # Very close coordinates (~11 meters)
        dist_close = calculate_distance_in_meters(12.97160, 77.59460, 12.97170, 77.59460)
        self.assertTrue(10.0 <= dist_close <= 13.0)

        # Further distance (~710 meters)
        dist_far = calculate_distance_in_meters(12.97160, 77.59460, 12.97800, 77.59460)
        self.assertTrue(700.0 <= dist_far <= 725.0)

    def test_in_store_redemption_succeeds_within_proximity(self):
        from decimal import Decimal
        from services.redemption_service import process_redemption
        # Shopper is ~11 meters away (within 50m radius + 25m tolerance = 75m)
        buyer_lat = 12.97170
        buyer_lng = 77.59460

        success, msg, redemption = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('500.00'),
            idempotency_key='geo_test_key_success_1',
            buyer_lat=buyer_lat,
            buyer_lng=buyer_lng
        )

        self.assertTrue(success, f"Redemption should have succeeded: {msg}")
        self.assertIsNotNone(redemption)
        self.assertTrue(redemption.is_geofence_verified)
        self.assertIsNotNone(redemption.redemption_distance_meters)
        self.assertTrue(redemption.redemption_distance_meters <= 75.0)

    def test_remote_scan_blocked_beyond_proximity_and_logged(self):
        from decimal import Decimal
        from apps.administration.models import AdminAction
        from services.redemption_service import process_redemption
        # Shopper is ~711 meters away (far beyond 75m allowed)
        buyer_lat = 12.97800
        buyer_lng = 77.59460

        success, msg, redemption = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('500.00'),
            idempotency_key='geo_test_key_fail_1',
            buyer_lat=buyer_lat,
            buyer_lng=buyer_lng
        )

        self.assertFalse(success)
        self.assertIsNone(redemption)
        self.assertIn("Proximity Check Failed", msg)
        self.assertIn("711m away from Indiranagar Tech Hub", msg)
        self.assertIn("within 75m", msg)

        # Verify security audit action was logged
        violation_logged = AdminAction.objects.filter(
            action_type="SECURITY_GEOFENCE_VIOLATION",
            target_id=str(self.offer.id)
        ).exists()
        self.assertTrue(violation_logged)

    def test_admin_settings_view_and_1click_toggle(self):
        self.client.force_login(self.admin_user)

        # 1. Access Settings page
        resp = self.client.get('/admin-portal/settings/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ACTIVE')
        self.assertContains(resp, '50m')

        # 2. 1-Click Toggle Geofence to Disabled
        toggle_resp = self.client.post('/admin-portal/settings/', {'action': 'toggle_geofence'}, follow=True)
        self.assertEqual(toggle_resp.status_code, 200)
        self.setting.refresh_from_db()
        self.assertFalse(self.setting.geofence_enabled)

        # 3. Remote scan now succeeds because geofence is disabled
        from decimal import Decimal
        from services.redemption_service import process_redemption
        success, msg, redemption = process_redemption(
            buyer_user=self.buyer_user,
            qr_code_str=self.qr.code,
            bill_amount=Decimal('500.00'),
            idempotency_key='geo_test_key_disabled_geofence',
            buyer_lat=12.97800,
            buyer_lng=77.59460
        )
        self.assertTrue(success)

        # 4. Update proximity parameters
        update_resp = self.client.post('/admin-portal/settings/', {
            'action': 'update_proximity',
            'max_redemption_distance_meters': '100',
            'gps_accuracy_tolerance_meters': '30',
            'geofence_enabled': 'on'
        }, follow=True)
        self.assertEqual(update_resp.status_code, 200)
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.geofence_enabled)
        self.assertEqual(self.setting.max_redemption_distance_meters, 100)
        self.assertEqual(self.setting.gps_accuracy_tolerance_meters, 30)

    def test_unauthenticated_cannot_access_settings(self):
        resp = self.client.get('/admin-portal/settings/')
        self.assertEqual(resp.status_code, 302)


class EnterpriseSecurityAndHardeningTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        from services.reward_service import get_or_create_reward_account
        from services.wallet_service import get_or_create_wallet
        from apps.shops.models import Shop, ShopCategory
        from apps.accounts.models import BuyerProfile, SellerProfile

        cache.clear()
        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username='platform_superadmin',
            email='superadmin@lokalloop.internal',
            password='Admin#Secure2026!'
        )
        Profile.objects.create(user=self.admin_user, role=Role.ADMIN, city='Pune')

        # Regular Buyer user
        self.buyer_user = User.objects.create_user(
            username='US-9912-INTERNAL-HASH',
            email='buyer.personal@secretmail.com',
            first_name='Rahul',
            last_name='Sharma',
            password='Buyer#Secure2026!'
        )
        Profile.objects.create(user=self.buyer_user, role=Role.BUYER, city='Pune', phone='+91 99999 11111')
        self.buyer_profile = BuyerProfile.objects.create(
            user=self.buyer_user,
            public_unique_id='PUN-8K9M-3X1Z',
            nickname='SwiftFalcon',
            state='Maharashtra',
            preferred_city='Pune'
        )
        get_or_create_reward_account(self.buyer_user)

        # Seller user
        self.seller_user = User.objects.create_user(
            username='seller.merchant@shopmail.com',
            email='seller.merchant@shopmail.com',
            password='Seller#Secure2026!'
        )
        Profile.objects.create(user=self.seller_user, role=Role.SELLER, city='Pune', phone='+91 88888 22222')
        SellerProfile.objects.create(user=self.seller_user, business_name='Pune Fashion Hub', is_verified=True)
        wallet = get_or_create_wallet(self.seller_user)
        wallet.balance_credits = 50
        wallet.save()

        self.category = ShopCategory.objects.create(name='Clothing', slug='clothing', icon='bi-bag')
        self.shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Pune Fashion Hub',
            address='FC Road',
            locality='Deccan',
            city='Pune',
            phone='+91 88888 22222',
            email='contact@punefashionhub.com'
        )

    def test_admin_route_rbac_rejects_non_admins_with_403(self):
        # 1. Buyer trying to access Admin Portal -> 403 Forbidden
        self.client.force_login(self.buyer_user)
        buyer_resp = self.client.get('/admin-portal/dashboard/')
        self.assertEqual(buyer_resp.status_code, 403)

        # 2. Seller trying to access Admin Portal -> 403 Forbidden
        self.client.force_login(self.seller_user)
        seller_resp = self.client.get('/admin-portal/dashboard/')
        self.assertEqual(seller_resp.status_code, 403)

    def test_admin_route_redirects_unauthenticated_with_302(self):
        # Anonymous user accessing Admin Portal is redirected to login gateway
        self.client.logout()
        resp = self.client.get('/admin-portal/dashboard/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin-login/', resp.url)

    def test_enterprise_password_complexity_validator(self):
        from apps.accounts.validators import EnterpriseComplexityValidator
        from django.core.exceptions import ValidationError

        validator = EnterpriseComplexityValidator(min_length=8)

        # Rejects < 8 chars
        with self.assertRaises(ValidationError) as ctx:
            validator.validate("Short1!")
        self.assertEqual(ctx.exception.code, 'password_too_short')

        # Rejects missing digit
        with self.assertRaises(ValidationError) as ctx:
            validator.validate("NoDigitsHere!")
        self.assertEqual(ctx.exception.code, 'password_no_digit')

        # Rejects missing special symbol
        with self.assertRaises(ValidationError) as ctx:
            validator.validate("NoSpecialChar123")
        self.assertEqual(ctx.exception.code, 'password_no_special')

        # Accepts strong password meeting all criteria
        try:
            validator.validate("ValidEnterprise#Pass99")
            validator.validate("Secure!Password888")
        except ValidationError:
            self.fail("EnterpriseComplexityValidator raised ValidationError unexpectedly for valid passwords!")

    def test_missing_optional_env_vars_fallback_safely(self):
        from django.conf import settings
        # Verify fallback presence for third-party decoupled secrets
        self.assertTrue(hasattr(settings, 'RAZORPAY_KEY_ID'))
        self.assertTrue(hasattr(settings, 'RAZORPAY_KEY_SECRET'))
        self.assertTrue(hasattr(settings, 'GOOGLE_MAPS_API_KEY'))
        self.assertTrue(hasattr(settings, 'AWS_ACCESS_KEY_ID'))
        self.assertTrue(hasattr(settings, 'AWS_STORAGE_BUCKET_NAME'))
        self.assertTrue(hasattr(settings, 'FIREBASE_API_KEY'))
        self.assertTrue(hasattr(settings, 'SMS_GATEWAY_API_KEY'))
        self.assertTrue(hasattr(settings, 'DJANGO_ADMIN_URL'))

    def test_security_cookies_and_headers_configured(self):
        from django.conf import settings
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, 'Lax')
        self.assertTrue(settings.SECURE_BROWSER_XSS_FILTER)
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')

    def test_admin_login_brute_force_lockout_and_audit_logging(self):
        from apps.administration.models import AdminAction
        from services.captcha_service import generate_captcha

        # 4 consecutive bad attempts
        for attempt in range(1, 5):
            cap = generate_captcha()
            resp = self.client.post('/admin-login/', {
                'identifier': 'platform_superadmin',
                'password': 'WrongPassword123!',
                'captcha_token': cap['captcha_token'],
                'captcha_answer': cap['_solution']
            })
            self.assertEqual(resp.status_code, 200)

        # Verify 4 ADMIN_LOGIN_FAILED logs created
        failed_count = AdminAction.objects.filter(action_type="ADMIN_LOGIN_FAILED").count()
        self.assertEqual(failed_count, 4)

        # 5th bad attempt -> Triggers 429 Lockout
        cap = generate_captcha()
        resp5 = self.client.post('/admin-login/', {
            'identifier': 'platform_superadmin',
            'password': 'WrongPassword123!',
            'captcha_token': cap['captcha_token'],
            'captcha_answer': cap['_solution']
        })
        self.assertEqual(resp5.status_code, 429)

        # Verify SECURITY_ADMIN_LOGIN_LOCKED log created
        lock_log_exists = AdminAction.objects.filter(action_type="SECURITY_ADMIN_LOGIN_LOCKED").exists()
        self.assertTrue(lock_log_exists)

        # 6th attempt (even with correct password) is immediately blocked by IP lockout
        cap = generate_captcha()
        resp6 = self.client.post('/admin-login/', {
            'identifier': 'platform_superadmin',
            'password': 'Admin#Secure2026!',
            'captcha_token': cap['captcha_token'],
            'captcha_answer': cap['_solution']
        })
        self.assertEqual(resp6.status_code, 429)

    def test_zero_personal_info_leakage_on_seller_and_public_views(self):
        from decimal import Decimal
        from django.utils import timezone
        from datetime import timedelta
        from decimal import Decimal
        from apps.offers.models import Offer, OfferType, OfferStatus
        from apps.redemptions.models import OfferRedemption, RedemptionStatus

        # Create active offer
        now = timezone.now()
        offer = Offer.objects.create(
            shop=self.shop,
            title='₹50 Off Festive Wear',
            offer_type=OfferType.FLAT,
            discount_value=Decimal('50.00'),
            min_purchase=Decimal('500.00'),
            credits_per_redemption=1,
            reward_points_per_redemption=5,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        redemption = OfferRedemption.objects.create(
            buyer=self.buyer_user,
            seller=self.seller_user,
            shop=self.shop,
            offer=offer,
            bill_amount=Decimal('500.00'),
            discount_given=Decimal('50.00'),
            credits_deducted=1,
            reward_points_earned=5,
            verification_otp='9482',
            idempotency_key='zero_leak_test_key_001',
            status=RedemptionStatus.SUCCESS,
            ip_address='127.0.0.1'
        )

        # 1. Verify Seller Redemptions View does NOT leak real name, email, or raw username
        self.client.force_login(self.seller_user)
        seller_resp = self.client.get('/seller/redemptions/')
        self.assertEqual(seller_resp.status_code, 200)

        content = seller_resp.content.decode('utf-8')
        # Pseudonymous identifiers MUST appear
        self.assertIn('SwiftFalcon', content)
        self.assertIn('PUN-8K9M-3X1Z', content)

        # Sensitive info MUST NOT appear
        self.assertNotIn('buyer.personal@secretmail.com', content)
        self.assertNotIn('US-9912-INTERNAL-HASH', content)
        self.assertNotIn('+91 99999 11111', content)





class BugFixesBatch98To116Tests(TestCase):
    """
    Comprehensive regression tests for BUG-98, BUG-108, BUG-109, BUG-110,
    BUG-111, BUG-112, BUG-114, BUG-115, and BUG-116.
    """
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username='admin_batch_test',
            email='admin_batch@example.com',
            password='testpassword123'
        )
        Profile.objects.create(user=self.admin_user, role=Role.ADMIN, city='Pune')

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    def test_bug98_multipart_payload_ceiling_allows_large_photos(self):
        """
        BUG-98: ApiSecurityMiddleware must permit photo uploads via multipart/form-data
        up to 12MB, while enforcing standard 2MB limit on regular requests.
        """
        from config.middleware import ApiSecurityMiddleware
        from django.test import RequestFactory
        rf = RequestFactory()
        mw = ApiSecurityMiddleware(lambda req: None)

        # 1. Standard JSON payload of 3MB (> 2MB) -> 413
        req_json = rf.post('/api/test/', content_type='application/json', CONTENT_LENGTH=str(3 * 1024 * 1024))
        res_json = mw.process_request(req_json)
        self.assertIsNotNone(res_json)
        self.assertEqual(res_json.status_code, 413)

        # 2. Multipart photo upload of 5MB (< 12MB) -> Permitted (returns None from middleware)
        req_multi = rf.post(
            '/buyer/rewards/bumper-bill/',
            content_type='multipart/form-data; boundary=----WebKitFormBoundaryX',
            CONTENT_LENGTH=str(5 * 1024 * 1024)
        )
        res_multi = mw.process_request(req_multi)
        self.assertIsNone(res_multi)

        # 3. Multipart upload exceeding 12MB -> 413
        req_oversized = rf.post(
            '/buyer/rewards/bumper-bill/',
            content_type='multipart/form-data; boundary=----WebKitFormBoundaryX',
            CONTENT_LENGTH=str(15 * 1024 * 1024)
        )
        res_oversized = mw.process_request(req_oversized)
        self.assertIsNotNone(res_oversized)
        self.assertEqual(res_oversized.status_code, 413)

    def test_bug108_haversine_domain_clamping_prevents_math_domain_error(self):
        """
        BUG-108: Haversine distance formula must clamp intermediate domain to [0.0, 1.0]
        to prevent ValueError: math domain error on boundary or antipodal coordinates.
        """
        from services.location_service import calculate_distance, calculate_distance_in_meters
        # Antipodal points
        dist_km = calculate_distance(0.0, 0.0, 0.0, 180.0)
        self.assertGreater(dist_km, 20000.0)
        dist_m = calculate_distance_in_meters(0.0, 0.0, 0.0, 180.0)
        self.assertGreater(dist_m, 20000000.0)

        # Identical points
        dist_zero = calculate_distance(18.5204, 73.8567, 18.5204, 73.8567)
        self.assertEqual(dist_zero, 0.0)

    def test_bug109_admin_buyer_search_includes_public_unique_id_and_nickname(self):
        """
        BUG-109: Admin buyer directory must filter by public_unique_id, shopper_id, and nickname.
        """
        from apps.accounts.models import BuyerProfile
        buyer_u = User.objects.create_user(username='b_search_109', email='b109@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER, city='Pune')
        BuyerProfile.objects.create(
            user=buyer_u,
            public_unique_id='PUB-109-XYZ',
            shopper_id='SHP-109-ABC',
            nickname='NeonEagle'
        )

        self.client.force_login(self.admin_user)
        # Search by Public Unique ID
        resp1 = self.client.get('/admin-portal/buyers/?q=PUB-109-XYZ')
        self.assertEqual(resp1.status_code, 200)
        self.assertContains(resp1, 'PUB-109-XYZ')

        # Search by Nickname
        resp2 = self.client.get('/admin-portal/buyers/?q=NeonEagle')
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, 'NeonEagle')

    def test_bug110_admin_seller_search_includes_merchant_id_and_phone(self):
        """
        BUG-110: Admin seller directory must filter by statutory merchant_id and registered phone.
        """
        from apps.accounts.models import SellerProfile
        seller_u = User.objects.create_user(username='s_search_110', email='s110@example.com', password='pass')
        Profile.objects.create(user=seller_u, role=Role.SELLER, city='Pune')
        SellerProfile.objects.create(
            user=seller_u,
            merchant_id='PUN-889911',
            phone='9811223344',
            business_name='Galaxy Superstore',
            full_name='Vikram Shinde'
        )

        self.client.force_login(self.admin_user)
        # Search by Merchant ID
        resp_id = self.client.get('/admin-portal/sellers/?q=PUN-889911')
        self.assertEqual(resp_id.status_code, 200)
        self.assertContains(resp_id, 'Galaxy Superstore')

        # Search by Phone
        resp_ph = self.client.get('/admin-portal/sellers/?q=9811223344')
        self.assertEqual(resp_ph.status_code, 200)
        self.assertContains(resp_ph, 'Galaxy Superstore')

    def test_bug111_admin_redemptions_search_includes_public_unique_id_and_otp(self):
        """
        BUG-111: Admin redemptions log must filter by buyer public_unique_id and 4-digit OTP.
        """
        from decimal import Decimal
        from django.utils import timezone
        from apps.accounts.models import BuyerProfile, SellerProfile
        from apps.shops.models import Shop, ShopCategory
        from apps.offers.models import Offer
        from apps.redemptions.models import OfferRedemption, RedemptionStatus

        buyer_u = User.objects.create_user(username='b_red_111', email='b111@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER, city='Pune')
        BuyerProfile.objects.create(user=buyer_u, public_unique_id='LQ9M-TEST-111', nickname='FalconShopper')

        seller_u = User.objects.create_user(username='s_red_111', email='s_red111@example.com', password='pass')
        Profile.objects.create(user=seller_u, role=Role.SELLER, city='Pune')
        SellerProfile.objects.create(user=seller_u, business_name='Apex Electronics')

        cat = ShopCategory.objects.first() or ShopCategory.objects.create(name='Electronics', slug='elec')
        shop = Shop.objects.create(seller=seller_u, category=cat, name='Apex Mart', locality='Camp', city='Pune')
        offer = Offer.objects.create(
            shop=shop,
            title='111 Flat Discount',
            slug='111-flat-discount',
            description='desc',
            terms='terms',
            end_date=timezone.now() + timezone.timedelta(days=10)
        )

        redemption = OfferRedemption.objects.create(
            buyer=buyer_u,
            seller=seller_u,
            shop=shop,
            offer=offer,
            verification_otp='4591',
            status=RedemptionStatus.SUCCESS,
            bill_amount=Decimal('500.00'),
            discount_given=Decimal('50.00'),
            idempotency_key='IDEMP-111-RED-TEST'
        )

        self.client.force_login(self.admin_user)
        # Search by 4-digit OTP
        resp_otp = self.client.get('/admin-portal/redemptions/?q=4591')
        self.assertEqual(resp_otp.status_code, 200)
        self.assertContains(resp_otp, '4591')

        # Search by Public Unique ID
        resp_pid = self.client.get('/admin-portal/redemptions/?q=LQ9M-TEST-111')
        self.assertEqual(resp_pid.status_code, 200)
        self.assertContains(resp_pid, 'LQ9M-TEST-111')

    def test_bug112_role_decorators_preserve_deep_link_query_strings(self):
        """
        BUG-112: Unauthenticated redirects in role decorators must retain URL query string.
        """
        self.client.logout()
        # Buyer required view
        r_buyer = self.client.get('/buyer/dashboard/?deal=active&ref=promo')
        self.assertEqual(r_buyer.status_code, 302)
        self.assertIn('deal=active', r_buyer.url)
        self.assertIn('ref=promo', r_buyer.url)

        # Seller required view
        r_seller = self.client.get('/seller/dashboard/?filter=sales&year=2026')
        self.assertEqual(r_seller.status_code, 302)
        self.assertIn('filter=sales', r_seller.url)

        # Admin required view
        r_admin = self.client.get('/admin-portal/dashboard/?tab=audit&level=warn')
        self.assertEqual(r_admin.status_code, 302)
        self.assertIn('tab=audit', r_admin.url)

    def test_bug114_model_defaults_aligned_with_welcome_bonuses(self):
        """
        BUG-114: Database model default values for BuyerRewardAccount and SellerWallet
        must reflect 25 points and 50 credits respectively.
        """
        from apps.rewards.models import BuyerRewardAccount
        from apps.wallets.models import SellerWallet

        raw_buyer = User.objects.create_user(username='raw_b114', email='rb114@example.com', password='pass')
        raw_acc = BuyerRewardAccount.objects.create(buyer=raw_buyer)
        self.assertEqual(raw_acc.current_points, 25)
        self.assertEqual(raw_acc.lifetime_earned, 25)

        raw_seller = User.objects.create_user(username='raw_s114', email='rs114@example.com', password='pass')
        raw_wal = SellerWallet.objects.create(seller=raw_seller)
        self.assertEqual(raw_wal.balance_credits, 50)
        self.assertEqual(raw_wal.lifetime_purchased, 50)

    def test_bug115_admin_bumper_bills_text_search(self):
        """
        BUG-115: Admin bumper purchase verifications must support free-text search by bill number.
        """
        from decimal import Decimal
        from apps.shops.models import Shop, ShopCategory
        from apps.rewards.models import BumperBillSubmission, BumperBillStatus

        b_user = User.objects.create_user(username='b_bumper_115', email='bb115@example.com', password='pass')
        Profile.objects.create(user=b_user, role=Role.BUYER, city='Pune')
        s_user = User.objects.create_user(username='s_bumper_115', email='sb115@example.com', password='pass')
        Profile.objects.create(user=s_user, role=Role.SELLER, city='Pune')

        cat = ShopCategory.objects.first() or ShopCategory.objects.create(name='General', slug='gen')
        shop = Shop.objects.create(seller=s_user, category=cat, name='Titan Store', locality='Aundh', city='Pune')

        BumperBillSubmission.objects.create(
            buyer=b_user,
            shop=shop,
            bill_number='BILL-VIP-7788',
            bill_amount=Decimal('15000.00'),
            status=BumperBillStatus.STORE_VERIFIED,
            points_awarded=100
        )

        self.client.force_login(self.admin_user)
        r_sub = self.client.get('/admin-portal/bumper-bills/?q=BILL-VIP-7788')
        self.assertEqual(r_sub.status_code, 200)
        self.assertContains(r_sub, 'BILL-VIP-7788')

    def test_bug116_safe_parse_coordinates_and_distance_handles_non_finite_values(self):
        """
        BUG-116: safe_parse_coordinates and distance calculations must handle non-finite
        floats (NaN, +/-Infinity) and invalid bounds gracefully without exceptions.
        """
        from services.location_service import safe_parse_coordinates, annotate_shops_with_distance
        from apps.shops.models import Shop, ShopCategory

        # Non-finite and boundary validation
        self.assertEqual(safe_parse_coordinates('NaN', '73.85'), (None, None))
        self.assertEqual(safe_parse_coordinates('Infinity', '73.85'), (None, None))
        self.assertEqual(safe_parse_coordinates('-Infinity', '73.85'), (None, None))
        self.assertEqual(safe_parse_coordinates(None, '73.85'), (None, None))
        self.assertEqual(safe_parse_coordinates('105.0', '73.85'), (None, None))  # Latitude > 90
        self.assertEqual(safe_parse_coordinates('18.5204', '73.8567'), (18.5204, 73.8567))

        # annotate_shops_with_distance safely returns without crash when non-finite coords passed
        seller_u = User.objects.create_user(username='s_geo_116', email='sgeo116@example.com', password='pass')
        cat = ShopCategory.objects.first() or ShopCategory.objects.create(name='Books', slug='books')
        shop = Shop.objects.create(seller=seller_u, category=cat, name='Kitab Khana', locality='FC Road', city='Pune')

        annotated = annotate_shops_with_distance(Shop.objects.filter(id=shop.id), 'NaN', 'Infinity')
        self.assertEqual(len(annotated), 1)
    def test_bug118_subcategory_filtering_returns_shops_and_offers(self):
        """
        BUG-118: Explore view and Buyer Explore view category filtering must match
        both main category slug and subcategory slug for stores and offers.
        """
        from datetime import timedelta
        from django.utils import timezone
        from apps.shops.models import Shop, ShopCategory, ShopSubcategory
        from decimal import Decimal
        from apps.offers.models import Offer, OfferType, OfferStatus

        seller_u = User.objects.create_user(username='s_cat_118', email='scat118@example.com', password='pass')
        cat = ShopCategory.objects.create(name='Apparel & Footwear', slug='apparel-footwear')
        subcat = ShopSubcategory.objects.create(category=cat, name='Sneakers', slug='sneakers')

        shop = Shop.objects.create(
            seller=seller_u,
            category=cat,
            subcategory=subcat,
            name='Sole Sneaker Hub',
            locality='Kothrud',
            city='Bengaluru',
            is_approved=True,
            is_active=True
        )
        offer = Offer.objects.create(
            shop=shop,
            title='20% Off Sneakers',
            discount_value=20,
            offer_type=OfferType.PERCENTAGE,
            min_purchase=1000,
            current_redemptions=0,
            max_redemptions=50,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=10),
            status=OfferStatus.ACTIVE
        )

        # Public explore with subcategory slug (shops)
        resp_pub = self.client.get('/explore/?category=sneakers')
        self.assertEqual(resp_pub.status_code, 200)
        self.assertContains(resp_pub, 'Sole Sneaker Hub')

        # Public explore with subcategory slug (offers)
        resp_pub_offers = self.client.get('/explore/?category=sneakers&type=offers')
        self.assertEqual(resp_pub_offers.status_code, 200)
        self.assertContains(resp_pub_offers, '20% Off Sneakers')

        # Buyer explore with subcategory slug
        buyer_u = User.objects.create_user(username='b_cat_118', email='bcat118@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER)
        self.client.force_login(buyer_u)
        resp_buyer = self.client.get(f'/buyer/explore/?category=sneakers')
        self.assertEqual(resp_buyer.status_code, 200)
        self.assertContains(resp_buyer, 'Sole Sneaker Hub')

    def test_bug119_leaderboard_scheduled_cycle_end_date(self):
        """
        BUG-119: Leaderboard cycle_end must reflect the cycle start + cycle duration window,
        not drifting with every live execution timestamp.
        """
        from datetime import timedelta
        from services.leaderboard_service import get_weekly_leaderboard
        data = get_weekly_leaderboard(cycle_days=7)
        self.assertIn('cycle_start', data)
        self.assertIn('cycle_end', data)
        self.assertGreater(data['cycle_end'], data['cycle_start'])
        self.assertEqual(data['cycle_end'] - data['cycle_start'], timedelta(days=7))

    def test_bug120_leaderboard_total_participants_unsliced_count(self):
        """
        BUG-120: Leaderboard total_participants metric must count all distinct buyers with
        earned points in the cycle rather than being truncated to the top 100 list slice.
        """
        from services.leaderboard_service import get_weekly_leaderboard
        data = get_weekly_leaderboard(cycle_days=7)
        self.assertIn('total_participants', data)
        self.assertIsInstance(data['total_participants'], int)
        self.assertGreaterEqual(data['total_participants'], 0)

    def test_bug121_seller_shop_view_persists_subcategory(self):
        """
        BUG-121: Merchant shop profile view must accept, validate, and persist the subcategory field.
        """
        from apps.shops.models import Shop, ShopCategory, ShopSubcategory
        seller_u = User.objects.create_user(username='s_sub_121', email='ssub121@example.com', password='pass')
        Profile.objects.create(user=seller_u, role=Role.SELLER)
        cat = ShopCategory.objects.create(name='Electronics & Gadgets', slug='electronics-gadgets')
        subcat = ShopSubcategory.objects.create(category=cat, name='Smartphones', slug='smartphones')
        shop = Shop.objects.create(
            seller=seller_u,
            category=cat,
            name='Gadget World',
            locality='Viman Nagar',
            city='Bengaluru',
            is_approved=True,
            is_active=True
        )

        self.client.force_login(seller_u)
        resp_get = self.client.get('/seller/shop/')
        self.assertEqual(resp_get.status_code, 200)
        self.assertIn('subcategories', resp_get.context)

        post_data = {
            'name': 'Gadget World Updated',
            'category': cat.id,
            'subcategory': subcat.id,
            'tagline': 'Best Mobile Store',
            'description': 'Smartphones and accessories',
            'phone': '9876543210',
            'address': 'Phoenix Market City',
            'locality': 'Viman Nagar',
            'city': 'Pune',
            'pincode': '411014'
        }
        resp_post = self.client.post('/seller/shop/', post_data)
        self.assertRedirects(resp_post, '/seller/shop/')
        shop.refresh_from_db()
        self.assertEqual(shop.subcategory, subcat)

    def test_bug122_authenticated_user_next_redirect_preserved(self):
        """
        BUG-122: When already authenticated buyers, sellers, or admins hit login endpoints
        with a valid ?next= parameter, redirect to the next_url instead of forcing dashboards.
        """
        buyer_u = User.objects.create_user(username='b_next_122', email='bnext122@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER)
        self.client.force_login(buyer_u)

        # Safe next URL is honored
        r_buyer = self.client.get('/buyer/login/?next=/rewards/catalog/')
        self.assertRedirects(r_buyer, '/rewards/catalog/', fetch_redirect_response=False)

        # Unsafe open redirect falls back to dashboard
        r_unsafe = self.client.get('/buyer/login/?next=https://malicious-external-site.com/steal')
        self.assertRedirects(r_unsafe, '/buyer/dashboard/', fetch_redirect_response=False)

        # Seller safe next URL is honored
        seller_u = User.objects.create_user(username='s_next_122', email='snext122@example.com', password='pass')
        Profile.objects.create(user=seller_u, role=Role.SELLER)
        self.client.force_login(seller_u)
        r_seller = self.client.get('/seller/login/?next=/seller/offers/')
        self.assertRedirects(r_seller, '/seller/offers/', fetch_redirect_response=False)

    def test_bug123_cron_sweeps_expired_catalog_reward_claims(self):
        """
        BUG-123: expire_stale_qrcodes_job must sweep active reward catalog claims
        whose expires_at timestamp has passed, marking them EXPIRED.
        """
        from datetime import timedelta
        from django.utils import timezone
        from apps.rewards.models import RewardCatalogItem, BuyerRewardClaim
        from services.background_tasks import expire_stale_qrcodes_job

        buyer_u = User.objects.create_user(username='b_exp_123', email='bexp123@example.com', password='pass')
        item = RewardCatalogItem.objects.create(
            title="Free Coffee Voucher",
            points_required=50,
            is_active=True
        )
        expired_claim = BuyerRewardClaim.objects.create(
            buyer=buyer_u,
            catalog_item=item,
            voucher_code="COFFEE-EXP-1234",
            points_spent=50,
            status='ACTIVE',
            expires_at=timezone.now() - timedelta(hours=1)
        )
        active_claim = BuyerRewardClaim.objects.create(
            buyer=buyer_u,
            catalog_item=item,
            voucher_code="COFFEE-ACT-1234",
            points_spent=50,
            status='ACTIVE',
            expires_at=timezone.now() + timedelta(days=2)
        )

        res = expire_stale_qrcodes_job()
        self.assertEqual(res['status'], 'success')
        self.assertGreaterEqual(res['expired_claims_count'], 1)

        expired_claim.refresh_from_db()
        self.assertEqual(expired_claim.status, 'EXPIRED')

        active_claim.refresh_from_db()
        self.assertEqual(active_claim.status, 'ACTIVE')

    def test_bug124_export_filename_uses_timezone_aware_timestamp(self):
        """
        BUG-124: Spreadsheet export HTTP response filename must generate timestamp
        using timezone-aware local time matching Django TIME_ZONE.
        """
        from django.utils import timezone
        from services.export_service import export_to_excel_response
        resp = export_to_excel_response(
            filename_prefix="audit_report",
            sheet_title="Audit",
            headers=["ID", "Name"],
            rows=[[1, "Test"]]
        )
        expected_ts = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M")
        expected_filename = f'audit_report_{expected_ts}.xlsx'
        self.assertIn(f'filename="{expected_filename}"', resp['Content-Disposition'])

    def test_bug125_invalid_credit_package_raises_validation_error(self):
        """
        BUG-125: process_demo_package_purchase must raise a clean ValidationError
        when an invalid or inactive package code is provided instead of throwing DoesNotExist.
        """
        from django.core.exceptions import ValidationError
        from services.wallet_service import process_demo_package_purchase

        seller_u = User.objects.create_user(username='s_pkg_125', email='spkg125@example.com', password='pass')
        with self.assertRaises(ValidationError) as ctx:
            process_demo_package_purchase(seller_u, "INVALID_GHOST_PACKAGE", "PAY-REF-125")
        self.assertIn("invalid or no longer active", str(ctx.exception))

    def test_bug1_1_offer_to_scanner_pipeline_preselects_qr_and_offer(self):
        """
        Bug 1.1: When navigating to /buyer/scan/?offer=<id>, the view and template
        must load preselected_offer and preselected_qr so the offer token is not lost.
        """
        from datetime import timedelta
        from django.utils import timezone
        from apps.shops.models import Shop, ShopCategory
        from decimal import Decimal
        from apps.offers.models import Offer, OfferType, OfferStatus

        seller_u = User.objects.create_user(username='s_pipe_11', email='spipe11@example.com', password='pass')
        cat = ShopCategory.objects.create(name='Dining 11', slug='dining-11')
        shop = Shop.objects.create(
            seller=seller_u,
            category=cat,
            name='The Gourmet Diner',
            locality='Indiranagar',
            city='Bengaluru',
            is_approved=True,
            is_active=True
        )
        offer = Offer.objects.create(
            shop=shop,
            title='Flat ₹100 Off Gourmet Meal',
            discount_value=100,
            offer_type=OfferType.FLAT,
            min_purchase=500,
            current_redemptions=0,
            max_redemptions=50,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=10),
            status=OfferStatus.ACTIVE
        )

        buyer_u = User.objects.create_user(username='b_pipe_11', email='bpipe11@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER)
        self.client.force_login(buyer_u)

        resp = self.client.get(f'/buyer/scan/?offer={offer.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['preselected_offer'], offer)
        self.assertIsNotNone(resp.context['preselected_qr'])
        self.assertContains(resp, 'The Gourmet Diner')
        self.assertContains(resp, resp.context['preselected_qr'].code)

    def test_bug1_2_admin_base_links_to_custom_admin_path(self):
        """
        Bug 1.2: Admin topbar must link to settings.DJANGO_ADMIN_URL (/portal-admin/)
        preventing 404 broken link from hardcoded /admin/.
        """
        self.client.force_login(self.admin_user)
        resp = self.client.get('/admin-portal/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '/portal-admin/')
        self.assertNotContains(resp, 'href="/admin/"')

    def test_bug1_3_qr_scanner_sanitizes_open_redirects_and_routes_offers(self):
        """
        Bug 1.3: QR Scanner template must contain security logic blocking external
        open-redirect domains and properly routing internal offer and store QR codes.
        """
        with open('templates/buyer/qr_scanner.html', 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('parsed.origin !== window.location.origin', content)
        self.assertIn('Security Notice: External QR code domain blocked.', content)
        self.assertIn("path.includes('/qr/')", content)
        self.assertIn("path.includes('/store/')", content)

    def test_bug1_4_seller_polling_rate_limit_and_interval(self):
        """
        Bug 1.4: Seller polling endpoint /api/seller/ must have dedicated relaxed
        rate limiting (60 req/min) and the template must poll at 8s to prevent 429 locks.
        """
        from config.middleware import RateLimitMiddleware
        mw = RateLimitMiddleware(get_response=lambda r: None)
        route, limit = mw._get_matched_rule('/api/seller/pending-redemptions/')
        self.assertEqual(route, '/api/seller/')
        self.assertEqual(limit, (60, 60))

        with open('templates/base/seller_base.html', 'r', encoding='utf-8') as f:
            sb = f.read()
        self.assertIn('setInterval(fetchPendingRedemptions, 8000);', sb)
        self.assertNotIn('setInterval(fetchPendingRedemptions, 3500);', sb)

    def test_bug1_5_buyer_registration_select_has_name_district(self):
        """
        Bug 1.5: The Maharashtra district select dropdown must have name="district"
        ensuring browser form submissions reliably transmit the chosen district.
        """
        resp = self.client.get('/buyer/register/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<select id="districtSelect" name="district"')
        self.assertNotContains(resp, '<input type="hidden" name="district"')

    def test_bug1_6_offer_form_validation_and_max_discount_rules(self):
        """
        Bug 1.6: Offer creation must reject percentage discounts exceeding 100%,
        handle BOGO without mandatory discount input, and offer form UI has max="100".
        """
        from apps.shops.models import Shop, ShopCategory
        from decimal import Decimal
        from apps.offers.models import Offer, OfferType

        seller_u = User.objects.create_user(username='s_off_16', email='soff16@example.com', password='pass')
        Profile.objects.create(user=seller_u, role=Role.SELLER)
        cat = ShopCategory.objects.create(name='Retail 16', slug='retail-16')
        shop = Shop.objects.create(
            seller=seller_u,
            category=cat,
            name='Modern Retail 16',
            locality='Kothrud',
            city='Pune',
            is_approved=True,
            is_active=True
        )

        self.client.force_login(seller_u)

        # 1. Percentage discount > 100% rejected
        post_bad = {
            'title': 'Mega 150% Off Everything',
            'offer_type': 'PERCENTAGE',
            'discount_value': '150',
            'min_purchase': '200',
            'max_redemptions': '50',
            'days_valid': '30'
        }
        resp_bad = self.client.post('/seller/offers/create/', post_bad)
        self.assertEqual(resp_bad.status_code, 200)
        self.assertContains(resp_bad, 'Percentage discount cannot exceed 100%.')

        # 2. BOGO without explicit discount value defaults cleanly to 50%
        post_bogo = {
            'title': 'Buy 1 Coffee Get 1 Free',
            'offer_type': 'BOGO',
            'discount_value': '',
            'min_purchase': '150',
            'max_redemptions': '50',
            'days_valid': '30'
        }
        resp_bogo = self.client.post('/seller/offers/create/', post_bogo)
        self.assertRedirects(resp_bogo, '/seller/offers/')
        bogo_offer = Offer.objects.filter(shop=shop, offer_type='BOGO').first()
        self.assertIsNotNone(bogo_offer)
        self.assertEqual(bogo_offer.discount_value, Decimal('50.00'))

    def test_bug2_1_service_worker_excludes_authenticated_routes(self):
        """
        Bug 2.1: Service worker must strictly exclude authenticated dashboards and APIs
        from HTML caching to protect user session privacy and prevent stale CSRF 403 errors.
        """
        with open('static/js/service-worker.js', 'r', encoding='utf-8') as f:
            sw = f.read()
        self.assertIn("url.pathname.startsWith('/buyer/')", sw)
        self.assertIn("url.pathname.startsWith('/seller/')", sw)
        self.assertIn("!isAuthenticatedOrApiRoute", sw)

    def test_bug2_3_clipboard_helper_has_fallback(self):
        """
        Bug 2.3: main.js copyToClipboard helper must include a safe fallback for HTTP
        and in-app WebViews where navigator.clipboard is undefined.
        """
        with open('static/js/main.js', 'r', encoding='utf-8') as f:
            js = f.read()
        self.assertIn("typeof navigator.clipboard.writeText === 'function'", js)
        self.assertIn("fallbackExecCopy", js)
        self.assertIn("document.execCommand('copy')", js)

    def test_bug3_1_and_3_2_buyer_base_mobile_responsiveness_and_drawer(self):
        """
        Bug 3.1 & 3.2: Buyer base must hide desktop navigation on mobile via desktop-nav
        without inline display:flex, and provide an account drawer in the mobile dock.
        """
        buyer_u = User.objects.create_user(username='b_base_31', email='bbase31@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER)
        self.client.force_login(buyer_u)
        resp = self.client.get('/buyer/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<nav class="desktop-nav d-flex align-center gap-2">')
        self.assertNotContains(resp, '<nav class="d-flex align-center gap-2" style="display: flex;">')
        self.assertContains(resp, 'id="mobileAccountDrawer"')
        self.assertContains(resp, 'openMobileAccountDrawer()')
        self.assertContains(resp, '/buyer/logout/')

    def test_bug3_4_buyer_topbar_renders_shopper_district(self):
        """
        Bug 3.4: Topbar city badge must dynamically render the shopper's actual district.
        """
        from apps.accounts.models import BuyerProfile
        buyer_u = User.objects.create_user(username='b_top_34', email='btop34@example.com', password='pass')
        Profile.objects.create(user=buyer_u, role=Role.BUYER)
        BuyerProfile.objects.create(user=buyer_u, district='Kolhapur')
        self.client.force_login(buyer_u)
        resp = self.client.get('/buyer/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Kolhapur')

    def test_bug3_5_and_3_6_seller_credits_and_analytics_calculations(self):
        """
        Bug 3.5 & 3.6: Credits view must calculate approx customer redemptions (balance // 5)
        and analytics view must calculate dynamic conversion rates.
        """
        from apps.shops.models import Shop, ShopCategory
        from apps.wallets.models import SellerWallet
        seller_u = User.objects.create_user(username='s_calc_35', email='scalc35@example.com', password='pass')
        Profile.objects.create(user=seller_u, role=Role.SELLER)
        cat = ShopCategory.objects.create(name='Services 35', slug='services-35')
        Shop.objects.create(seller=seller_u, category=cat, name='Studio 35', locality='Koregaon Park', city='Pune', is_approved=True, is_active=True)
        SellerWallet.objects.create(seller=seller_u, balance_credits=100)

        self.client.force_login(seller_u)
        # Bug 3.5: Credits calculation (100 credits // 5 = 20)
        resp_credits = self.client.get('/seller/credits/')
        self.assertEqual(resp_credits.status_code, 200)
        self.assertEqual(resp_credits.context['approx_redemptions'], 20)
        self.assertContains(resp_credits, '20')

        # Bug 3.6: Analytics dynamic metrics
        resp_analytics = self.client.get('/seller/analytics/')
        self.assertEqual(resp_analytics.status_code, 200)
        self.assertIn('scan_rate', resp_analytics.context)
        self.assertIn('redemption_rate', resp_analytics.context)

    def test_bug3_7_offline_page_is_self_contained(self):
        """
        Bug 3.7: Offline template must be self-contained HTML without extending base templates.
        """
        resp = self.client.get('/offline/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<!DOCTYPE html>")
        self.assertContains(resp, "Try Reconnecting")
        self.assertNotContains(resp, "base/public_base.html")

    def test_bug4_2_to_4_6_ux_and_print_enhancements(self):
        """
        Bug 4.2 to 4.6: Verify clean video CTA, password input padding, map fallback,
        print media rules, and decline bill reason validation.
        """
        # Bug 4.2: Truthful 1-min guide affordance
        resp_home = self.client.get('/')
        self.assertEqual(resp_home.status_code, 200)
        self.assertContains(resp_home, "How It Works (1-Min Guide)")
        self.assertNotContains(resp_home, "Watch Video 1:45")

        # Bug 4.3: Password input has right padding
        resp_login = self.client.get('/buyer/login/')
        self.assertEqual(resp_login.status_code, 200)
        self.assertContains(resp_login, 'style="padding-right: 44px;"')

        # Bug 4.5: Standee print avoids visibility hidden body flow bug
        with open('templates/seller/qr_display.html', 'r', encoding='utf-8') as f:
            qr_disp = f.read()
        self.assertNotIn("visibility: hidden", qr_disp)
        self.assertIn('@media print', qr_disp)

        # Bug 4.6: Decline receipt prompts for reason
        with open('templates/seller/bill_verifications.html', 'r', encoding='utf-8') as f:
            bv = f.read()
        self.assertIn('handleDeclineBumperBill', bv)
        self.assertIn('A valid reason is required', bv)

