from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.accounts.models import Profile, Role, BuyerProfile, SellerProfile
from apps.shops.models import Shop, ShopCategory, ShopSubcategory, ShopVerification
from apps.notifications.models import Notification
from apps.accounts.shopper_utils import (
    generate_unique_shopper_id,
    validate_strong_password,
    get_district_prefix
)
from services.captcha_service import (
    generate_captcha,
    validate_captcha,
    generate_challenge,
    create_captcha_token,
    create_test_slider_payload
)
import json


def get_test_captcha(is_dark=False):
    c = generate_captcha(is_dark=is_dark)
    return {
        'captcha_token': c['captcha_token'],
        'captcha_answer': c['_solution'],
    }


class AnonymousBuyerAuthTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_district_prefix_and_shopper_id_generation(self):
        prefix_pune = get_district_prefix("Pune")
        self.assertEqual(prefix_pune, "PUN")

        prefix_mum = get_district_prefix("Mumbai")
        self.assertEqual(prefix_mum, "MUM")

        prefix_blr = get_district_prefix("Bengaluru Urban")
        self.assertEqual(prefix_blr, "BLR")

        sid = generate_unique_shopper_id("Pune")
        self.assertTrue(sid.startswith("PUN-"))
        self.assertTrue(len(sid) >= 8)

    def test_strong_password_validator(self):
        # Too short
        valid, msg = validate_strong_password("Short1!")
        self.assertFalse(valid)

        # Missing uppercase
        valid, msg = validate_strong_password("lowercase123!")
        self.assertFalse(valid)

        # Missing symbol
        valid, msg = validate_strong_password("NoSpecial123")
        self.assertFalse(valid)

        # Valid strong password
        valid, msg = validate_strong_password("StrongPass123!")
        self.assertTrue(valid)

    def test_shopper_id_ajax_api(self):
        response = self.client.get('/api/shopper-id/?district=Pune')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['shopper_id'].startswith("PUN-"))
        self.assertEqual(data['district'], "Pune")

    def test_anonymous_registration_and_login_and_recovery(self):
        # 1. Register with anonymous district ID
        reg_payload = {
            'state': 'Maharashtra',
            'district': 'Pune',
            'shopper_id': 'PUN-9876',
            'password': 'SecurePassword123!',
            'confirm_password': 'SecurePassword123!',
            'security_question': 'What is your favorite regional dish/food?',
            'security_answer': 'Misal Pav',
            'website': '',  # Honeypot empty
            **get_test_captcha(),
        }
        res = self.client.post('/buyer/register/', reg_payload, follow=True)
        self.assertEqual(res.status_code, 200)

        # Verify user & profile created
        user = User.objects.get(username='PUN-9876')
        self.assertIsNotNone(user)
        self.assertEqual(user.profile.role, Role.BUYER)
        self.assertEqual(user.buyer_profile.shopper_id, 'PUN-9876')
        self.assertEqual(user.buyer_profile.district, 'Pune')
        self.assertEqual(user.buyer_profile.state, 'Maharashtra')
        self.assertTrue(bool(user.buyer_profile.security_answer_hash))

        # Check reward points credited (+25)
        self.assertEqual(user.reward_account.current_points, 25)

        # 2. Logout
        self.client.logout()

        # 3. Standard Login with Shopper ID
        login_res = self.client.post('/buyer/login/', {
            'identifier': 'PUN-9876',
            'password': 'SecurePassword123!',
            **get_test_captcha(),
        }, follow=True)
        self.assertEqual(login_res.status_code, 200)
        self.assertTrue(login_res.context['user'].is_authenticated)

        # 4. Logout and Test Account Recovery
        self.client.logout()

        # Step 1 lookup
        lookup_res = self.client.post('/buyer/recover/', {
            'action_lookup': '1',
            'shopper_id': 'PUN-9876',
            'website': '',
            **get_test_captcha(),
        })
        self.assertEqual(lookup_res.status_code, 200)
        self.assertEqual(lookup_res.context['step'], 2)

        # Step 2 reset with correct security answer (case insensitive)
        reset_res = self.client.post('/buyer/recover/', {
            'action_reset': '1',
            'shopper_id': 'PUN-9876',
            'security_answer': 'misal pav',
            'new_password': 'BrandNewPassword456!',
            'website': '',
        }, follow=True)
        self.assertEqual(reset_res.status_code, 200)

        # User is authenticated with new password
        self.assertTrue(reset_res.context['user'].is_authenticated)

        # Verify new password login
        self.client.logout()
        new_login = self.client.post('/buyer/login/', {
            'identifier': 'PUN-9876',
            'password': 'BrandNewPassword456!',
            **get_test_captcha(),
        }, follow=True)
        self.assertEqual(new_login.status_code, 200)
        self.assertTrue(new_login.context['user'].is_authenticated)


