import time
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin


def get_client_ip(request):
    """
    Extract client IP address respecting X-Forwarded-For header for reverse proxies.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '')
    return ip or '127.0.0.1'


class RateLimitMiddleware(MiddlewareMixin):
    """
    High-Performance In-Memory Rate Limiting Middleware.
    Protects authentication, QR scanning, and search APIs against scrapers, automated bots,
    and brute-force attacks without requiring external paid WAF services.
    """

    # Local loopback & trusted development IPs
    DEFAULT_WHITELIST = {'127.0.0.1', '::1', 'localhost'}

    # Rate Limit Rule Definitions:
    # Prefix/Route -> (max_requests, window_seconds)
    RATE_LIMIT_RULES = [
        # Strict API & Checkout / Scan protection
        ('/buyer/scan/', (5, 10)),
        ('/api/shopper-id/', (5, 10)),
        ('/buyer/set-nickname/', (10, 60)),
        ('/api/seller/', (60, 60)),  # Merchant counter polling (BUG-1.4)
        ('/api/', (15, 60)),
        
        # Authentication & Brute-force protection
        ('/buyer/login/', (5, 60)),
        ('/seller/login/', (5, 60)),
        ('/admin-login/', (5, 60)),
        ('/buyer/recover/', (5, 60)),
        ('/buyer/register/', (8, 60)),
        ('/seller/register/', (8, 60)),

        # General browsing & catalog exploration
        ('/explore/', (30, 10)),
        ('/', (30, 10)),
    ]

    def _get_matched_rule(self, path):
        """
        Match exact path or prefix against configured rate limiting rules.
        Returns (route_prefix, limit_config) or (None, None).
        """
        for route_prefix, limit_config in self.RATE_LIMIT_RULES:
            if route_prefix == '/' and path == '/':
                return route_prefix, limit_config
            elif route_prefix != '/' and path.startswith(route_prefix):
                return route_prefix, limit_config
        return None, None

    def process_request(self, request):
        ip = get_client_ip(request)

        # Allow bypass for whitelisted local loopback when explicitly permitted
        # Only whitelist loopback if settings.RATE_LIMIT_WHITELIST_LOCAL is True (default True in DEBUG)
        whitelist_local = getattr(settings, 'RATE_LIMIT_WHITELIST_LOCAL', getattr(settings, 'DEBUG', False))
        trusted_ips = getattr(settings, 'RATE_LIMIT_WHITELIST_IPS', set(self.DEFAULT_WHITELIST))

        # Check if IP is explicitly whitelisted
        if whitelist_local and ip in trusted_ips:
            # Check if testing flag is enabled to allow tests to enforce rate limits
            if not getattr(request, '_test_rate_limit_enforce', False):
                return None

        path = request.path

        # Static, media, and favicon assets are never rate-limited
        if path.startswith('/static/') or path.startswith('/media/') or path == '/favicon.ico':
            return None

        matched_prefix, rule = self._get_matched_rule(path)
        if not rule:
            return None

        max_requests, window_seconds = rule
        # BUG-99: Key rate limit bucket on matched prefix to prevent query param & sub-path bypass
        cache_key = f"rl:{ip}:{matched_prefix}"

        current_time = time.time()
        # Retrieve list of timestamps for this client key
        timestamps = cache.get(cache_key, [])

        # Filter out timestamps outside the sliding window
        window_start = current_time - window_seconds
        valid_timestamps = [ts for ts in timestamps if ts > window_start]

        if len(valid_timestamps) >= max_requests:
            # Rate limit exceeded -> Return HTTP 429
            retry_after = max(1, int(window_seconds - (current_time - valid_timestamps[0])))

            is_api = (
                request.path.startswith('/api/') or
                request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                'application/json' in request.headers.get('accept', '').lower()
            )

            if is_api:
                response = JsonResponse({
                    'error': 'Too Many Requests',
                    'detail': 'Too many attempts. Please slow down and try again in a few seconds.',
                    'retry_after': retry_after,
                }, status=429)
            else:
                try:
                    html_content = render_to_string('429.html', {
                        'retry_after': retry_after,
                        'request': request
                    }, request=request)
                    response = HttpResponse(html_content, status=429)
                except Exception:
                    response = HttpResponse(
                        "Too many attempts. Please slow down and try again in a few seconds.",
                        status=429
                    )

            response['Retry-After'] = str(retry_after)
            return response

        # Add current timestamp and store with TTL
        valid_timestamps.append(current_time)
        cache.set(cache_key, valid_timestamps, timeout=window_seconds + 5)

        return None


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Enforces enterprise-grade HTTP security headers across all responses:
    - X-Content-Type-Options: nosniff (MIME-sniffing protection)
    - X-Frame-Options: DENY (Clickjacking protection)
    - X-XSS-Protection: 1; mode=block (Reflected XSS filter)
    - Referrer-Policy: strict-origin-when-cross-origin (Privacy header)
    - Permissions-Policy: camera=(self), geolocation=(self), microphone=() (Hardware restriction)
    - Content-Security-Policy: Restricts untrusted script execution and framing
    """

    def process_response(self, request, response):
        if not response.has_header('X-Content-Type-Options'):
            response['X-Content-Type-Options'] = 'nosniff'
        if not response.has_header('X-Frame-Options'):
            response['X-Frame-Options'] = 'DENY'
        if not response.has_header('X-XSS-Protection'):
            response['X-XSS-Protection'] = '1; mode=block'
        if not response.has_header('Referrer-Policy'):
            response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if not response.has_header('Permissions-Policy'):
            response['Permissions-Policy'] = 'camera=(self), geolocation=(self), microphone=()'
        if not response.has_header('Content-Security-Policy'):
            response['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
                "img-src 'self' data: https: blob:; "
                "connect-src 'self' https://nominatim.openstreetmap.org; "
                "frame-ancestors 'none'; "
                "base-uri 'self';"
            )
        return response


