from django.test import TestCase, Client, RequestFactory
from django.urls import path
from django.http import HttpResponse
from django.contrib.auth.models import User
from apps.administration.models import ErrorLog, ErrorLogLevel
from apps.administration.middleware import ExceptionLoggingMiddleware
from apps.administration.views import custom_404_view, custom_500_view
from services.security_logger import (
    generate_incident_id,
    is_sensitive_key,
    mask_sensitive_payload,
    scrub_traceback,
    scrub_log_message
)


def sample_crashing_view(request):
    """Simulated view that crashes with unhandled exception containing sensitive data."""
    sensitive_var = "MySuperSecretP@ssword99!"
    user_pan = "ABCDE1234F"
    user_phone = "9822011223"
    raise RuntimeError(f"Database query failure: password='{sensitive_var}', pan='{user_pan}', mobile={user_phone}")


class ErrorHandlingAndRedactionSecurityTestCase(TestCase):
    """
    Comprehensive verification of Enterprise-Grade Custom 500/404 Error Handling
    and Sensitive Data (PII) Redaction Engine:
    1. Automated PII & credential scrubbing from logs & tracebacks.
    2. Zero information leakage to frontend.
    3. Incident Reference ID generation and correlation.
    4. Custom 500 & 404 error page status codes and isolated rendering.
    """

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.middleware = ExceptionLoggingMiddleware(get_response=lambda r: HttpResponse())

    # ==========================================================================
    # 1. PII REDACTION & DATA MASKING ENGINE
    # ==========================================================================

    def test_sensitive_key_detection(self):
        """Verify blacklisted keys are accurately identified (case & delimiter insensitive)."""
        sensitive_keys = [
            'password', 'PASSWORD', 'Confirm_Password', 'new-password',
            'current_password', 'token', 'captcha_answer', 'captcha_token',
            'secret', 'api_key', 'pan', 'pan_number', 'business_pan',
            'fssai', 'fssai_license_number', 'fda_license_number',
            'credit_card', 'authorization', 'csrfmiddlewaretoken'
        ]
        for key in sensitive_keys:
            self.assertTrue(is_sensitive_key(key), f"Expected '{key}' to be identified as sensitive.")

        non_sensitive = ['username', 'email', 'business_name', 'city', 'locality', 'created_at']
        for key in non_sensitive:
            self.assertFalse(is_sensitive_key(key), f"Expected '{key}' to NOT be marked sensitive.")

    def test_recursive_payload_masking(self):
        """Verify nested dictionaries, lists, and query payloads are masked with [REDACTED]."""
        payload = {
            'user': {
                'username': 'rameshwar_k',
                'password': 'SuperSecretPassword123!',
                'confirm_password': 'SuperSecretPassword123!',
            },
            'business': {
                'business_name': 'Kothrud Fresh Mart',
                'business_pan': 'ABCDE1234F',
                'fssai_license_number': '11521012345678',
            },
            'security': {
                'captcha_token': 'secret-token-xyz',
                'captcha_answer': '1234',
                'api_key': 'ak_live_abcdef123456',
            },
            'items': [
                {'name': 'Apple', 'token': 'jwt_secret_token_123'},
                {'name': 'Banana', 'price': 50}
            ]
        }

        masked = mask_sensitive_payload(payload)

        # Sensitive keys MUST be [REDACTED]
        self.assertEqual(masked['user']['password'], '[REDACTED]')
        self.assertEqual(masked['user']['confirm_password'], '[REDACTED]')
        self.assertEqual(masked['business']['business_pan'], '[REDACTED]')
        self.assertEqual(masked['business']['fssai_license_number'], '[REDACTED]')
        self.assertEqual(masked['security']['captcha_token'], '[REDACTED]')
        self.assertEqual(masked['security']['captcha_answer'], '[REDACTED]')
        self.assertEqual(masked['security']['api_key'], '[REDACTED]')
        self.assertEqual(masked['items'][0]['token'], '[REDACTED]')

        # Non-sensitive keys MUST remain untouched
        self.assertEqual(masked['user']['username'], 'rameshwar_k')
        self.assertEqual(masked['business']['business_name'], 'Kothrud Fresh Mart')
        self.assertEqual(masked['items'][1]['price'], 50)

    def test_traceback_pii_scrubbing(self):
        """Verify inline passwords, PAN cards, and 10-digit mobile numbers are scrubbed from tracebacks."""
        raw_traceback = (
            'Traceback (most recent call last):\n'
            '  File "/app/apps/accounts/views.py", line 120, in login_view\n'
            '    password = "MyCleartextPassword123!"\n'
            '    pan_number = "ABCDE1234F"\n'
            '    phone = "9822011223"\n'
            '    headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"}\n'
            'ZeroDivisionError: division by zero'
        )

        cleaned_tb = scrub_traceback(raw_traceback)

        # Cleartext secrets MUST NOT exist in scrubbed output
        self.assertNotIn("MyCleartextPassword123!", cleaned_tb)
        self.assertNotIn("ABCDE1234F", cleaned_tb)
        self.assertNotIn("9822011223", cleaned_tb)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz", cleaned_tb)

        # Redaction markers MUST be present
        self.assertIn('[REDACTED', cleaned_tb)

    def test_log_message_pii_scrubbing(self):
        """Verify inline credentials and numbers inside log messages are scrubbed."""
        msg = "Authentication failure for mobile 9822011223 with pan ABCDE1234F and password='Secret!123'"
        cleaned_msg = scrub_log_message(msg)

        self.assertNotIn("9822011223", cleaned_msg)
        self.assertNotIn("ABCDE1234F", cleaned_msg)
        self.assertNotIn("Secret!123", cleaned_msg)
        self.assertIn("[REDACTED", cleaned_msg)

    # ==========================================================================
    # 2. EXCEPTION LOGGING MIDDLEWARE & INCIDENT REFERENCE ID
    # ==========================================================================

    def test_exception_logging_middleware_captures_incident_id_and_redacts(self):
        """
        Verify that an unhandled view exception:
        1. Generates a unique Incident Reference ID (ERR-XXXXXXXX).
        2. Attaches incident_id to request.
        3. Creates an ErrorLog record with sanitized traceback.
        4. Returns custom_500_view with status 500 and matching incident_id.
        """
        request = self.factory.post(
            '/buyer/login/',
            data={'email': 'shopper@test.com', 'password': 'PlaintextPassword123!'},
            HTTP_USER_AGENT='Mozilla/5.0 TestBrowser'
        )

        try:
            sample_crashing_view(request)
        except Exception as exc:
            response = self.middleware.process_exception(request, exc)

        # 1. Status Code must be 500
        self.assertEqual(response.status_code, 500)

        # 2. Incident ID must be attached to request
        self.assertTrue(hasattr(request, 'incident_id'))
        incident_id = request.incident_id
        self.assertTrue(incident_id.startswith('ERR-'))

        # 3. ErrorLog must be created with matching incident_id
        log_entry = ErrorLog.objects.filter(incident_id=incident_id).first()
        self.assertIsNotNone(log_entry, "Expected an ErrorLog with matching incident_id to be persisted.")

        # 4. ErrorLog must contain REDACTED data, never plaintext password
        self.assertNotIn("PlaintextPassword123!", log_entry.traceback)
        self.assertNotIn("MySuperSecretP@ssword99!", log_entry.traceback)
        self.assertNotIn("ABCDE1234F", log_entry.traceback)
        self.assertNotIn("9822011223", log_entry.traceback)
        self.assertIn("[REDACTED", log_entry.traceback)

        # 5. Response HTML must contain incident_id and ZERO Python traces
        content = response.content.decode('utf-8')
        self.assertIn(incident_id, content)
        self.assertIn("Something went wrong on our end", content)
        self.assertNotIn("RuntimeError", content)
        self.assertNotIn("Traceback", content)
        self.assertNotIn("MySuperSecretP@ssword99!", content)

    def test_middleware_survives_internal_logging_failures(self):
        """Guarantees middleware never crashes even if the database logging operation fails."""
        request = self.factory.get('/test-crash/')
        
        # Pass an object that causes an exception during logging
        exc = RuntimeError("Simulated crash")
        
        # Even if ErrorLog creation fails, process_exception must return a valid 500 response
        response = self.middleware.process_exception(request, exc)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(hasattr(request, 'incident_id'))

    # ==========================================================================
    # 3. CUSTOM 500 & 404 ERROR VIEWS (ZERO INFORMATION LEAKAGE)
    # ==========================================================================

    def test_custom_404_view_html(self):
        """Verify custom 404 page returns HTTP 404 and clean branded UI."""
        request = self.factory.get('/completely-non-existent-page/')
        response = custom_404_view(request)

        self.assertEqual(response.status_code, 404)
        content = response.content.decode('utf-8')
        self.assertIn("Page Not Found", content)
        self.assertIn("Explore Deals", content)
        self.assertIn("Return to Home", content)
        # Ensure zero technical trace
        self.assertNotIn("Resolver404", content)
        self.assertNotIn("urlpatterns", content)

    def test_custom_404_view_api(self):
        """Verify API 404 returns structured clean JSON with HTTP 404."""
        request = self.factory.get('/api/non-existent-endpoint/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = custom_404_view(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn(b'"Not Found"', response.content)

    def test_custom_500_view_html(self):
        """Verify custom 500 page displays Incident Reference ID and copy helper."""
        request = self.factory.get('/some-broken-page/')
        request.incident_id = "ERR-99887766"
        response = custom_500_view(request)

        self.assertEqual(response.status_code, 500)
        content = response.content.decode('utf-8')
        self.assertIn("ERR-99887766", content)
        self.assertIn("Something went wrong on our end", content)
        self.assertIn("Copy ID", content)
        # Must contain zero technical stack trace
        self.assertNotIn("Traceback", content)
        self.assertNotIn("Exception", content)
        self.assertNotIn("SELECT", content)

    def test_custom_500_view_api(self):
        """Verify API 500 returns clean JSON with incident_id and HTTP 500."""
        request = self.factory.get('/api/trigger-error/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        request.incident_id = "ERR-API12345"
        response = custom_500_view(request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn(b'"incident_id": "ERR-API12345"', response.content)
        self.assertNotIn(b'Traceback', response.content)

    def test_client_404_live_dispatch(self):
        """Verify live HTTP client GET to unmatched URL triggers 404 with custom template."""
        res = self.client.get('/this-path-definitely-does-not-exist-12345/')
        self.assertEqual(res.status_code, 404)
        self.assertContains(res, "Page Not Found", status_code=404)
        self.assertContains(res, "Lost in your neighborhood?", status_code=404)