class SmartBuyerAlgorithmicIdAndGennieTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_age_calculation_and_age_band_mapping(self):
        import datetime
        from apps.accounts.shopper_utils import calculate_age, get_age_band_code

        ref_date = datetime.date(2026, 10, 15)
        
        # Birthday has passed this year
        age_passed = calculate_age(2004, 5, ref_date)
        self.assertEqual(age_passed, 22)

        # Birthday has NOT passed this year
        age_not_passed = calculate_age(2004, 12, ref_date)
        self.assertEqual(age_not_passed, 21)

        # Age band boundary checks
        self.assertEqual(get_age_band_code(14), 'H82')  # < 16
        self.assertEqual(get_age_band_code(15), 'H82')  # < 16
        self.assertEqual(get_age_band_code(16), 'Y77')  # 16 - 17
        self.assertEqual(get_age_band_code(17), 'Y77')  # 16 - 17
        self.assertEqual(get_age_band_code(18), 'O56')  # 18 - 25
        self.assertEqual(get_age_band_code(22), 'O56')  # 18 - 25
        self.assertEqual(get_age_band_code(25), 'O56')  # 18 - 25
        self.assertEqual(get_age_band_code(26), 'P95')  # 26 - 30
        self.assertEqual(get_age_band_code(30), 'P95')  # 26 - 30
        self.assertEqual(get_age_band_code(31), 'T54')  # 31 - 35
        self.assertEqual(get_age_band_code(35), 'T54')  # 31 - 35
        self.assertEqual(get_age_band_code(36), 'T58')  # 36 - 40
        self.assertEqual(get_age_band_code(40), 'T58')  # 36 - 40
        self.assertEqual(get_age_band_code(41), 'Z77')  # 41 - 45
        self.assertEqual(get_age_band_code(45), 'Z77')  # 41 - 45
        self.assertEqual(get_age_band_code(46), 'W45')  # 46 - 50
        self.assertEqual(get_age_band_code(50), 'W45')  # 46 - 50
        self.assertEqual(get_age_band_code(51), 'M22')  # 51 - 55
        self.assertEqual(get_age_band_code(55), 'M22')  # 51 - 55
        self.assertEqual(get_age_band_code(56), 'J65')  # 56 - 60
        self.assertEqual(get_age_band_code(60), 'J65')  # 56 - 60
        self.assertEqual(get_age_band_code(61), 'F45')  # 61+
        self.assertEqual(get_age_band_code(75), 'F45')  # 61+

    def test_internal_composite_code_generation(self):
        from apps.accounts.shopper_utils import generate_internal_composite_id

        # Pune + 22 yrs + Vikram -> A8208-O56-U4127-S2905
        comp_vikram = generate_internal_composite_id("Pune", 22, "Vikram")
        self.assertEqual(comp_vikram, "A8208-O56-U4127-S2905")

        # Mumbai City + 32 yrs + Anita ('A' -> V2506, 'a' -> V2506) -> I4278-T54-V2506-V2506
        comp_anita = generate_internal_composite_id("Mumbai City", 32, "Anita")
        self.assertEqual(comp_anita, "I4278-T54-V2506-V2506")

        # Nagpur + 16 yrs + Rohan ('R' -> F9890, 'n' -> W3490) -> D3249-Y77-F9890-W3490
        comp_rohan = generate_internal_composite_id("Nagpur", 16, "Rohan")
        self.assertEqual(comp_rohan, "D3249-Y77-F9890-W3490")

    def test_formatted_public_unique_id_generation(self):
        import re
        from apps.accounts.shopper_utils import generate_public_unique_id

        composite = "A8208-O56-U4127-S2905"
        pub_id = generate_public_unique_id(composite)
        
        # Format check: XXXX-XXXX-XXXX (12 chars + 2 hyphens = 14 total)
        self.assertEqual(len(pub_id), 14)
        self.assertTrue(bool(re.match(r'^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', pub_id)))

    def test_smart_registration_and_public_id_login_with_handset(self):
        reg_payload = {
            'first_name': 'Vikram',
            'birth_month': 5,
            'birth_year': 2004,
            'district': 'Pune',
            'state': 'Maharashtra',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'website': '',
            **get_test_captcha(),
        }
        res = self.client.post('/buyer/register/', reg_payload)
        # Should redirect to onboarding
        self.assertEqual(res.status_code, 302)
        self.assertIn('/buyer/onboarding/', res.url)

        # Handset token cookie should be set
        self.assertIn('ll_handset_token', res.cookies)

        # Query user
        buyer_profile = BuyerProfile.objects.filter(district='Pune', user__first_name='Vikram').first()
        self.assertIsNotNone(buyer_profile)
        self.assertEqual(buyer_profile.internal_composite_id, "A8208-O56-U4127-S2905")
        self.assertIsNotNone(buyer_profile.public_unique_id)
        pub_id = buyer_profile.public_unique_id

        # Logout
        self.client.logout()

        # Login with Public Unique ID (XXXX-XXXX-XXXX)
        login_res = self.client.post('/buyer/login/', {
            'identifier': pub_id,
            'password': 'StrongPassword123!',
            **get_test_captcha(),
        }, follow=True)
        self.assertEqual(login_res.status_code, 200)
        self.assertTrue(login_res.context['user'].is_authenticated)

    def test_gennie_nickname_onboarding_and_dashboard_greeting(self):
        # 1. Register a buyer
        reg_payload = {
            'first_name': 'Vikram',
            'birth_month': 1,
            'birth_year': 2000,
            'district': 'Pune',
            'state': 'Maharashtra',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'website': '',
            **get_test_captcha(),
        }
        self.client.post('/buyer/register/', reg_payload, follow=True)

        # 2. Access onboarding page
        onboarding_page = self.client.get('/buyer/onboarding/')
        self.assertEqual(onboarding_page.status_code, 200)
        self.assertContains(onboarding_page, "Welcome User")
        self.assertContains(onboarding_page, "Gennie wants to know your nickname to call you :)")

        # 3. Post Nickname via view
        set_res = self.client.post('/buyer/set-nickname/', {'nickname': 'Viki'}, follow=True)
        self.assertEqual(set_res.status_code, 200)

        buyer_user = User.objects.get(first_name='Vikram')
        self.assertEqual(buyer_user.buyer_profile.nickname, 'Viki')
        self.assertTrue(buyer_user.buyer_profile.is_nickname_set)

        # 4. Check greeting banner on Buyer Dashboard
        dash_res = self.client.get('/buyer/dashboard/')
        self.assertEqual(dash_res.status_code, 200)
        self.assertContains(dash_res, "✨ Welcome to the Gennie App Viki !!!")

        # 5. Check greeting banner on Buyer Explore
        explore_res = self.client.get('/buyer/explore/')
        self.assertEqual(explore_res.status_code, 200)
        self.assertContains(explore_res, "✨ Welcome to the Gennie App Viki !!!")

        # 6. Test AJAX nickname update
        ajax_res = self.client.post(
            '/buyer/set-nickname/',
            {'nickname': 'Vikstar'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(ajax_res.status_code, 200)
        self.assertEqual(ajax_res.json()['nickname'], 'Vikstar')


class HighSecurityEnterpriseProtectionTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()

    def test_security_headers_enforcement(self):
        res = self.client.get('/buyer/register/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(res.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(res.headers.get('X-XSS-Protection'), '1; mode=block')
        self.assertEqual(res.headers.get('Referrer-Policy'), 'strict-origin-when-cross-origin')
        self.assertIn('camera=(self)', res.headers.get('Permissions-Policy', ''))

    def test_login_brute_force_lockout_after_five_failed_attempts(self):
        # Trigger 5 failed login attempts
        for i in range(5):
            res = self.client.post('/buyer/login/', {
                'identifier': 'LOCKED-USER-001',
                'password': f'WrongPass{i}!',
                **get_test_captcha(),
            })
            self.assertEqual(res.status_code, 200)

        # 6th attempt must be locked out with HTTP 429
        lockout_res = self.client.post('/buyer/login/', {
            'identifier': 'LOCKED-USER-001',
            'password': 'AnyPassword123!',
            **get_test_captcha(),
        })
        self.assertEqual(lockout_res.status_code, 429)
        self.assertTrue(b"Too many attempts" in lockout_res.content or b"locked" in lockout_res.content)

    def test_demographic_hmac_tamper_proofing(self):
        from apps.accounts.shopper_utils import compute_composite_hmac, verify_composite_integrity
        comp_id = "A8208-O56-U4127-S2905"
        sig = compute_composite_hmac(comp_id)
        self.assertTrue(len(sig) >= 16)
        self.assertTrue(verify_composite_integrity(comp_id, sig))

        # Tampered composite ID must fail verification
        tampered_comp_id = "A8208-O56-U4127-S9999"
        self.assertFalse(verify_composite_integrity(tampered_comp_id, sig))

    def test_input_sanitization_and_xss_prevention(self):
        from apps.accounts.shopper_utils import sanitize_user_input
        dirty_input = "<script>alert('pwned')</script>Vikram"
        clean = sanitize_user_input(dirty_input, max_length=30)
        self.assertNotIn('<script>', clean)
        self.assertEqual(clean, "alert('pwned')Vikram")

        # Alphanumeric only
        alphanumeric_clean = sanitize_user_input("Vikram<script>alert(1)</script>!@#$", max_length=30, allow_alphanumeric_only=True)
        self.assertNotIn('<', alphanumeric_clean)
        self.assertNotIn('!', alphanumeric_clean)
        self.assertEqual(alphanumeric_clean, "Vikramalert1")

    def test_api_high_security_shield(self):
        # 1. Block malicious vulnerability scanner
        scanner_res = self.client.get('/api/shopper-id/', HTTP_USER_AGENT='sqlmap/1.6.4#stable')
        self.assertEqual(scanner_res.status_code, 403)
        self.assertEqual(scanner_res.json()['error'], 'Forbidden')

        # 2. Block SQL injection attempt in API query string
        sqli_res = self.client.get('/api/shopper-id/?district=union+select+1,2,3')
        self.assertEqual(sqli_res.status_code, 400)
        self.assertEqual(sqli_res.json()['error'], 'Bad Request')

        # 3. Block XSS script tag injection in API query string
        xss_res = self.client.get('/api/shopper-id/?district=<script>alert(1)</script>')
        self.assertEqual(xss_res.status_code, 400)
        self.assertEqual(xss_res.json()['error'], 'Bad Request')

        # 4. Valid API request has anti-caching & security headers
        valid_res = self.client.get('/api/shopper-id/?district=Pune')
        self.assertEqual(valid_res.status_code, 200)
        self.assertIn('no-store', valid_res.headers.get('Cache-Control', ''))
        self.assertEqual(valid_res.headers.get('X-Content-Type-Options'), 'nosniff')


class SelfHostedCaptchaSecurityTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()

    def test_captcha_generation_and_no_leakage(self):
        """
        Verify captcha bundle contains cryptographic token, SVG badge, and prompt.
        Verify that solution is NOT exposed in the token or SVG markup.
        """
        captcha = generate_captcha()
        self.assertIn('captcha_token', captcha)
        self.assertIn('challenge_svg', captcha)
        self.assertIn('challenge_prompt', captcha)
        parts = captcha['captcha_token'].split(':')
        self.assertEqual(len(parts), 3)

        # Solution MUST NOT be in the client token string
        sol = captcha['_solution']
        self.assertNotEqual(parts[0], sol)
        self.assertNotEqual(parts[1], sol)
        self.assertNotEqual(parts[2], sol)

        # SVG must be valid SVG markup and not contain the solution in plain data attributes
        self.assertIn('<svg', captcha['challenge_svg'])
        self.assertIn('</svg>', captcha['challenge_svg'])
        self.assertNotIn(f'solution="{sol}"', captcha['challenge_svg'])

    def test_correct_captcha_passes_validation(self):
        """
        Verify that submitting the correct answer passes validation.
        """
        captcha = generate_captcha()
        is_valid, error = validate_captcha(captcha['captcha_token'], captcha['_solution'])
        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    def test_wrong_captcha_fails_validation(self):
        """
        Verify that wrong answer fails validation with a clear error message.
        """
        captcha = generate_captcha()
        is_valid, error = validate_captcha(captcha['captcha_token'], "incorrect-answer-999")
        self.assertFalse(is_valid)
        self.assertIn("Incorrect security challenge answer", error)

    def test_replayed_captcha_fails_validation(self):
        """
        Verify anti-replay defense: single-use nonce is burned immediately.
        Submitting the same token a second time must fail.
        """
        captcha = generate_captcha()
        token = captcha['captcha_token']
        sol = captcha['_solution']

        # First use -> passes
        first_valid, first_err = validate_captcha(token, sol)
        self.assertTrue(first_valid)

        # Second use (replay attack) -> MUST fail
        second_valid, second_err = validate_captcha(token, sol)
        self.assertFalse(second_valid)
        self.assertIn("already been used", second_err)

    def test_expired_captcha_fails_validation(self):
        """
        Verify that a captcha token older than 5 minutes (or expired) is rejected.
        """
        # Create token expired 10 seconds ago
        prompt, display, sol = generate_challenge()
        expired_token = create_captcha_token(sol, expiry_seconds=-10)

        is_valid, error = validate_captcha(expired_token, sol)
        self.assertFalse(is_valid)
        self.assertIn("expired", error)

    def test_tampered_captcha_token_fails(self):
        """
        Verify that tampering with nonce or signature fails HMAC check.
        """
        captcha = generate_captcha()
        nonce, expiry, sig = captcha['captcha_token'].split(':')
        tampered_token = f"{nonce}:{expiry}:tamperedsignature1234567890abcdef"

        is_valid, error = validate_captcha(tampered_token, captcha['_solution'])
        self.assertFalse(is_valid)
        self.assertIn("Incorrect security challenge answer", error)

    def test_captcha_refresh_api(self):
        """
        Verify GET /api/captcha/refresh/ returns fresh token, SVG badge, and security headers.
        """
        response = self.client.get('/api/captcha/refresh/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('captcha_token', data)
        self.assertIn('challenge_svg', data)
        self.assertNotIn('_solution', data)  # NO solution leaked over API!
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')

    def test_buyer_login_with_correct_and_incorrect_captcha(self):
        """
        Integration test: verify buyer login form with captcha.
        """
        user = User.objects.create_user(username='TEST-BUYER', password='ValidPassword123!')
        Profile.objects.create(user=user, role=Role.BUYER)
        BuyerProfile.objects.create(user=user, shopper_id='TEST-BUYER', public_unique_id='TB99-1234-5678')

        # 1. Login with incorrect captcha -> should fail with captcha error
        wrong_c = generate_captcha()
        res_fail = self.client.post('/buyer/login/', {
            'identifier': 'TEST-BUYER',
            'password': 'ValidPassword123!',
            'captcha_token': wrong_c['captcha_token'],
            'captcha_answer': '999999',
        })
        self.assertEqual(res_fail.status_code, 200)
        self.assertIn('form', res_fail.context)
        self.assertTrue(res_fail.context['form'].errors.get('captcha_answer'))

        # 2. Login with correct captcha -> should succeed
        correct_c = generate_captcha()
        res_ok = self.client.post('/buyer/login/', {
            'identifier': 'TEST-BUYER',
            'password': 'ValidPassword123!',
            'captcha_token': correct_c['captcha_token'],
            'captcha_answer': correct_c['_solution'],
        }, follow=True)
        self.assertEqual(res_ok.status_code, 200)
        self.assertTrue(res_ok.context['user'].is_authenticated)

    def test_slider_instant_bot_velocity_rejected(self):
        """
        Verify that bot submissions with instantaneous drag speed (< 100ms) are blocked.
        """
        captcha = generate_captcha()
        bot_payload = json.dumps({
            'token': captcha['captcha_token'],
            't': 20,  # 20ms duration (inhumanly fast bot)
            'm': 10,
            'trail': [[0, 0, 0], [200, 0, 20]]
        })
        is_valid, error = validate_captcha(captcha['captcha_token'], bot_payload)
        self.assertFalse(is_valid)
        self.assertIn("dragged too quickly", error)

    def test_slider_zero_moves_bot_rejected(self):
        """
        Verify that bot submissions without movement trajectory samples are blocked.
        """
        captcha = generate_captcha()
        bot_payload = json.dumps({
            'token': captcha['captcha_token'],
            't': 500,
            'm': 0,
            'trail': []
        })
        is_valid, error = validate_captcha(captcha['captcha_token'], bot_payload)
        self.assertFalse(is_valid)
        self.assertIn("automated bot behavior detected", error)

    def test_slider_verify_api_endpoint(self):
        """
        Verify POST /api/captcha/verify-slider/ returns signed verified_token.
        """
        captcha = generate_captcha()
        valid_payload = {
            'captcha_token': captcha['captcha_token'],
            't': 450,
            'm': 12,
            'trail': [[0, 0, 0], [100, 0, 200], [260, 0, 450]]
        }
        response = self.client.post(
            '/api/captcha/verify-slider/',
            data=json.dumps(valid_payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('verified_token', data)

        # The returned verified_token must pass validate_captcha
        is_valid, err = validate_captcha(captcha['captcha_token'], data['verified_token'])
        self.assertTrue(is_valid)
        self.assertEqual(err, "")


class AccountPasswordChangeSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create standard test buyer
        self.buyer_user = User.objects.create_user(
            username='PUN-TEST-PWD',
            password='CurrentBuyerPassword123!'
        )
        self.buyer_profile = Profile.objects.create(
            user=self.buyer_user,
            role=Role.BUYER
        )
        self.buyer_account = BuyerProfile.objects.create(
            user=self.buyer_user,
            shopper_id='PUN-TEST-PWD',
            public_unique_id='PUN1-ABCD-EFGH'
        )

        # Create standard test seller
        self.seller_user = User.objects.create_user(
            username='seller_tester',
            email='seller_tester@lokalloop.in',
            password='CurrentSellerPassword123!'
        )
        self.seller_profile = Profile.objects.create(
            user=self.seller_user,
            role=Role.SELLER
        )
        self.seller_account = SellerProfile.objects.create(
            user=self.seller_user,
            business_name='Apex Electronics'
        )

    def test_unauthenticated_user_redirected_to_login(self):
        """
        Verify unauthenticated users cannot access change password views and get redirected.
        """
        buyer_res = self.client.get('/buyer/change-password/')
        self.assertEqual(buyer_res.status_code, 302)
        self.assertIn('/buyer/login/', buyer_res.url)

        seller_res = self.client.get('/seller/change-password/')
        self.assertEqual(seller_res.status_code, 302)
        self.assertIn('/seller/login/', seller_res.url)

    def test_buyer_password_change_success(self):
        """
        Verify valid current password and strong new password updates password,
        keeps session alive via update_session_auth_hash, and dispatches security alert notification.
        """
        self.client.login(username='PUN-TEST-PWD', password='CurrentBuyerPassword123!')

        # GET change password page
        get_res = self.client.get('/buyer/change-password/')
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, 'Account Security &amp; Password')

        # POST valid password change
        post_data = {
            'current_password': 'CurrentBuyerPassword123!',
            'new_password': 'NewSuperSecurePass456@',
            'confirm_password': 'NewSuperSecurePass456@',
        }
        post_res = self.client.post('/buyer/change-password/', post_data, follow=True)
        self.assertEqual(post_res.status_code, 200)

        # Verify password updated in DB
        self.buyer_user.refresh_from_db()
        self.assertTrue(self.buyer_user.check_password('NewSuperSecurePass456@'))
        self.assertFalse(self.buyer_user.check_password('CurrentBuyerPassword123!'))

        # Verify user remains logged in without session termination
        self.assertTrue(post_res.context['user'].is_authenticated)

        # Verify security alert notification created
        notif = Notification.objects.filter(
            recipient=self.buyer_user,
            title__icontains="Security Alert"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("changed successfully", notif.message)

    def test_seller_password_change_success(self):
        """
        Verify seller portal password change succeeds and updates merchant credentials.
        """
        self.client.login(username='seller_tester', password='CurrentSellerPassword123!')

        get_res = self.client.get('/seller/change-password/')
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, 'Merchant Account Security')

        post_data = {
            'current_password': 'CurrentSellerPassword123!',
            'new_password': 'NewMerchantSecret789#',
            'confirm_password': 'NewMerchantSecret789#',
        }
        post_res = self.client.post('/seller/change-password/', post_data, follow=True)
        self.assertEqual(post_res.status_code, 200)

        self.seller_user.refresh_from_db()
        self.assertTrue(self.seller_user.check_password('NewMerchantSecret789#'))
        self.assertFalse(self.seller_user.check_password('CurrentSellerPassword123!'))

        self.assertTrue(post_res.context['user'].is_authenticated)

        notif = Notification.objects.filter(
            recipient=self.seller_user,
            title__icontains="Security Alert"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("changed successfully", notif.message)

    def test_incorrect_current_password_fails(self):
        """
        Verify wrong current password triggers validation error and preserves old password.
        """
        self.client.login(username='PUN-TEST-PWD', password='CurrentBuyerPassword123!')

        post_data = {
            'current_password': 'WrongCurrentPassword999!',
            'new_password': 'NewValidPassword123$',
            'confirm_password': 'NewValidPassword123$',
        }
        res = self.client.post('/buyer/change-password/', post_data)
        self.assertEqual(res.status_code, 200)
        self.assertIn('form', res.context)
        self.assertIn('current_password', res.context['form'].errors)

        # Old password still intact
        self.buyer_user.refresh_from_db()
        self.assertTrue(self.buyer_user.check_password('CurrentBuyerPassword123!'))

    def test_mismatched_confirm_password_fails(self):
        """
        Verify non-matching confirm password triggers validation error.
        """
        self.client.login(username='PUN-TEST-PWD', password='CurrentBuyerPassword123!')

        post_data = {
            'current_password': 'CurrentBuyerPassword123!',
            'new_password': 'NewValidPassword123$',
            'confirm_password': 'DifferentPassword456%',
        }
        res = self.client.post('/buyer/change-password/', post_data)
        self.assertEqual(res.status_code, 200)
        self.assertIn('form', res.context)
        self.assertIn('confirm_password', res.context['form'].errors)

        self.buyer_user.refresh_from_db()
        self.assertTrue(self.buyer_user.check_password('CurrentBuyerPassword123!'))

    def test_weak_new_password_fails(self):
        """
        Verify password lacking digits or special characters is rejected.
        """
        self.client.login(username='PUN-TEST-PWD', password='CurrentBuyerPassword123!')

        # Missing symbol
        res1 = self.client.post('/buyer/change-password/', {
            'current_password': 'CurrentBuyerPassword123!',
            'new_password': 'NoSpecialCharacters123',
            'confirm_password': 'NoSpecialCharacters123',
        })
        self.assertEqual(res1.status_code, 200)
        self.assertIn('new_password', res1.context['form'].errors)

        # Too short (< 8 chars)
        res2 = self.client.post('/buyer/change-password/', {
            'current_password': 'CurrentBuyerPassword123!',
            'new_password': 'Short1!',
            'confirm_password': 'Short1!',
        })
        self.assertEqual(res2.status_code, 200)
        self.assertIn('new_password', res2.context['form'].errors)

        self.buyer_user.refresh_from_db()
        self.assertTrue(self.buyer_user.check_password('CurrentBuyerPassword123!'))

    def test_same_as_current_password_fails(self):
        """
        Verify setting new password identical to current password is rejected.
        """
        self.client.login(username='PUN-TEST-PWD', password='CurrentBuyerPassword123!')

        res = self.client.post('/buyer/change-password/', {
            'current_password': 'CurrentBuyerPassword123!',
            'new_password': 'CurrentBuyerPassword123!',
            'confirm_password': 'CurrentBuyerPassword123!',
        })
        self.assertEqual(res.status_code, 200)
        self.assertIn('form', res.context)
        self.assertIn('new_password', res.context['form'].errors)

    def test_cross_role_access_restricted(self):
        """
        Verify a buyer cannot access the seller change-password endpoint.
        """
        self.client.login(username='PUN-TEST-PWD', password='CurrentBuyerPassword123!')
        res = self.client.get('/seller/change-password/')
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, '/buyer/dashboard/')


class BuyerGenderRegistrationTests(TestCase):
    """
    Tests verifying Gender selection in the Buyer Registration form.
    """

    def setUp(self):
        self.client = Client()

    def test_buyer_registration_with_gender(self):
        cap = get_test_captcha()
        reg_payload = {
            'first_name': 'Aaradhya',
            'gender': 'FEMALE',
            'birth_month': 5,
            'birth_year': 1999,
            'state': 'Maharashtra',
            'district': 'Pune',
            'password': 'SecureP@ssword123!',
            'confirm_password': 'SecureP@ssword123!',
            'website': '',
            **cap
        }
        res = self.client.post('/buyer/register/', reg_payload, follow=True)
        self.assertEqual(res.status_code, 200)

        # Verify buyer profile saved gender as FEMALE
        buyer_profile = BuyerProfile.objects.filter(gender='FEMALE').first()
        self.assertIsNotNone(buyer_profile)
        self.assertEqual(buyer_profile.gender, 'FEMALE')
        self.assertEqual(buyer_profile.get_gender_display(), 'Female')
        self.assertEqual(buyer_profile.user.first_name, 'Aaradhya')

    def test_buyer_registration_template_renders_gender_field(self):
        res = self.client.get('/buyer/register/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'name="gender"')
        self.assertContains(res, 'value="MALE"')
        self.assertContains(res, 'value="FEMALE"')
        self.assertContains(res, 'value="OTHER"')
        self.assertContains(res, 'Gender')

    def test_buyer_registration_form_invalid_gender_rejected(self):
        from apps.accounts.forms import AnonymousBuyerRegistrationForm
        form = AnonymousBuyerRegistrationForm(data={
            'first_name': 'Vikram',
            'gender': 'INVALID_GENDER',
            'birth_month': 1,
            'birth_year': 2000,
            'password': 'SecureP@ssword123!',
            'confirm_password': 'SecureP@ssword123!',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('gender', form.errors)


class EnterpriseSellerMultiStepRegistrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Seed test categories and subcategories
        self.cat_grocery = ShopCategory.objects.create(
            name="Grocery Supermarket",
            icon="bi-basket",
            requires_fssai=True,
            requires_fda=False
        )
        self.sub_produce = ShopSubcategory.objects.create(
            category=self.cat_grocery,
            name="Fresh Produce"
        )
        self.cat_pharma = ShopCategory.objects.create(
            name="Healthcare & Pharmacy",
            icon="bi-heart-pulse",
            requires_fssai=False,
            requires_fda=True
        )
        self.sub_pharmacy = ShopSubcategory.objects.create(
            category=self.cat_pharma,
            name="Pharmacy Counter"
        )
        self.cat_lifestyle = ShopCategory.objects.create(
            name="Lifestyle & Trends",
            icon="bi-bag-heart",
            requires_fssai=False,
            requires_fda=False
        )
        self.sub_clothing = ShopSubcategory.objects.create(
            category=self.cat_lifestyle,
            name="Designer Clothes"
        )

        # Admin user for admin portal moderation tests
        self.admin_user = User.objects.create_superuser(
            username="admin@lokalloop.in",
            email="admin@lokalloop.in",
            password="AdminP@ssword123!"
        )
        Profile.objects.create(user=self.admin_user, role=Role.ADMIN)

    def test_seller_registration_valid_multi_step_submission(self):
        cap = get_test_captcha()
        payload = {
            'business_name': 'Kothrud Fresh Mart',
            'category': self.cat_grocery.id,
            'subcategory': self.sub_produce.id,
            'address': 'Shop 12, Golden Arcade, Paud Road',
            'locality': 'Kothrud',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411038',
            'latitude': '18.507400',
            'longitude': '73.807700',
            'operating_hours': '08:00 AM - 10:30 PM (All Days)',
            'display_phone': '+91 20 2544 9988',
            'full_name': 'Rameshwar Kulkarni',
            'designation': 'OWNER',
            'email': 'kothrud.mart@example.com',
            'phone': '9822199880',
            'password': 'StrongMerchantP@ss1!',
            'confirm_password': 'StrongMerchantP@ss1!',
            'shop_act_license_number': 'MH/PUN/2024/778899',
            'shop_act_valid_till': '2028-12-31',
            'fssai_license_number': '11521012345678',
            'fssai_valid_till': '2027-06-30',
            'fda_license_number': '',
            'fda_valid_till': '',
            'gst_number': '27ABCDE1234F1Z5',
            'business_pan': 'ABCDE1234F',
            'agreement_accepted': True,
            **cap
        }
        res = self.client.post('/seller/register/', payload, follow=True)
        self.assertEqual(res.status_code, 200)

        # 1. Verify User & SellerProfile created
        user = User.objects.get(email='kothrud.mart@example.com')
        self.assertEqual(user.first_name, 'Rameshwar Kulkarni')
        self.assertEqual(user.profile.role, Role.SELLER)
        self.assertEqual(user.seller_profile.business_name, 'Kothrud Fresh Mart')
        self.assertEqual(user.seller_profile.designation, 'OWNER')
        self.assertEqual(user.seller_profile.pan_number, 'ABCDE1234F')
        self.assertEqual(user.seller_profile.gstin, '27ABCDE1234F1Z5')
        self.assertTrue(user.seller_profile.merchant_id.startswith('PUN-'))
        self.assertFalse(user.seller_profile.is_verified)

        # 2. Verify Shop outlet created with geolocation and subcategory
        shop = Shop.objects.get(seller=user)
        self.assertEqual(shop.category, self.cat_grocery)
        self.assertEqual(shop.subcategory, self.sub_produce)
        self.assertEqual(shop.locality, 'Kothrud')
        self.assertEqual(float(shop.latitude), 18.5074)
        self.assertEqual(shop.display_phone, '+91 20 2544 9988')
        self.assertFalse(shop.is_approved)

        # 3. Verify ShopVerification record created with PENDING status and Valid Till dates
        verification = shop.verification
        self.assertEqual(verification.shop_act_license_number, 'MH/PUN/2024/778899')
        self.assertEqual(str(verification.shop_act_valid_till), '2028-12-31')
        self.assertEqual(verification.fssai_license_number, '11521012345678')
        self.assertEqual(str(verification.fssai_valid_till), '2027-06-30')
        self.assertEqual(verification.status, 'PENDING')

        # 4. Verify Merchant Digital Agreement Log created
        from apps.administration.models import MerchantAgreementLog
        agreement_log = MerchantAgreementLog.objects.filter(user=user).first()
        self.assertIsNotNone(agreement_log)
        self.assertEqual(agreement_log.merchant_id, user.seller_profile.merchant_id)
        self.assertTrue(agreement_log.is_accepted)

    def test_dynamic_subcategory_api(self):
        res = self.client.get(f'/api/subcategories/?category_id={self.cat_grocery.id}')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('subcategories', data)
        sub_names = [s['name'] for s in data['subcategories']]
        self.assertIn('Fresh Produce', sub_names)

    def test_pan_regex_validation(self):
        from apps.accounts.forms import SellerRegistrationForm
        base_data = {
            'business_name': 'Valid PAN Test Store',
            'category': self.cat_lifestyle.id,
            'address': 'MG Road',
            'locality': 'Camp',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411001',
            'full_name': 'Owner Person',
            'designation': 'OWNER',
            'email': 'validpan@example.com',
            'phone': '9890123456',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'shop_act_license_number': 'SHOPACT123',
            'agreement_accepted': True,
        }

        # 1. Invalid PAN (less than 10 chars, all numbers, wrong format)
        invalid_pans = ['INVALIDPAN', '12345ABCDE', 'ABC1234', 'ABCDE12345']
        for bad_pan in invalid_pans:
            form = SellerRegistrationForm(data={**base_data, 'business_pan': bad_pan, **get_test_captcha()})
            self.assertFalse(form.is_valid())
            self.assertIn('business_pan', form.errors)

        # 2. Valid PAN
        form_valid = SellerRegistrationForm(data={**base_data, 'business_pan': 'BCDEF5678G', **get_test_captcha()})
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        self.assertEqual(form_valid.cleaned_data['business_pan'], 'BCDEF5678G')

    def test_gstin_regex_validation(self):
        from apps.accounts.forms import SellerRegistrationForm
        base_data = {
            'business_name': 'GSTIN Test Store',
            'category': self.cat_lifestyle.id,
            'address': 'MG Road',
            'locality': 'Camp',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411001',
            'full_name': 'Owner Person',
            'designation': 'OWNER',
            'email': 'gsttest@example.com',
            'phone': '9890123457',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'shop_act_license_number': 'SHOPACT123',
            'business_pan': 'ABCDE1234F',
            'agreement_accepted': True,
        }

        # Invalid GSTIN
        form_bad_gst = SellerRegistrationForm(data={**base_data, 'gst_number': 'NOT_A_VALID_GST', **get_test_captcha()})
        self.assertFalse(form_bad_gst.is_valid())
        self.assertIn('gst_number', form_bad_gst.errors)

        # Valid GSTIN (e.g. 27ABCDE1234F1Z5)
        form_ok_gst = SellerRegistrationForm(data={**base_data, 'gst_number': '27ABCDE1234F1Z5', **get_test_captcha()})
        self.assertTrue(form_ok_gst.is_valid(), form_ok_gst.errors)
        self.assertEqual(form_ok_gst.cleaned_data['gst_number'], '27ABCDE1234F1Z5')

        # Empty GSTIN is allowed (optional if turnover exempt)
        form_empty_gst = SellerRegistrationForm(data={**base_data, 'gst_number': '', **get_test_captcha()})
        self.assertTrue(form_empty_gst.is_valid(), form_empty_gst.errors)

    def test_category_dependent_fssai_and_fda_enforcement(self):
        from apps.accounts.forms import SellerRegistrationForm
        base_data = {
            'business_name': 'Compliance Enforcement Store',
            'address': 'FC Road',
            'locality': 'Shivajinagar',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411005',
            'full_name': 'Owner Person',
            'designation': 'OWNER',
            'email': 'compliance@example.com',
            'phone': '9890123458',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'shop_act_license_number': 'SHOPACT123',
            'business_pan': 'ABCDE1234F',
            'agreement_accepted': True,
        }

        # 1. Grocery category WITHOUT FSSAI -> MUST fail
        form_fssai_missing = SellerRegistrationForm(data={
            **base_data,
            'category': self.cat_grocery.id,
            'fssai_license_number': '',
            **get_test_captcha()
        })
        self.assertFalse(form_fssai_missing.is_valid())
        self.assertIn('fssai_license_number', form_fssai_missing.errors)

        # 2. Grocery category WITH valid FSSAI and Valid Till -> passes
        form_fssai_ok = SellerRegistrationForm(data={
            **base_data,
            'category': self.cat_grocery.id,
            'fssai_license_number': '11521012345678',
            'fssai_valid_till': '2028-10-22',
            **get_test_captcha()
        })
        self.assertTrue(form_fssai_ok.is_valid(), form_fssai_ok.errors)

        # 3. Pharmacy category WITHOUT FDA -> MUST fail
        form_fda_missing = SellerRegistrationForm(data={
            **base_data,
            'category': self.cat_pharma.id,
            'fda_license_number': '',
            **get_test_captcha()
        })
        self.assertFalse(form_fda_missing.is_valid())
        self.assertIn('fda_license_number', form_fda_missing.errors)

        # 4. Pharmacy category WITH valid FDA and Valid Till -> passes
        form_fda_ok = SellerRegistrationForm(data={
            **base_data,
            'category': self.cat_pharma.id,
            'fda_license_number': 'MH-PUN-20B-123456',
            'fda_valid_till': '2028-10-22',
            **get_test_captcha()
        })
        self.assertTrue(form_fda_ok.is_valid(), form_fda_ok.errors)

    def test_past_valid_till_date_rejected(self):
        from apps.accounts.forms import SellerRegistrationForm
        base_data = {
            'business_name': 'Past Date Test Store',
            'category': self.cat_lifestyle.id,
            'address': 'FC Road',
            'locality': 'Shivajinagar',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411005',
            'full_name': 'Owner Person',
            'designation': 'OWNER',
            'email': 'pastdate@example.com',
            'phone': '9890123499',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'shop_act_license_number': 'SHOPACT123',
            'business_pan': 'ABCDE1234F',
            'agreement_accepted': True,
        }

        # Past date on shop_act_valid_till MUST fail
        form_past = SellerRegistrationForm(data={
            **base_data,
            'shop_act_valid_till': '2020-01-01',
            **get_test_captcha()
        })
        self.assertFalse(form_past.is_valid())
        self.assertIn('shop_act_valid_till', form_past.errors)
        self.assertIn('cannot be in the past', str(form_past.errors['shop_act_valid_till']))

    def test_fssai_16_digit_and_numeric_validation(self):
        from apps.accounts.forms import SellerRegistrationForm
        base_data = {
            'business_name': 'FSSAI Digit Test Store',
            'category': self.cat_grocery.id,
            'address': 'FC Road',
            'locality': 'Shivajinagar',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411005',
            'full_name': 'Owner Person',
            'designation': 'OWNER',
            'email': 'fssaidigits@example.com',
            'phone': '9890123498',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'shop_act_license_number': 'SHOPACT123',
            'business_pan': 'ABCDE1234F',
            'fssai_valid_till': '2028-10-22',
            'agreement_accepted': True,
        }

        # Non-numeric FSSAI MUST fail
        form_non_num = SellerRegistrationForm(data={
            **base_data,
            'fssai_license_number': '1152101234ABCD',
            **get_test_captcha()
        })
        self.assertFalse(form_non_num.is_valid())
        self.assertIn('fssai_license_number', form_non_num.errors)

        # > 16 digits MUST fail
        form_too_long = SellerRegistrationForm(data={
            **base_data,
            'fssai_license_number': '12345678901234567',
            **get_test_captcha()
        })
        self.assertFalse(form_too_long.is_valid())
        self.assertIn('fssai_license_number', form_too_long.errors)

        # Exactly 16 digits passes
        form_16_ok = SellerRegistrationForm(data={
            **base_data,
            'fssai_license_number': '1234567890123456',
            **get_test_captcha()
        })
        self.assertTrue(form_16_ok.is_valid(), form_16_ok.errors)

    def test_duplicate_email_and_phone_rejected(self):
        from apps.accounts.forms import SellerRegistrationForm
        # Existing seller
        existing_user = User.objects.create_user(username='existing@example.com', email='existing@example.com', password='Password123!')
        Profile.objects.create(user=existing_user, role=Role.SELLER, phone='9890111222')
        SellerProfile.objects.create(user=existing_user, business_name='Old Shop', phone='9890111222')

        base_data = {
            'business_name': 'Duplicate Test Store',
            'category': self.cat_lifestyle.id,
            'address': 'Deccan',
            'locality': 'Deccan',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411004',
            'full_name': 'New Owner',
            'designation': 'OWNER',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'shop_act_license_number': 'SHOPACT123',
            'business_pan': 'ABCDE1234F',
            'agreement_accepted': True,
        }

        # 1. Duplicate email -> fails
        form_dup_email = SellerRegistrationForm(data={
            **base_data,
            'email': 'existing@example.com',
            'phone': '9890999888',
            **get_test_captcha()
        })
        self.assertFalse(form_dup_email.is_valid())
        self.assertIn('email', form_dup_email.errors)

        # 2. Duplicate phone -> fails
        form_dup_phone = SellerRegistrationForm(data={
            **base_data,
            'email': 'newunique@example.com',
            'phone': '9890111222',
            **get_test_captcha()
        })
        self.assertFalse(form_dup_phone.is_valid())
        self.assertIn('phone', form_dup_phone.errors)

    def test_admin_category_and_verification_management(self):
        self.client.login(username="admin@lokalloop.in", password="AdminP@ssword123!")

        # 1. Admin creates a new dynamic category
        res_cat = self.client.post('/admin-portal/categories/', {
            'action': 'create_category',
            'name': 'Home & Kitchen Decor',
            'icon': 'bi-house-heart',
            'requires_fssai': '',
            'requires_fda': ''
        }, follow=True)
        self.assertEqual(res_cat.status_code, 200)
        new_cat = ShopCategory.objects.get(name='Home & Kitchen Decor')
        self.assertTrue(new_cat.is_active)

        # 2. Admin creates dynamic subcategory
        res_sub = self.client.post('/admin-portal/categories/', {
            'action': 'create_subcategory',
            'category_id': new_cat.id,
            'name': 'Ceramic Cookware'
        }, follow=True)
        self.assertEqual(res_sub.status_code, 200)
        self.assertTrue(ShopSubcategory.objects.filter(category=new_cat, name='Ceramic Cookware').exists())

        # 3. Admin toggles category
        self.client.post(f'/admin-portal/categories/{new_cat.id}/toggle/')
        new_cat.refresh_from_db()
        self.assertFalse(new_cat.is_active)

        # 4. Admin reviews ShopVerification record
        shop = Shop.objects.create(
            seller=self.admin_user,
            category=new_cat,
            name='Test Moderated Store',
            address='Paud Road',
            locality='Kothrud',
            city='Pune',
            phone='9800011111'
        )
        verification = ShopVerification.objects.create(
            shop=shop,
            shop_act_license_number='MH/PUN/2024/9900',
            business_pan='ABCDE1234F',
            status='PENDING'
        )

        res_ver_list = self.client.get('/admin-portal/verifications/')
        self.assertEqual(res_ver_list.status_code, 200)

        # Approve verification
        res_approve = self.client.post(f'/admin-portal/verifications/{verification.id}/update/', {
            'status': 'APPROVED',
            'admin_notes': 'All municipal documents validated.'
        })
        self.assertEqual(res_approve.status_code, 302)
        verification.refresh_from_db()
        self.assertEqual(verification.status, 'APPROVED')
        self.assertTrue(verification.shop.is_approved)


class MerchantIdGenerationAndGatedAccessTests(TestCase):
    """
    Automated tests verifying:
    1. Unique City-Code Merchant ID generation rules & collision handling
    2. Registration defaults (is_verified=False, shop.is_approved=False, ShopVerification.status='PENDING')
    3. Gated access protection: Unapproved sellers cannot access gated actions
    4. Admin approval workflow: Unlocks features, sets is_verified=True, and credits 100 starter points
    """
    def setUp(self):
        from apps.accounts.utils import extract_city_code, generate_unique_merchant_id
        self.extract_city_code = extract_city_code
        self.generate_unique_merchant_id = generate_unique_merchant_id

        # Admin user
        self.admin = User.objects.create_superuser(
            username="platform_admin@lokalloop.in",
            email="platform_admin@lokalloop.in",
            password="AdminP@ssword123!"
        )
        Profile.objects.create(user=self.admin, role=Role.ADMIN)

        # Category for shops
        self.category = ShopCategory.objects.create(name="Retail & Grocery", icon="bi-shop", is_active=True)

    def test_city_code_extraction_rules(self):
        # 1. Single-Word Cities: first 3 letters uppercase
        self.assertEqual(self.extract_city_code('Pune'), 'PUN')
        self.assertEqual(self.extract_city_code('Akola'), 'AKO')
        self.assertEqual(self.extract_city_code('Mumbai'), 'MUM')
        self.assertEqual(self.extract_city_code('Nagpur'), 'NAG')
        self.assertEqual(self.extract_city_code('Nashik'), 'NAS')

        # 2. Multi-Word Cities: First letter of each word in uppercase
        self.assertEqual(self.extract_city_code('Chhatrapati Sambhaji Nagar'), 'CSN')
        self.assertEqual(self.extract_city_code('Navi Mumbai'), 'NMU')
        self.assertEqual(self.extract_city_code('New Delhi'), 'NDL')

        # 3. Fallback: blank/empty/None -> 'LOK'
        self.assertEqual(self.extract_city_code(''), 'LOK')
        self.assertEqual(self.extract_city_code('   '), 'LOK')
        self.assertEqual(self.extract_city_code(None), 'LOK')

    def test_generate_unique_merchant_id_collision_prevention(self):
        import re
        m_id = self.generate_unique_merchant_id('Pune')
        self.assertTrue(re.match(r'^PUN-\d{6}$', m_id))

        # Create seller with this ID to force collision on next candidate
        dummy_user = User.objects.create_user(username='dummy_merchant@test.com', password='Pass123!dummy')
        SellerProfile.objects.create(user=dummy_user, merchant_id=m_id, business_name='Collision Test Store')

        new_id = self.generate_unique_merchant_id('Pune')
        self.assertTrue(re.match(r'^PUN-\d{6}$', new_id))
        self.assertNotEqual(m_id, new_id)

    def test_unapproved_merchant_gated_access_redirects(self):
        seller_user = User.objects.create_user(
            username="pending_seller@test.com",
            email="pending_seller@test.com",
            password="SecurePassword123!"
        )
        Profile.objects.create(user=seller_user, role=Role.SELLER, city="Akola")
        m_id = self.generate_unique_merchant_id("Akola")
        SellerProfile.objects.create(
            user=seller_user,
            merchant_id=m_id,
            business_name="Akola Super Center",
            is_verified=False
        )
        shop = Shop.objects.create(
            seller=seller_user,
            category=self.category,
            name="Akola Super Center",
            address="Station Road",
            locality="Civil Lines",
            city="Akola",
            is_approved=False,
            is_active=False
        )
        ShopVerification.objects.create(
            shop=shop,
            shop_act_license_number="AKO/SHOP/2026/101",
            status="PENDING"
        )

        self.client.login(username="pending_seller@test.com", password="SecurePassword123!")

        # 1. Dashboard is accessible and displays Compliance Review banner & Merchant ID
        res_dash = self.client.get('/seller/dashboard/')
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "Application Under Compliance Review")
        self.assertContains(res_dash, m_id)

        # 2. Gated views redirect to /seller/dashboard/
        gated_urls = [
            '/seller/offers/create/',
            '/seller/offers/new/',
            '/seller/qr/',
            '/seller/redemptions/',
            '/seller/credits/buy/',
        ]
        for url in gated_urls:
            res = self.client.get(url, follow=True)
            self.assertRedirects(res, '/seller/dashboard/')
            self.assertContains(res, "This feature is locked while your account is pending verification.")

    def test_admin_approval_unlocks_features_and_grants_credits(self):
        seller_user = User.objects.create_user(
            username="approve_seller@test.com",
            email="approve_seller@test.com",
            password="SecurePassword123!"
        )
        Profile.objects.create(user=seller_user, role=Role.SELLER, city="Pune")
        m_id = self.generate_unique_merchant_id("Pune")
        seller_profile = SellerProfile.objects.create(
            user=seller_user,
            merchant_id=m_id,
            business_name="Viman Nagar Deli",
            is_verified=False
        )
        shop = Shop.objects.create(
            seller=seller_user,
            category=self.category,
            name="Viman Nagar Deli",
            address="Airport Road",
            locality="Viman Nagar",
            city="Pune",
            is_approved=False,
            is_active=False
        )
        verification = ShopVerification.objects.create(
            shop=shop,
            shop_act_license_number="PUN/SHOP/2026/888",
            business_pan="ABCDE5678G",
            status="PENDING"
        )

        # 1. Admin logs in and approves verification
        self.client.login(username="platform_admin@lokalloop.in", password="AdminP@ssword123!")
        res_approve = self.client.post(f'/admin-portal/verifications/{verification.id}/update/', {
            'status': 'APPROVED',
            'admin_notes': 'Documents verified and approved.'
        })
        self.assertEqual(res_approve.status_code, 302)

        # 2. Check updated states
        verification.refresh_from_db()
        shop.refresh_from_db()
        seller_profile.refresh_from_db()

        self.assertEqual(verification.status, 'APPROVED')
        self.assertTrue(shop.is_approved)
        self.assertTrue(shop.is_active)
        self.assertTrue(seller_profile.is_verified)
        self.assertIsNotNone(seller_profile.verified_at)
        self.assertEqual(seller_profile.merchant_id, m_id)

        # 3. Check 50 starter promotional credits granted
        self.assertEqual(seller_user.wallet.balance_credits, 50)

        # 4. Now seller can access gated views without redirect
        self.client.login(username="approve_seller@test.com", password="SecurePassword123!")
        res_create = self.client.get('/seller/offers/create/')
        self.assertEqual(res_create.status_code, 200)

        res_qr = self.client.get('/seller/qr/')
        self.assertEqual(res_qr.status_code, 200)

        res_redemptions = self.client.get('/seller/redemptions/')
        self.assertEqual(res_redemptions.status_code, 200)

    def test_admin_toggle_seller_approval_view(self):
        seller_user = User.objects.create_user(
            username="toggle_seller@test.com",
            email="toggle_seller@test.com",
            password="SecurePassword123!"
        )
        Profile.objects.create(user=seller_user, role=Role.SELLER, city="Nashik")
        m_id = self.generate_unique_merchant_id("Nashik")
        seller_profile = SellerProfile.objects.create(
            user=seller_user,
            merchant_id=m_id,
            business_name="Nashik Grape Mart",
            is_verified=False
        )
        shop = Shop.objects.create(
            seller=seller_user,
            category=self.category,
            name="Nashik Grape Mart",
            address="College Road",
            locality="College Road",
            city="Nashik",
            is_approved=False,
            is_active=False
        )
        verification = ShopVerification.objects.create(
            shop=shop,
            shop_act_license_number="NAS/SHOP/2026/777",
            status="PENDING"
        )

        self.client.login(username="platform_admin@lokalloop.in", password="AdminP@ssword123!")
        res_toggle = self.client.post(f'/admin-portal/sellers/{seller_profile.id}/toggle/')
        self.assertEqual(res_toggle.status_code, 302)

        seller_profile.refresh_from_db()
        shop.refresh_from_db()
        verification.refresh_from_db()

        self.assertTrue(seller_profile.is_verified)
        self.assertTrue(shop.is_approved)
        self.assertEqual(verification.status, 'APPROVED')
        self.assertEqual(seller_user.wallet.balance_credits, 50)
        self.assertEqual(seller_profile.merchant_id, m_id)


class SellerSecurityQuestionRecoveryTests(TestCase):
    def setUp(self):
        from apps.shops.models import ShopCategory
        from django.core.cache import cache
        cache.clear()
        self.cat = ShopCategory.objects.create(name="Groceries", slug="groceries")

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    def test_seller_model_set_and_check_security_answer_hashing(self):
        user = User.objects.create_user(username="test_merchant_qa@example.com", email="test_merchant_qa@example.com", password="Password123!")
        profile = SellerProfile.objects.create(
            user=user,
            business_name="Test Store",
            phone="9822001122",
            security_question="What is your favorite childhood street/place?"
        )
        profile.set_security_answer("Camp Pune")
        profile.save()

        # Plaintext must not be saved in database
        self.assertNotIn("Camp Pune", profile.security_answer_hash)
        self.assertTrue(profile.security_answer_hash.startswith("pbkdf2_sha256$") or profile.security_answer_hash.startswith("md5$"))

        # Case-insensitivity & whitespace normalization
        self.assertTrue(profile.check_security_answer("camp pune"))
        self.assertTrue(profile.check_security_answer("CAMP PUNE"))
        self.assertTrue(profile.check_security_answer("  Camp Pune  "))
        self.assertFalse(profile.check_security_answer("Mumbai"))
        self.assertFalse(profile.check_security_answer(""))

    def test_seller_registration_with_security_question(self):
        from apps.accounts.forms import SellerRegistrationForm
        reg_payload = {
            'business_name': 'Green Grocery Mart',
            'category': self.cat.id,
            'address': 'Main Road 45',
            'locality': 'Kothrud',
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411038',
            'full_name': 'Ganesh Kadam',
            'designation': 'OWNER',
            'email': 'ganesh.kadam@example.com',
            'phone': '9822334455',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'security_question': 'What was the name of your first school?',
            'security_answer': 'St. Vincent High School',
            'shop_act_license_number': 'PUN/SHOP/2026/001',
            'business_pan': 'ABCDE1234F',
            'agreement_accepted': True,
            **get_test_captcha(),
        }

        response = self.client.post('/seller/register/', reg_payload)
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(email='ganesh.kadam@example.com')
        self.assertTrue(hasattr(user, 'seller_profile'))
        self.assertEqual(user.seller_profile.security_question, 'What was the name of your first school?')
        self.assertTrue(user.seller_profile.check_security_answer('st. vincent high school'))
        self.assertNotIn('St. Vincent', user.seller_profile.security_answer_hash)

    def test_seller_recovery_flow_lookup_and_reset(self):
        # 1. Setup merchant
        user = User.objects.create_user(username="baker@example.com", email="baker@example.com", password="InitialPassword123!")
        profile = Profile.objects.create(user=user, role=Role.SELLER, phone="9890112233")
        seller_profile = SellerProfile.objects.create(
            user=user,
            merchant_id="PUN-998877",
            business_name="Pune Bakers",
            phone="9890112233",
            security_question="What is your favorite regional dish/food?"
        )
        seller_profile.set_security_answer("Misal Pav")
        seller_profile.save()

        # 2a. Lookup by Merchant ID
        lookup_res = self.client.post('/seller/recover/', {
            'action_lookup': '1',
            'identifier': 'PUN-998877',
            'website': '',
            **get_test_captcha(),
        })
        self.assertEqual(lookup_res.status_code, 200)
        self.assertEqual(lookup_res.context['step'], 2)
        self.assertIn("What is your favorite regional dish/food?", lookup_res.content.decode('utf-8'))

        # 2b. Lookup by Email
        lookup_email_res = self.client.post('/seller/recover/', {
            'action_lookup': '1',
            'identifier': 'baker@example.com',
            'website': '',
            **get_test_captcha(),
        })
        self.assertEqual(lookup_email_res.status_code, 200)
        self.assertEqual(lookup_email_res.context['step'], 2)

        # 2c. Lookup by Phone
        lookup_phone_res = self.client.post('/seller/recover/', {
            'action_lookup': '1',
            'identifier': '9890112233',
            'website': '',
            **get_test_captcha(),
        })
        self.assertEqual(lookup_phone_res.status_code, 200)
        self.assertEqual(lookup_phone_res.context['step'], 2)

        # 3. Step 2: Incorrect Answer fails
        wrong_res = self.client.post('/seller/recover/', {
            'action_reset': '1',
            'identifier': 'baker@example.com',
            'security_answer': 'Vada Pav',
            'new_password': 'NewStrongPassword456!',
            'confirm_password': 'NewStrongPassword456!',
            'website': '',
        })
        self.assertEqual(wrong_res.status_code, 200)
        self.assertFalse(user.check_password('NewStrongPassword456!'))
        self.assertIn('security_answer', wrong_res.context['reset_form'].errors)

        # 4. Step 2: Correct Answer succeeds
        correct_res = self.client.post('/seller/recover/', {
            'action_reset': '1',
            'identifier': 'baker@example.com',
            'security_answer': 'misal pav',
            'new_password': 'NewStrongPassword456!',
            'confirm_password': 'NewStrongPassword456!',
            'website': '',
        })
        self.assertEqual(correct_res.status_code, 302)
        self.assertTrue(correct_res['Location'].startswith('/seller/login/'))

        # 5. Verify authentication with new credentials
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewStrongPassword456!'))
        self.assertFalse(user.check_password('InitialPassword123!'))

    def test_seller_recovery_rate_limiting_lockout(self):
        user = User.objects.create_user(username="victim@example.com", email="victim@example.com", password="Password123!")
        Profile.objects.create(user=user, role=Role.SELLER, phone="9890554433")
        seller_profile = SellerProfile.objects.create(
            user=user,
            merchant_id="PUN-112233",
            business_name="Target Store",
            phone="9890554433",
            security_question="What is your favorite childhood street/place?"
        )
        seller_profile.set_security_answer("Secret Place")
        seller_profile.save()

        test_ip = "198.51.100.99"

        # 5 failed attempts
        for i in range(5):
            res = self.client.post('/seller/recover/', {
                'action_reset': '1',
                'identifier': 'victim@example.com',
                'security_answer': f'WrongGuess{i}',
                'new_password': 'NewStrongPassword123!',
                'confirm_password': 'NewStrongPassword123!',
                'website': '',
            }, REMOTE_ADDR=test_ip)
            self.assertEqual(res.status_code, 200)

        # 6th attempt -> HTTP 429 lockout
        lockout_res = self.client.post('/seller/recover/', {
            'action_reset': '1',
            'identifier': 'victim@example.com',
            'security_answer': 'Secret Place',
            'new_password': 'NewStrongPassword123!',
            'confirm_password': 'NewStrongPassword123!',
            'website': '',
        }, REMOTE_ADDR=test_ip)
        self.assertEqual(lockout_res.status_code, 429)
        self.assertIn("locked", lockout_res.content.decode('utf-8').lower())
    def test_buyer_recovery_by_public_unique_id_and_shopper_id(self):
        # Setup buyer with public unique ID and shopper ID
        buyer = User.objects.create_user(username="rohit_sharma", email="rohit@example.com", password="OldPassword123!")
        Profile.objects.create(user=buyer, role=Role.BUYER)
        bp = BuyerProfile.objects.create(
            user=buyer,
            public_unique_id="ROHIT-9988",
            shopper_id="PUN-5544",
            security_question="What is your favorite childhood street/place?"
        )
        bp.set_security_answer("FC Road")
        bp.save()

        # 1. Lookup by Public Unique ID
        res_pid = self.client.post('/buyer/recover/', {
            'action_lookup': '1',
            'shopper_id': 'ROHIT-9988',
            'website': '',
            **get_test_captcha(),
        })
        self.assertEqual(res_pid.status_code, 200)
        self.assertEqual(res_pid.context['step'], 2)

        # 2. Lookup by Shopper ID
        res_sid = self.client.post('/buyer/recover/', {
            'action_lookup': '1',
            'shopper_id': 'PUN-5544',
            'website': '',
            **get_test_captcha(),
        })
        self.assertEqual(res_sid.status_code, 200)
        self.assertEqual(res_sid.context['step'], 2)

        # 3. Reset password via alias /buyer/forgot-password/
        res_reset = self.client.post('/buyer/forgot-password/', {
            'action_reset': '1',
            'shopper_id': 'ROHIT-9988',
            'security_answer': 'fc road',
            'new_password': 'BrandNewPassword789!',
            'website': '',
        }, follow=True)
        self.assertEqual(res_reset.status_code, 200)
        buyer.refresh_from_db()
        self.assertTrue(buyer.check_password('BrandNewPassword789!'))

    def test_buyer_registration_with_security_question(self):
        payload = {
            'first_name': 'Amit',
            'gender': 'MALE',
            'birth_month': 5,
            'birth_year': 1995,
            'state': 'Maharashtra',
            'district': 'Pune',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'security_question': 'What is your favorite regional dish/food?',
            'security_answer': 'Puran Poli',
            'website': '',
            **get_test_captcha(),
        }
        res = self.client.post('/buyer/register/', payload)
        self.assertEqual(res.status_code, 302)

        user = User.objects.filter(first_name='Amit').order_by('-id').first()
        self.assertIsNotNone(user)
        self.assertTrue(hasattr(user, 'buyer_profile'))
        self.assertEqual(user.buyer_profile.security_question, 'What is your favorite regional dish/food?')
        self.assertTrue(user.buyer_profile.check_security_answer('puran poli'))
        self.assertNotIn('Puran Poli', user.buyer_profile.security_answer_hash)



class AccountDeletionAndAuditLoggingTests(TestCase):
    """
    Comprehensive verification for Enterprise-Grade Account Deletion Engine:
    - Seller zero active liabilities & pending redemptions checks
    - Bulk unpublishing & public delisting from explore/shop views
    - Soft-delete with DPDP & GST financial data retention
    - Buyer rewards forfeiture warning & deactivation
    - Re-authentication gate with rate-limiting guard
    - Immutable AccountDeletionLog creation & Admin Dashboard rendering
    """
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()

        # Create Category
        self.category = ShopCategory.objects.create(name='Electronics & Gadgets', slug='electronics-gadgets')

        # Create Seller
        self.seller_user = User.objects.create_user(
            username='seller_delete_test',
            email='seller_delete@example.com',
            password='CurrentStrongPass123!'
        )
        Profile.objects.create(user=self.seller_user, role=Role.SELLER, city='Pune')
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            merchant_id='PUN-DEL-101',
            business_name='Gadget Galaxy Store',
            security_question='What is your primary store trade license registration number?',
            is_verified=True
        )
        self.seller_profile.set_security_answer('pune-retail-404')
        self.seller_profile.save()

        # Create Store
        self.shop = Shop.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Gadget Galaxy Store',
            slug='gadget-galaxy-store',
            address='MG Road Camp',
            locality='Camp',
            city='Pune',
            is_approved=True,
            is_active=True
        )

        # Create Buyer
        self.buyer_user = User.objects.create_user(
            username='buyer_delete_test',
            email='buyer_delete@example.com',
            password='BuyerStrongPass123!'
        )
        Profile.objects.create(user=self.buyer_user, role=Role.BUYER, city='Pune')
        self.buyer_profile = BuyerProfile.objects.create(
            user=self.buyer_user,
            shopper_id='PUN-SHO-999',
            public_unique_id='LQ7M-8K2R-999X',
            security_question='What city was your mother born in?',
        )
        self.buyer_profile.set_security_answer('aurangabad')
        self.buyer_profile.save()

        # Buyer Rewards Account
        from apps.rewards.models import BuyerRewardAccount
        self.reward_account = BuyerRewardAccount.objects.create(
            buyer=self.buyer_user,
            current_points=250,
            lifetime_earned=300
        )

        # Admin User
        self.admin_user = User.objects.create_superuser(
            username='admin_audit_tester',
            email='admin_audit@example.com',
            password='AdminPassword123!'
        )
        Profile.objects.create(user=self.admin_user, role=Role.ADMIN, city='Pune')

    def test_seller_deletion_eligibility_api(self):
        # 1. Unauthenticated -> 401
        res = self.client.get('/api/seller/check-deletion-eligibility/')
        self.assertEqual(res.status_code, 401)

        # 2. Buyer accessing seller API -> 403
        self.client.force_login(self.buyer_user)
        res_buyer = self.client.get('/api/seller/check-deletion-eligibility/')
        self.assertEqual(res_buyer.status_code, 403)

        # 3. Seller accessing -> 200 with status
        self.client.force_login(self.seller_user)
        res_seller = self.client.get('/api/seller/check-deletion-eligibility/')
        self.assertEqual(res_seller.status_code, 200)
        data = res_seller.json()
        self.assertTrue(data['can_delete'])
        self.assertEqual(data['stores_count'], 1)
        self.assertEqual(data['active_campaigns_count'], 0)
        self.assertEqual(data['pending_redemptions_count'], 0)
        self.assertTrue(data['has_security_question'])

    def test_seller_cannot_delete_with_pending_redemptions(self):
        from apps.offers.models import Offer, OfferStatus, OfferType
        from apps.redemptions.models import OfferRedemption, RedemptionStatus
        from decimal import Decimal
        from datetime import timedelta
        from django.utils import timezone

        offer = Offer.objects.create(
            shop=self.shop,
            title='Flat ₹100 Off Gadgets',
            slug='flat-100-off-gadgets',
            offer_type=OfferType.FLAT,
            discount_value=Decimal('100.00'),
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=7),
            status=OfferStatus.ACTIVE
        )

        # Create pending counter redemption
        OfferRedemption.objects.create(
            offer=offer,
            shop=self.shop,
            buyer=self.buyer_user,
            seller=self.seller_user,
            idempotency_key='del-test-idem-pending-1',
            status=RedemptionStatus.PENDING_APPROVAL
        )

        self.client.force_login(self.seller_user)

        # Try to delete account
        res = self.client.post('/seller/delete-account/', {
            'reason_category': 'CLOSING_BUSINESS',
            'auth_method': 'PASSWORD',
            'current_password': 'CurrentStrongPass123!',
            'confirm_closure': 'on',
        })
        self.assertEqual(res.status_code, 302)

        # Verify seller account was NOT deleted
        self.seller_user.refresh_from_db()
        self.assertTrue(self.seller_user.is_active)
        self.assertTrue(self.shop.is_active)

    def test_seller_active_campaigns_requires_bulk_unpublish_confirmation(self):
        from apps.offers.models import Offer, OfferStatus, OfferType
        from decimal import Decimal
        from datetime import timedelta
        from django.utils import timezone

        offer = Offer.objects.create(
            shop=self.shop,
            title='Active Promo Deal',
            slug='active-promo-deal',
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('15.00'),
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=5),
            status=OfferStatus.ACTIVE
        )

        self.client.force_login(self.seller_user)

        # Submit without confirm_bulk_unpublish -> fails
        res_fail = self.client.post('/seller/delete-account/', {
            'reason_category': 'SWITCHING_PLATFORMS',
            'auth_method': 'PASSWORD',
            'current_password': 'CurrentStrongPass123!',
            'confirm_closure': 'on',
        })
        self.assertEqual(res_fail.status_code, 200)  # Re-renders form with error
        self.assertIn('confirm_bulk_unpublish', res_fail.context['form'].errors)
        self.assertIn('active campaign(s)', str(res_fail.context['form'].errors['confirm_bulk_unpublish']))

        # Submit WITH confirm_bulk_unpublish -> succeeds
        res_ok = self.client.post('/seller/delete-account/', {
            'reason_category': 'SWITCHING_PLATFORMS',
            'auth_method': 'PASSWORD',
            'current_password': 'CurrentStrongPass123!',
            'confirm_bulk_unpublish': 'on',
            'confirm_closure': 'on',
        }, follow=True)
        self.assertEqual(res_ok.status_code, 200)

        # Verify offer is paused/unpublished
        offer.refresh_from_db()
        self.assertEqual(offer.status, OfferStatus.PAUSED)

    def test_seller_reauth_failure_and_rate_limiting(self):
        self.client.force_login(self.seller_user)

        # Wrong password 4 times
        for _ in range(4):
            res = self.client.post('/seller/delete-account/', {
                'reason_category': 'OTHER',
                'auth_method': 'PASSWORD',
                'current_password': 'WrongPassword!',
                'confirm_closure': 'on',
            })
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, 'attempts remaining')

        # 5th attempt triggers lockout
        res_5 = self.client.post('/seller/delete-account/', {
            'reason_category': 'OTHER',
            'auth_method': 'PASSWORD',
            'current_password': 'WrongPassword!',
            'confirm_closure': 'on',
        })
        self.assertEqual(res_5.status_code, 200)
        self.assertContains(res_5, 'locked for 15 minutes')

    def test_seller_deletion_with_security_question(self):
        self.client.force_login(self.seller_user)

        res = self.client.post('/seller/delete-account/', {
            'reason_category': 'PRIVACY_CONCERNS',
            'reason_notes': 'Exercising data rights under DPDP Act.',
            'auth_method': 'SECURITY_QUESTION',
            'security_answer': 'PUNE-RETAIL-404',  # Case insensitive match
            'confirm_closure': 'on',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue('/seller/login/?deleted=success' in res.url)

        # Verify soft deletion
        self.seller_user.refresh_from_db()
        self.assertFalse(self.seller_user.is_active)
        self.assertTrue(self.seller_user.username.startswith('deleted_seller_'))
        self.assertFalse(self.seller_user.has_usable_password())

        # Verify store delisted
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_active)

        # Verify AccountDeletionLog entry
        from apps.accounts.models import AccountDeletionLog
        log = AccountDeletionLog.objects.filter(role='SELLER').order_by('-deleted_at').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user_identifier, 'PUN-DEL-101')
        self.assertEqual(log.reason_category, 'PRIVACY_CONCERNS')
        self.assertEqual(log.stores_delisted_count, 1)
        self.assertTrue(log.financial_records_retained)

    def test_delisted_store_disappears_from_public_views(self):
        # 1. Active store is found initially
        res_shop = self.client.get(f'/shops/{self.shop.slug}/')
        self.assertEqual(res_shop.status_code, 200)

        res_explore = self.client.get('/explore/?city=Pune')
        self.assertContains(res_explore, 'Gadget Galaxy Store')

        # 2. Delist store
        self.shop.is_active = False
        self.shop.save()

        # 3. Store view now returns 404
        res_shop_404 = self.client.get(f'/shops/{self.shop.slug}/')
        self.assertEqual(res_shop_404.status_code, 404)

        # 4. Explore page no longer shows store
        res_explore_delisted = self.client.get('/explore/?city=Pune')
        self.assertNotContains(res_explore_delisted, 'Gadget Galaxy Store')

    def test_buyer_account_deletion_forfeits_rewards_and_logs(self):
        self.client.force_login(self.buyer_user)

        res = self.client.post('/buyer/delete-account/', {
            'reason_category': 'PRIVACY_CONCERNS',
            'reason_notes': 'Personal data erasure request.',
            'auth_method': 'SECURITY_QUESTION',
            'security_answer': 'Aurangabad',
            'confirm_forfeiture': 'on',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue('/buyer/login/?deleted=success' in res.url)

        # Verify reward balance forfeited
        self.reward_account.refresh_from_db()
        self.assertEqual(self.reward_account.current_points, 0)

        # Verify user soft deleted
        self.buyer_user.refresh_from_db()
        self.assertFalse(self.buyer_user.is_active)
        self.assertTrue(self.buyer_user.username.startswith('deleted_buyer_'))

        # Verify audit log
        from apps.accounts.models import AccountDeletionLog
        log = AccountDeletionLog.objects.filter(role='BUYER').order_by('-deleted_at').first()
        self.assertIsNotNone(log)
        self.assertIn('250', log.reason_notes)
        self.assertTrue(log.financial_records_retained)

    def test_admin_dashboard_displays_deletion_logs(self):
        from apps.accounts.models import AccountDeletionLog, DeletionRole, DeletionReasonCategory
        AccountDeletionLog.objects.create(
            user=self.seller_user,
            user_identifier='PUN-AUDIT-TEST-99',
            role=DeletionRole.SELLER,
            reason_category=DeletionReasonCategory.CLOSING_BUSINESS,
            reason_notes='Retiring from retail business.',
            stores_delisted_count=2,
            financial_records_retained=True,
            ip_address='192.168.1.50'
        )

        self.client.force_login(self.admin_user)

        # Check Admin Dashboard
        res_dash = self.client.get('/admin-portal/dashboard/')
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, 'PUN-AUDIT-TEST-99')
        self.assertContains(res_dash, 'Retiring from retail business.')
        self.assertContains(res_dash, '2 Delisted')
        self.assertContains(res_dash, 'Retained (GST/IT)')

        # Check Admin Audit Logs View
        res_audit = self.client.get('/admin-portal/audit/')
        self.assertEqual(res_audit.status_code, 200)
        self.assertContains(res_audit, 'PUN-AUDIT-TEST-99')
        self.assertContains(res_audit, 'Statutory Account Deletion & DPDP Audit Trail')

    def test_delete_account_open_redirect_referer_protection(self):
        """Open Redirect Defense: Spoofed external HTTP_REFERER is blocked and falls back to safe path."""
        self.client.force_login(self.seller_user)

        # 1. External malicious referer is rejected
        res_malicious = self.client.post(
            '/seller/settings/delete-account/',
            {'confirm_closure': False},  # Fails form validation
            HTTP_REFERER='https://evil-phishing-site.com/steal-credentials/'
        )
        # Should NOT redirect to evil external site
        if res_malicious.status_code in [301, 302]:
            self.assertFalse(res_malicious.url.startswith('https://evil-phishing-site.com'))
            self.assertTrue(res_malicious.url.startswith('/seller/'))

        # 2. Internal safe referer on same host is accepted
        res_safe = self.client.post(
            '/seller/settings/delete-account/',
            {},  # Will trigger pending redemptions or lockout error if any
            HTTP_REFERER='http://testserver/seller/profile/'
        )
        if res_safe.status_code in [301, 302]:
            self.assertIn('testserver', res_safe.url)

    def test_bug66a_deleted_user_cannot_recover_or_reactivate(self):
        """BUG-66A: Deleted/inactive user cannot recover password or reactivate via recovery."""
        self.buyer_profile.set_security_answer('mumbai')
        self.buyer_profile.security_question = 'What is your favorite city?'
        self.buyer_profile.save()

        # Mark user as inactive (soft-deleted)
        self.buyer_user.is_active = False
        self.buyer_user.save()

        # Step 1: Lookup fails because user is inactive
        res = self.client.post('/buyer/recover/', {
            'action_lookup': '1',
            'shopper_id': self.buyer_profile.public_unique_id or self.buyer_user.username,
            'website': '',
            **get_test_captcha(),
        })
        self.assertContains(res, 'No shopper account found')

        # Direct access to buyer required views is denied for inactive user
        self.client.force_login(self.buyer_user)
        res_dash = self.client.get('/buyer/dashboard/')
        self.assertEqual(res_dash.status_code, 302)
        self.assertTrue(res_dash.url.startswith('/buyer/login/'))

    def test_bug66b_deleted_user_unique_ids_released_for_reregistration(self):
        """BUG-66B: Deleting account releases unique constraints (public_unique_id, merchant_id)."""
        self.client.force_login(self.buyer_user)
        res = self.client.post('/buyer/delete-account/', {
            'auth_method': 'PASSWORD',
            'current_password': 'BuyerStrongPass123!',
            'confirm_forfeiture': 'on',
            'reason_category': 'OTHER',
            'reason_notes': 'Testing unique constraint release',
        })
        self.assertEqual(res.status_code, 302)

        self.buyer_profile.refresh_from_db()
        self.assertIsNone(self.buyer_profile.public_unique_id)
        self.assertTrue(self.buyer_profile.shopper_id.startswith('del_'))

    def test_bug66c_multi_device_sessions_flushed_on_deletion(self):
        """BUG-66C: Remote sessions across multiple devices are invalidated upon deletion."""
        from django.contrib.sessions.models import Session
        from django.contrib.sessions.backends.db import SessionStore

        # Create simulated remote session for this user
        remote_session = SessionStore()
        remote_session['_auth_user_id'] = str(self.buyer_user.id)
        remote_session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        remote_session.create()
        remote_key = remote_session.session_key

        self.assertTrue(Session.objects.filter(session_key=remote_key).exists())

        # Perform deletion
        self.client.force_login(self.buyer_user)
        res = self.client.post('/buyer/delete-account/', {
            'auth_method': 'PASSWORD',
            'current_password': 'BuyerStrongPass123!',
            'confirm_forfeiture': 'on',
            'reason_category': 'OTHER',
            'reason_notes': 'Session flush test',
        })
        self.assertEqual(res.status_code, 302)

        # Verify remote session was deleted from database
        self.assertFalse(Session.objects.filter(session_key=remote_key).exists())

    def test_bug66d_pending_bumper_bills_and_claims_expired_on_deletion(self):
        """BUG-66D: Pending bumper bills are rejected and reward claims expired on deletion."""
        from apps.rewards.models import (
            BumperBillSubmission, BumperBillStatus,
            RewardCatalogItem, BuyerRewardClaim, CatalogRewardType
        )
        from decimal import Decimal

        # Create pending bumper bill
        bill = BumperBillSubmission.objects.create(
            buyer=self.buyer_user,
            shop=self.shop,
            bill_number='TEST-BILL-66D',
            bill_amount=Decimal('15000.00'),
            status=BumperBillStatus.PENDING_STORE
        )

        # Create catalog item and active claim
        cat_item = RewardCatalogItem.objects.create(
            title='₹500 Gourmet Meal Voucher',
            points_required=100,
            reward_type=CatalogRewardType.FOOD_BEVERAGE
        )
        claim = BuyerRewardClaim.objects.create(
            buyer=self.buyer_user,
            catalog_item=cat_item,
            voucher_code='CLAIM-66D-TEST',
            points_spent=100,
            status='ACTIVE'
        )

        # Perform deletion
        self.client.force_login(self.buyer_user)
        res = self.client.post('/buyer/delete-account/', {
            'auth_method': 'PASSWORD',
            'current_password': 'BuyerStrongPass123!',
            'confirm_forfeiture': 'on',
            'reason_category': 'OTHER',
            'reason_notes': 'Testing financial cleanup',
        })
        self.assertEqual(res.status_code, 302)

        bill.refresh_from_db()
        claim.refresh_from_db()

        self.assertEqual(bill.status, BumperBillStatus.REJECTED)
        self.assertEqual(claim.status, 'EXPIRED')

    def test_bug66e_pii_scrubbed_under_dpdp_section_12(self):
        """BUG-66E: PII (first_name, last_name, nickname, security question/hash) is wiped on deletion."""
        self.buyer_user.first_name = 'Arjun'
        self.buyer_user.last_name = 'Sharma'
        self.buyer_user.save()

        self.buyer_profile.nickname = 'GoldHunter'
        self.buyer_profile.security_question = 'Mother maiden name?'
        self.buyer_profile.set_security_answer('sharma')
        self.buyer_profile.save()

        # Perform deletion
        self.client.force_login(self.buyer_user)
        res = self.client.post('/buyer/delete-account/', {
            'auth_method': 'PASSWORD',
            'current_password': 'BuyerStrongPass123!',
            'confirm_forfeiture': 'on',
            'reason_category': 'PRIVACY_CONCERNS',
            'reason_notes': 'DPDP Section 12 erasure request',
        })
        self.assertEqual(res.status_code, 302)

        self.buyer_user.refresh_from_db()
        self.buyer_profile.refresh_from_db()

        self.assertEqual(self.buyer_user.first_name, '')
        self.assertEqual(self.buyer_user.last_name, '')
        self.assertEqual(self.buyer_profile.nickname, '')
        self.assertEqual(self.buyer_profile.security_question, '')
        self.assertEqual(self.buyer_profile.security_answer_hash, '')

    def test_bug66f_admin_counts_and_filters_active_users(self):
        """BUG-66F: Admin portal filters out deleted users and counts active users accurately."""
        # Deactivate buyer user
        self.buyer_user.is_active = False
        self.buyer_user.save()

        self.client.force_login(self.admin_user)
        res = self.client.get('/admin-portal/buyers/?status=active')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(self.buyer_user, res.context['buyers'])

        res_deleted = self.client.get('/admin-portal/buyers/?status=deleted')
        self.assertEqual(res_deleted.status_code, 200)
        self.assertIn(self.buyer_user, res_deleted.context['buyers'])

    def test_bug66g_recovery_rate_limit_canonical_user_lockout(self):
        """BUG-66G: Canonical user ID lockout prevents identifier hopping during recovery."""
        from django.core.cache import cache
        cache.clear()

        self.buyer_profile.security_question = 'First car?'
        self.buyer_profile.set_security_answer('honda')
        self.buyer_profile.shopper_id = 'SHOPPER-66G'
        self.buyer_profile.save()

        canonical_key = f"recover_fail_canonical_user:{self.buyer_user.id}"

        # 5 failed attempts targeting this user
        for _ in range(5):
            self.client.post('/buyer/recover/', {
                'action_reset': '1',
                'shopper_id': 'SHOPPER-66G',
                'security_answer': 'wrong-answer',
                'new_password': 'NewPassword123!',
                'confirm_new_password': 'NewPassword123!',
            })

        # 6th attempt is locked out with 429
        res = self.client.post('/buyer/recover/', {
            'action_reset': '1',
            'shopper_id': self.buyer_user.username,  # Hopping to different identifier!
            'security_answer': 'wrong-answer',
            'new_password': 'NewPassword123!',
            'confirm_new_password': 'NewPassword123!',
        })
        self.assertEqual(res.status_code, 429)


class BuyerAccountViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.buyer_user = User.objects.create_user(
            username='account_tester',
            email='account_tester@example.com',
            password='Password123!'
        )
        Profile.objects.create(user=self.buyer_user, role=Role.BUYER, city='Pune')
        self.buyer_profile = BuyerProfile.objects.create(
            user=self.buyer_user,
            nickname='RahulG',
            shopper_id='PUN-SHO-777',
            public_unique_id='LQ7M-8K2R-777X',
            preferred_city='Pune',
            district='Pune'
        )
        from apps.rewards.models import BuyerRewardAccount
        self.reward_account = BuyerRewardAccount.objects.create(
            buyer=self.buyer_user,
            current_points=120,
            lifetime_earned=350,
            lifetime_spent=230
        )

    def test_anonymous_access_redirects_to_login(self):
        res = self.client.get('/buyer/account/')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/buyer/login/', res.url)

    def test_authenticated_buyer_account_page_render(self):
        self.client.force_login(self.buyer_user)
        res = self.client.get('/buyer/account/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'RahulG')
        self.assertContains(res, 'LQ7M-8K2R-777X')
        self.assertContains(res, 'Silver Tier')
        self.assertEqual(res.context['tier_title'], 'Silver Tier')
        self.assertEqual(res.context['reward_account'].current_points, 120)

    def test_update_profile_post(self):
        self.client.force_login(self.buyer_user)
        res = self.client.post('/buyer/account/', {
            'action': 'update_profile',
            'nickname': 'RahulChampion',
            'city': 'Mumbai'
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, '/buyer/account/')
        self.buyer_profile.refresh_from_db()
        self.assertEqual(self.buyer_profile.nickname, 'RahulChampion')
        self.assertEqual(self.buyer_profile.preferred_city, 'Mumbai')
