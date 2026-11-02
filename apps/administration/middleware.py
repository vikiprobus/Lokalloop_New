import traceback
import logging
from django.utils.deprecation import MiddlewareMixin
from apps.administration.models import ErrorLog, ErrorLogLevel
from services.security_logger import (
    generate_incident_id,
    scrub_traceback,
    scrub_log_message,
    mask_sensitive_payload
)

logger = logging.getLogger(__name__)


class ExceptionLoggingMiddleware(MiddlewareMixin):
    """
    Enterprise Exception Logging Middleware:
    1. Generates cryptographically unique Incident Reference IDs.
    2. Scrubs sensitive credentials, tokens, and PII from tracebacks and payloads.
    3. Persists diagnostic error logs with automatic 15-day retention.
    4. Seamlessly isolates frontend users by delegating to custom_500_view.
    """
    def __init__(self, get_response=None):
        super().__init__(get_response)

    def process_exception(self, request, exception):
        from django.core.exceptions import PermissionDenied
        from django.http import Http404

        if isinstance(exception, (PermissionDenied, Http404)):
            return None

        incident_id = getattr(request, 'incident_id', None)
        if not incident_id:
            incident_id = generate_incident_id()
            request.incident_id = incident_id

        try:
            # Enforce 15-day retention cleanup
            ErrorLog.purge_old_errors(days=15)

            user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
            ip = request.META.get('HTTP_X_FORWARDED_FOR')
            if ip:
                ip = ip.split(',')[0].strip()
            else:
                ip = request.META.get('REMOTE_ADDR')

            user_agent = request.META.get('HTTP_USER_AGENT', '')[:490]
            tb_str = traceback.format_exc()

            # PII Redaction & Sensitive Data Masking:
            sanitized_message = scrub_log_message(str(exception) or 'An unexpected error occurred.')
            sanitized_traceback = scrub_traceback(tb_str)

            # Scrub POST payload context if present
            if hasattr(request, 'POST') and request.POST:
                try:
                    masked_post = mask_sensitive_payload(request.POST.dict())
                    sanitized_traceback = f"--- REQUEST POST DATA ---\n{masked_post}\n\n--- TRACEBACK ---\n{sanitized_traceback}"
                except Exception:
                    pass

            ErrorLog.objects.create(
                incident_id=incident_id,
                level=ErrorLogLevel.ERROR,
                error_type=exception.__class__.__name__,
                message=sanitized_message,
                traceback=sanitized_traceback,
                path=request.path[:490],
                method=request.method,
                ip_address=ip,
                user=user,
                user_agent=user_agent,
                is_resolved=False
            )
        except Exception as exc:
            # Secondary safety net: never let logging exceptions crash error handling
            logger.error("Failed to record ErrorLog for %s: %s", incident_id, exc)

        # Delegate to custom_500_view for clean user response
        from apps.administration.views import custom_500_view
        return custom_500_view(request)
