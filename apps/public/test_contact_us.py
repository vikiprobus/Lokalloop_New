from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.cache import cache
from apps.administration.models import ContactInquiry, InquiryRole, SupportTicket, TicketStatus
from services.captcha_service import generate_captcha

User = get_user_model()


def get_test_captcha():
    c = generate_captcha()
    return {
        'captcha_token': c['captcha_token'],
        'captcha_answer': c['_solution'],
    }


class ContactUsComplianceAndSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_contact_us_urls_resolve_and_render_statutory_disclosures(self):
        """
        Verify that both /contact/ and /contact-us/ return HTTP 200 and
        display all statutory & corporate disclosures required for compliance
        (Google Ads, Merchant Center, Play Store, IT Rules 2021).
        """
        for url in ['/contact/', '/contact-us/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

            content = response.content.decode('utf-8')

            # 1. Official Corporate Entity Disclosures
            self.assertIn("Trisha Infotech Solution", content)
            self.assertIn("GENNIA", content)
            self.assertIn("Office 610, D Wing, Freedom Tower", content)
            self.assertIn("Chhatrapati Sambhaji Nagar", content)
            self.assertIn("431001", content)
            self.assertIn("gennia@gmail.com", content)

            # 2. Operating Hours & Customer Care
            self.assertIn("10:00 AM", content)
            self.assertIn("6:30 PM", content)

            # 3. Statutory Grievance Redressal Details
            self.assertIn("Rule 3(2)", content)
            self.assertIn("Information Technology", content)
            self.assertIn("48 hours", content)
            self.assertIn("30 calendar days", content)

            # 4. Form Fields & Honeypot Check
            self.assertIn('name="full_name"', content)
            self.assertIn('name="email"', content)
            self.assertIn('name="phone"', content)
            self.assertIn('name="user_role"', content)
            self.assertIn('name="subject"', content)
            self.assertIn('name="message"', content)
            self.assertIn('name="website"', content)

    def test_contact_us_valid_submission_creates_inquiry_and_redirects(self):
        """
        Verify that a valid anonymous inquiry is stored as ContactInquiry with
        ticket number prefixed with TKT-, status OPEN, and redirects to PRG landing.
        """
        payload = {
            'full_name': 'Ramesh Patil',
            'email': 'ramesh.patil@example.com',
            'phone': '9822011223',
            'user_role': InquiryRole.BUYER,
            'subject': 'Inquiry regarding coupon scanning',
            'message': 'Hello support team, I would like to inquire about redeeming weekend grocery offers.',
            'website': '',
            **get_test_captcha(),
        }

        response = self.client.post('/contact-us/', payload, follow=False, REMOTE_ADDR='103.21.244.10')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/contact-us/?ticket=TKT-'))

        inquiry = ContactInquiry.objects.filter(email='ramesh.patil@example.com').first()
        self.assertIsNotNone(inquiry)
        self.assertEqual(inquiry.full_name, 'Ramesh Patil')
        self.assertEqual(inquiry.phone, '9822011223')
        self.assertEqual(inquiry.user_role, InquiryRole.BUYER)
        self.assertEqual(inquiry.status, TicketStatus.OPEN)
        self.assertEqual(inquiry.ip_address, '103.21.244.10')
        self.assertTrue(inquiry.ticket_number.startswith('TKT-'))

    def test_contact_us_authenticated_user_links_and_creates_mirrored_support_ticket(self):
        """
        Verify that an inquiry submitted by an authenticated user is linked to their
        account and automatically creates a mirrored SupportTicket.
        """
        user = User.objects.create_user(
            username='merchant_user_test',
            email='merchant_test@example.com',
            password='TestSecurePassword123!'
        )
        self.client.force_login(user)

        payload = {
            'full_name': 'Merchant Partner',
            'email': 'merchant_test@example.com',
            'phone': '9890123456',
            'user_role': InquiryRole.MERCHANT,
            'subject': 'Settlement and payout query',
            'message': 'Need clarification on weekly batch redemption settlements.',
            'website': '',
            **get_test_captcha(),
        }

        response = self.client.post('/contact-us/', payload, follow=False)
        self.assertEqual(response.status_code, 302)

        inquiry = ContactInquiry.objects.filter(email='merchant_test@example.com').first()
        self.assertIsNotNone(inquiry)
        self.assertEqual(inquiry.user, user)

        support_ticket = SupportTicket.objects.filter(user=user).first()
        self.assertIsNotNone(support_ticket)
        self.assertIn(inquiry.ticket_number, support_ticket.subject)
        self.assertEqual(support_ticket.status, TicketStatus.OPEN)

    def test_contact_us_honeypot_rejects_spambot(self):
        """
        Verify that submissions filling the invisible 'website' honeypot are rejected.
        """
        payload = {
            'full_name': 'Spam Bot',
            'email': 'bot@automated-spam.com',
            'phone': '9822011223',
            'user_role': InquiryRole.GENERAL,
            'subject': 'Buy cheap crypto now',
            'message': 'Spam spam spam marketing message here.',
            'website': 'https://malicious-spam-site.com',
            **get_test_captcha(),
        }

        response = self.client.post('/contact-us/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactInquiry.objects.filter(email='bot@automated-spam.com').count(), 0)

    def test_contact_us_invalid_mobile_rejected(self):
        """
        Verify that non-Indian or improperly formatted mobile numbers are rejected.
        """
        payload = {
            'full_name': 'Aakash Deshmukh',
            'email': 'aakash@example.com',
            'phone': '12345',  # Invalid phone
            'user_role': InquiryRole.BUYER,
            'subject': 'Help with login',
            'message': 'Cannot sign into my buyer app.',
            'website': '',
            **get_test_captcha(),
        }

        response = self.client.post('/contact-us/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactInquiry.objects.filter(email='aakash@example.com').count(), 0)

    def test_contact_us_xss_injection_detected_and_rejected(self):
        """
        Verify that script tags and dangerous HTML in input fields are caught by
        the security validator.
        """
        payload = {
            'full_name': 'Hacker <script>alert(1)</script>',
            'email': 'hacker@example.com',
            'phone': '9822011223',
            'user_role': InquiryRole.GENERAL,
            'subject': 'XSS Probe',
            'message': '<img src=x onerror=alert(1)> Testing vulnerability',
            'website': '',
            **get_test_captcha(),
        }

        response = self.client.post('/contact-us/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactInquiry.objects.filter(email='hacker@example.com').count(), 0)

    def test_contact_us_rate_limiting_enforcement(self):
        """
        Verify that exceeding 5 submissions from the same IP address triggers HTTP 429.
        """
        test_ip = '198.51.100.42'
        base_payload = {
            'full_name': 'Rate Limit Tester',
            'email': 'rate.limit@example.com',
            'phone': '9822011223',
            'user_role': InquiryRole.BUYER,
            'subject': 'Rate test query',
            'message': 'Testing rate limiting enforcement for spam protection.',
            'website': '',
        }

        # 5 allowed requests
        for i in range(5):
            p = {**base_payload, **get_test_captcha()}
            res = self.client.post('/contact-us/', p, REMOTE_ADDR=test_ip)
            self.assertEqual(res.status_code, 302, f"Request {i+1} failed to submit")

        # 6th request should hit rate limit HTTP 429
        p6 = {**base_payload, **get_test_captcha()}
        res6 = self.client.post('/contact-us/', p6, REMOTE_ADDR=test_ip)
        self.assertEqual(res6.status_code, 429)