class ApiSecurityMiddleware(MiddlewareMixin):
    """
    Comprehensive Enterprise API & Input Security Middleware:
    1. Blocks automated vulnerability scanners (sqlmap, nikto, masscan, acunetix, etc.).
    2. Enforces O(1) payload size limit (rejects oversized payloads > 2MB with HTTP 413).
    3. ReDoS-immune linear inspection: blocks SQL injection, XSS, and Path Traversal in query params.
    4. Enforces strict anti-caching & MIME-sniffing protection on all API endpoints.
    """

    BLOCKED_USER_AGENTS = (
        'sqlmap', 'nikto', 'dirbuster', 'masscan', 'wpscan', 'acunetix', 'nmap', 'havij'
    )

    def process_request(self, request):
        path = request.path
        is_api = (
            path.startswith('/api/') or
            request.headers.get('x-requested-with') == 'XMLHttpRequest' or
            'application/json' in request.headers.get('accept', '').lower()
        )

        # 1. Block known malicious vulnerability scanners
        user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
        if any(tool in user_agent for tool in self.BLOCKED_USER_AGENTS):
            return JsonResponse({'error': 'Forbidden', 'detail': 'Access denied.'}, status=403)

        # 2. Enforce O(1) payload size limit on all mutating requests (2MB standard, 12MB multipart photo uploads - BUG-98)
        if request.method in ('POST', 'PUT', 'PATCH'):
            content_length = request.META.get('CONTENT_LENGTH')
            if content_length:
                try:
                    from services.security_validator import MAX_REQUEST_PAYLOAD_BYTES, MAX_MULTIPART_PAYLOAD_BYTES
                    content_type = request.META.get('CONTENT_TYPE', '').lower()
                    is_multipart = 'multipart/form-data' in content_type
                    payload_ceiling = MAX_MULTIPART_PAYLOAD_BYTES if is_multipart else MAX_REQUEST_PAYLOAD_BYTES

                    if int(content_length) > payload_ceiling:
                        limit_mb = payload_ceiling // (1024 * 1024)
                        if is_api:
                            return JsonResponse(
                                {'error': 'Payload Too Large', 'detail': f'Request payload exceeds {limit_mb}MB limit.'},
                                status=413
                            )
                        return HttpResponse(
                            f"413 Payload Too Large: Request entity exceeds {limit_mb}MB ceiling.",
                            status=413,
                            content_type="text/plain"
                        )
                except (ValueError, TypeError):
                    pass

        # 3. Deep malicious pattern inspection in query string (ReDoS-immune linear engine)
        import urllib.parse
        from services.security_validator import detect_malicious_payload, LENGTH_LIMITS

        query_string = request.META.get('QUERY_STRING', '')
        if query_string:
            if len(query_string) > LENGTH_LIMITS['query_string']:
                if is_api:
                    return JsonResponse({'error': 'Bad Request', 'detail': 'Query string exceeds maximum safe limit.'}, status=400)
                return HttpResponse("Bad Request: Query string exceeds maximum safe limit.", status=400)

            decoded_query = urllib.parse.unquote_plus(query_string)
            has_attack, attack_reason = detect_malicious_payload(decoded_query)
            if has_attack:
                if is_api:
                    return JsonResponse({'error': 'Bad Request', 'detail': f'Malicious payload blocked: {attack_reason}'}, status=400)
                return HttpResponse(f"Bad Request: Malicious payload blocked.", status=400)

        return None

    def process_response(self, request, response):
        path = request.path
        is_api = (
            path.startswith('/api/') or
            isinstance(response, JsonResponse) or
            'application/json' in response.get('Content-Type', '').lower()
        )

        if is_api:
            # Enforce strict anti-caching on all API data
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            response['X-Content-Type-Options'] = 'nosniff'

        return response




class UserNavigationStateMiddleware(MiddlewareMixin):
    """
    Smart Navigation State & Session Memory Middleware for Mobile, PWA & Android.
    Preserves the user's last visited authenticated page across app restarts,
    allowing automatic restoration to the exact screen they were viewing.
    """
    TRACKED_PREFIXES = ('/buyer/', '/seller/', '/admin-portal/', '/explore/')
    EXCLUDED_SUBSTRINGS = ('login', 'logout', 'recover', 'delete-account', 'static', 'media', 'api', 'favicon')

    def process_response(self, request, response):
        if request.method != 'GET' or response.status_code != 200:
            return response

        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return response

        is_api = (
            request.path.startswith('/api/') or
            isinstance(response, JsonResponse) or
            request.headers.get('x-requested-with') == 'XMLHttpRequest' or
            'application/json' in response.get('Content-Type', '').lower()
        )
        if is_api:
            return response

        path = request.path
        if any(path.startswith(prefix) for prefix in self.TRACKED_PREFIXES):
            if not any(excluded in path for excluded in self.EXCLUDED_SUBSTRINGS):
                if hasattr(request, 'session'):
                    request.session['last_visited_url'] = path
                response.set_cookie(
                    'last_visited_url',
                    path,
                    max_age=86400 * 30,
                    samesite='Lax',
                    httponly=False
                )

        return response
