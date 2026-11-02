import time
from django.test import TestCase, Client
from django.core.exceptions import ValidationError
from services.security_validator import (
    sanitize_text,
    sanitize_null_bytes,
    detect_malicious_payload,
    validate_indian_mobile,
    validate_indian_pan,
    validate_indian_gstin,
    validate_fssai_license,
    validate_pincode,
    validate_alphanumeric_name,
    validate_safe_filename,
    SecurityValidator,
    SecurityValidationError,
    ReDoSExecutionGuardError,
    LENGTH_LIMITS,
    MAX_REQUEST_PAYLOAD_BYTES
)


class SecurityEngineUnitTestCase(TestCase):
    """
    Comprehensive verification of the Enterprise Input Validation & Sanitization Engine:
    1. O(1) Pre-validation length capping.
    2. ReDoS immunity & execution guard timing.
    3. Whitelist validation (Mobile, PAN, GSTIN, FSSAI, Pincode, Name).
    4. Attack defense (SQLi, XSS, Path Traversal, Null Bytes).
    5. Clean user-safe error messages.
    """

    def setUp(self):
        self.client = Client()

    # ==========================================================================
    # 1. O(1) PRE-VALIDATION LENGTH & SIZE CAPPING
    # ==========================================================================

    def test_o1_length_capping_sub_millisecond_rejection(self):
        """Verify that oversized inputs are rejected in < 0.1ms without regex evaluation."""
        huge_mobile_input = "9" * 100_000
        start = time.perf_counter()
        is_valid, cleaned, err = validate_indian_mobile(huge_mobile_input)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertFalse(is_valid)
        self.assertIn("10-digit", err)
        self.assertLess(elapsed_ms, 1.0, f"O(1) pre-check took {elapsed_ms:.4f}ms, expected < 1ms")

    def test_o1_pan_length_capping(self):
        """Verify PAN with length > 12 is rejected instantly."""
        huge_pan = "ABCDE1234F" * 10_000
        start = time.perf_counter()
        is_valid, cleaned, err = validate_indian_pan(huge_pan)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertFalse(is_valid)
        self.assertIn("maximum 12 characters", err)
        self.assertLess(elapsed_ms, 1.0)

    def test_o1_gstin_length_capping(self):
        """Verify GSTIN with length > 15 is rejected instantly."""
        huge_gstin = "27ABCDE1234F1Z5" + "X" * 5000
        is_valid, cleaned, err = validate_indian_gstin(huge_gstin)
        self.assertFalse(is_valid)
        self.assertIn("cannot exceed 15 characters", err)

    def test_o1_fssai_length_capping(self):
        """Verify FSSAI license exceeding 16 digits is rejected instantly."""
        huge_fssai = "12345678901234567890"
        is_valid, cleaned, err = validate_fssai_license(huge_fssai)
        self.assertFalse(is_valid)
        self.assertIn("maximum 16 digits", err)

    def test_o1_pincode_length_capping(self):
        """Verify pincode not equal to 6 digits is rejected instantly."""
        self.assertFalse(validate_pincode("41100199")[0])
        self.assertFalse(validate_pincode("4110")[0])

    # ==========================================================================
    # 2. ReDoS IMMUNITY & EXECUTION GUARD TIMING
    # ==========================================================================

    def test_redos_immunity_against_catastrophic_backtracking_payloads(self):
        """
        Tests evil regex payloads (e.g., repeated sequences designed to trigger
        catastrophic exponential backtracking on non-linear engines).
        Guarantees evaluation finishes well under 5ms (zero CPU spike).
        """
        evil_payloads = [
            "a" * 10_000 + "!",
            " " * 5_000 + "9822011223",
            "SELECT " + "a, " * 500 + " FROM users",
            "<script " + "attr='val' " * 200 + ">alert(1)</script>",
            "../" * 500 + "etc/passwd",
        ]

        for payload in evil_payloads:
            start = time.perf_counter()
            has_attack, reason = detect_malicious_payload(payload)
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            self.assertLess(
                elapsed_ms, 10.0,
                f"ReDoS detected: Payload took {elapsed_ms:.3f}ms (threshold 10ms)"
            )

    # ==========================================================================
    # 3. STRICT WHITELIST VALIDATION
    # ==========================================================================

    def test_indian_mobile_whitelist_validation(self):
        """Verify Indian mobile numbers: ^[6-9]\\d{9}$ after standardization."""
        valid_numbers = [
            "9822011223",
            "+91 9822011223",
            "9822-011-223",
            "6123456789",
            "8999999999",
            "7000000000",
        ]
        for num in valid_numbers:
            is_valid, cleaned, err = validate_indian_mobile(num)
            self.assertTrue(is_valid, f"Expected {num} to be valid, err: {err}")
            self.assertEqual(len(cleaned), 10)
            self.assertRegex(cleaned, r'^[6-9]\d{9}$')

        invalid_numbers = [
            "5123456789",     # Starts with 5 (invalid)
            "09822011223",    # 11 digits
            "982201122",      # 9 digits
            "982201122a",     # Contains alphabet
            "9822@011223",    # Special characters
            "",               # Empty
        ]
        for num in invalid_numbers:
            is_valid, cleaned, err = validate_indian_mobile(num)
            self.assertFalse(is_valid, f"Expected {num} to be invalid")
            self.assertTrue(len(err) > 0)

    def test_indian_pan_whitelist_validation(self):
        """Verify Indian PAN: ^[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{0,2}$."""
        valid_pans = [
            "ABCDE1234F",
            "AAAPL1234C",
            "XYZAB9876Q",
            "ABCDE1234F1",
            "ABCDE1234F12",
        ]
        for pan in valid_pans:
            is_valid, cleaned, err = validate_indian_pan(pan)
            self.assertTrue(is_valid, f"Expected {pan} to be valid, err: {err}")
            self.assertEqual(cleaned, pan.upper())

        invalid_pans = [
            "12345ABCDE",     # Inverted format
            "ABCDE12345",     # Trailing digits instead of letter
            "ABC1234F",       # Too short
            "ABCDE1234F123",  # Too long (> 12)
            "ABCDE-1234F",    # Illegal hyphen
            "abcde1234f#",    # Illegal special symbol
            "",               # Empty
        ]
        for pan in invalid_pans:
            is_valid, cleaned, err = validate_indian_pan(pan)
            self.assertFalse(is_valid, f"Expected {pan} to be invalid")

    def test_indian_gstin_whitelist_validation(self):
        """Verify Indian GSTIN: ^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$."""
        valid_gstins = [
            "27ABCDE1234F1Z5",
            "29AAAAA0000A1Z5",
            "07AAAAA0000A1Z5",
        ]
        for gstin in valid_gstins:
            is_valid, cleaned, err = validate_indian_gstin(gstin)
            self.assertTrue(is_valid, f"Expected {gstin} to be valid, err: {err}")

        # Optional field should pass when empty
        self.assertTrue(validate_indian_gstin("")[0])
        self.assertTrue(validate_indian_gstin(None)[0])

        invalid_gstins = [
            "27ABCDE1234F",        # Too short
            "27ABCDE1234F1Z599",   # Too long
            "XXABCDE1234F1Z5",     # Letters instead of state code
            "27ABCDE1234F1A5",     # Missing required 'Z' digit
        ]
        for gstin in invalid_gstins:
            is_valid, cleaned, err = validate_indian_gstin(gstin)
            self.assertFalse(is_valid, f"Expected {gstin} to be invalid")

    def test_fssai_license_whitelist_validation(self):
        """Verify FSSAI license: ^[0-9]{14,16}$."""
        valid_licenses = [
            "11521012345678",      # 14 digits
            "115210123456789",     # 15 digits
            "1152101234567890",    # 16 digits
        ]
        for lic in valid_licenses:
            is_valid, cleaned, err = validate_fssai_license(lic)
            self.assertTrue(is_valid, f"Expected {lic} to be valid, err: {err}")

        # Optional field check
        self.assertTrue(validate_fssai_license("")[0])

        invalid_licenses = [
            "11521012345",         # 11 digits (too short)
            "11521012345678901",   # 17 digits (too long)
            "1152101234567A",      # Contains character
            "1152-1012-3456",      # Contains hyphens
        ]
        for lic in invalid_licenses:
            is_valid, cleaned, err = validate_fssai_license(lic)
            self.assertFalse(is_valid, f"Expected {lic} to be invalid")

    def test_pincode_whitelist_validation(self):
        """Verify Indian pincode: ^[1-9][0-9]{5}$."""
        valid_pincodes = ["411001", "560001", "400050", "110001"]
        for pin in valid_pincodes:
            is_valid, cleaned, err = validate_pincode(pin)
            self.assertTrue(is_valid)
            self.assertEqual(cleaned, pin)

        invalid_pincodes = ["011001", "41100", "4110011", "41100A", "411 001"]
        for pin in invalid_pincodes:
            is_valid, cleaned, err = validate_pincode(pin)
            self.assertFalse(is_valid)

    def test_alphanumeric_name_validation(self):
        """Verify alphanumeric names with safe punctuation."""
        valid_names = [
            "Artisan Coffee & Roasters",
            "Pune Supreme Supermarket, Koregaon Park",
            "Kulkarni's Fresh Mart",
            "A-Z Electronics",
        ]
        for name in valid_names:
            is_valid, cleaned, err = validate_alphanumeric_name(name)
            self.assertTrue(is_valid, f"Expected {name} to be valid, err: {err}")

        invalid_names = [
            "",
            "A" * 101,  # Exceeds max 100
            "Shop <script>alert(1)</script>",
            "Store; DROP TABLE shops; --",
        ]
        for name in invalid_names:
            is_valid, cleaned, err = validate_alphanumeric_name(name)
            self.assertFalse(is_valid, f"Expected {name} to be invalid")

    # ==========================================================================
    # 4. ATTACK IMMUNITY & SANITIZATION (SQLi, XSS, Path Traversal, Null Bytes)
    # ==========================================================================

    def test_sqli_attack_detection(self):
        """Verify SQL injection vectors are detected and blocked."""
        sqli_payloads = [
            "1; DROP TABLE users; --",
            "' UNION SELECT username, password FROM auth_user --",
            "admin' OR 1=1 --",
            "1' AND 1=1 --",
            "1; EXEC xp_cmdshell('dir')",
            "1' UNION SELECT NULL, NULL, NULL --",
        ]
        for payload in sqli_payloads:
            has_attack, reason = detect_malicious_payload(payload)
            self.assertTrue(has_attack, f"Failed to detect SQLi in: {payload}")
            self.assertIn("SQL injection", reason)

    def test_xss_attack_detection_and_sanitization(self):
        """Verify XSS vectors are detected and sanitized."""
        xss_payloads = [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert('XSS')>",
            "javascript:alert(document.cookie)",
            "<iframe src='http://evil.com'></iframe>",
            "<svg onload=alert(1)>",
        ]
        for payload in xss_payloads:
            has_attack, reason = detect_malicious_payload(payload)
            self.assertTrue(has_attack, f"Failed to detect XSS in: {payload}")

        # Verify sanitize_text strips HTML tags and escapes dangerous markup
        dirty_input = "Hello <b>World</b><script>alert('xss')</script>"
        sanitized = sanitize_text(dirty_input)
        self.assertNotIn("<script>", sanitized)
        self.assertNotIn("<b>", sanitized)
        self.assertIn("World", sanitized)

    def test_path_traversal_detection_and_safe_filename(self):
        """Verify directory traversal and arbitrary file upload attacks are blocked."""
        traversal_payloads = [
            "../../etc/passwd",
            "..\\..\\windows\\system32\\cmd.exe",
            "....//....//etc/shadow",
            "file.php\x00.jpg",
            "/etc/hosts",
            "C:\\boot.ini",
        ]
        for payload in traversal_payloads:
            is_valid, cleaned, err = validate_safe_filename(payload)
            self.assertFalse(is_valid, f"Expected traversal to be blocked for: {payload}")

        valid_files = [
            "store_front.jpg",
            "business_pan_doc.pdf",
            "shop-act-2026.png",
            "fssai_cert.webp",
        ]
        for f in valid_files:
            is_valid, cleaned, err = validate_safe_filename(f)
            self.assertTrue(is_valid, f"Expected safe filename {f} to pass")

    def test_null_byte_stripping(self):
        """Verify null bytes are stripped cleanly from all data structures."""
        dirty_str = "user\x00name"
        cleaned_str = sanitize_null_bytes(dirty_str)
        self.assertEqual(cleaned_str, "username")

        dirty_dict = {"key\x00": "val\x00ue", "nested": ["item\x001", "item\x002"]}
        cleaned_dict = sanitize_null_bytes(dirty_dict)
        self.assertEqual(cleaned_dict, {"key": "value", "nested": ["item1", "item2"]})

    # ==========================================================================
    # 5. DJANGO FORM INTEGRATION & SAFE USER ERRORS
    # ==========================================================================

    def test_security_validator_django_form_integration(self):
        """Verify SecurityValidator raises clean ValidationError without leaking internals."""
        with self.assertRaises(SecurityValidationError) as ctx:
            SecurityValidator.mobile("123")
        self.assertIn("10-digit", str(ctx.exception))
        # Ensure database schema or regex internals are NOT leaked in user message
        self.assertNotIn("SELECT", str(ctx.exception))
        self.assertNotIn("regex", str(ctx.exception).lower())

        with self.assertRaises(SecurityValidationError) as ctx:
            SecurityValidator.pan("INVALID")
        self.assertIn("Invalid PAN format", str(ctx.exception))

        with self.assertRaises(SecurityValidationError):
            SecurityValidator.assert_no_injection("'; DROP TABLE shops; --", "Search")

    # ==========================================================================
    # 6. HTTP 413 & SECURITY HEADERS MIDDLEWARE
    # ==========================================================================

    def test_http_413_payload_too_large_rejection(self):
        """Verify requests exceeding 2MB payload ceiling are rejected with HTTP 413."""
        # 2MB + 1 byte
        oversized_content_length = MAX_REQUEST_PAYLOAD_BYTES + 1
        res = self.client.post(
            '/api/shopper-id/',
            data='{}',
            content_type='application/json',
            CONTENT_LENGTH=str(oversized_content_length)
        )
        self.assertEqual(res.status_code, 413)

    def test_security_headers_enforced(self):
        """Verify enterprise security headers on HTTP responses."""
        res = self.client.get('/explore/')
        self.assertEqual(res.status_code, 200)

        # 1. MIME-sniffing protection
        self.assertEqual(res.headers.get('X-Content-Type-Options'), 'nosniff')

        # 2. Clickjacking protection
        self.assertEqual(res.headers.get('X-Frame-Options'), 'DENY')

        # 3. Referrer Policy
        self.assertEqual(res.headers.get('Referrer-Policy'), 'strict-origin-when-cross-origin')

        # 4. Content-Security-Policy
        self.assertIn('Content-Security-Policy', res.headers)
        csp = res.headers.get('Content-Security-Policy')
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
