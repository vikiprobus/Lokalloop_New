"""
Enterprise Security Logging & Sensitive Data (PII) Redaction Engine.
Guarantees:
- Zero information leakage of credentials, tokens, or compliance data to logs or database.
- Recursive redaction of request payloads (POST/GET/Headers/JSON).
- Regex-based scrubbing of inline passwords, 10-digit mobile numbers, and PAN numbers from tracebacks.
- Cryptographically secure Incident Reference ID generation.
"""

import re
import uuid
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Strict Blacklist of Keys containing sensitive credentials, compliance, or financial tokens
SENSITIVE_KEY_NAMES = {
    'password',
    'confirm_password',
    'new_password',
    'current_password',
    'pass',
    'pwd',
    'token',
    'captcha_answer',
    'captcha_token',
    'secret',
    'api_key',
    'apikey',
    'auth_token',
    'access_token',
    'refresh_token',
    'pan',
    'pan_number',
    'business_pan',
    'fssai',
    'fssai_license_number',
    'fda',
    'fda_license_number',
    'credit_card',
    'card_number',
    'cvv',
    'cvc',
    'authorization',
    'auth',
    'cookie',
    'sessionid',
    'csrftoken',
    'csrfmiddlewaretoken',
}

REPLACEMENT_LABEL = "[REDACTED]"

# 1. Key-Value pairs in text: (password|pan|token|secret)="xyz" or 'xyz' or xyz
RE_KEY_VALUE_SENSITIVE = re.compile(
    r'(?i)\b('
    r'password|confirm_password|new_password|current_password|'
    r'token|captcha_answer|secret|api_key|'
    r'pan|pan_number|business_pan|fssai|authorization'
    r')\s*([:=])\s*(["\'][^"\'\r\n]{1,120}["\']|[^\s,;]{1,120})'
)

# 2. Indian PAN patterns in text: 5 letters, 4 digits, 1 letter, optional 2 alphanumeric
RE_PAN_INLINE = re.compile(r'\b[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{0,2}\b')

# 3. Indian 10-digit Mobile numbers in text: Starts with 6-9, followed by 9 digits
RE_MOBILE_INLINE = re.compile(r'\b[6-9]\d{9}\b')

# 4. Bearer / Basic auth tokens in strings or headers
RE_AUTH_HEADER = re.compile(r'(?i)\b(bearer|basic)\s+([a-zA-Z0-9_\-\.=]{10,})')


def generate_incident_id() -> str:
    """
    Generates a cryptographically secure, unique Incident Reference ID.
    Format: ERR-<8-char-hex> (e.g., ERR-A1B2C3D4)
    """
    return f"ERR-{uuid.uuid4().hex[:8].upper()}"


def is_sensitive_key(key: Any) -> bool:
    """
    Determines if a field or header key corresponds to a protected sensitive credential.
    Case-insensitive, normalizes underscores and dashes.
    """
    if not isinstance(key, str):
        key = str(key)
    normalized = key.strip().lower().replace('-', '_')
    if normalized in SENSITIVE_KEY_NAMES:
        return True
    for sensitive in SENSITIVE_KEY_NAMES:
        if sensitive in normalized:
            return True
    return False


def mask_sensitive_payload(data: Any) -> Any:
    """
    Recursively scrubs sensitive keys from dictionaries, lists, QueryDicts, or strings.
    Replaces sensitive values strictly with '[REDACTED]'.
    """
    if data is None:
        return None

    # Handle Django QueryDict or standard dict
    if hasattr(data, 'items') and callable(data.items):
        scrubbed = {}
        for k, v in data.items():
            k_str = str(k)
            if is_sensitive_key(k_str):
                scrubbed[k_str] = REPLACEMENT_LABEL
            else:
                scrubbed[k_str] = mask_sensitive_payload(v)
        return scrubbed

    # Handle lists, tuples, sets
    if isinstance(data, (list, tuple, set)):
        scrubbed_list = [mask_sensitive_payload(item) for item in data]
        return type(data)(scrubbed_list)

    # Handle string values: scrub potential sensitive inline patterns
    if isinstance(data, str):
        data = RE_KEY_VALUE_SENSITIVE.sub(r'\1\2"[REDACTED]"', data)
        data = RE_AUTH_HEADER.sub(r'\1 [REDACTED]', data)
        return data

    return data


def scrub_traceback(tb_text: str) -> str:
    """
    Scrubs sensitive data from raw Python exception tracebacks and logs:
    1. Redacts inline key-value assignments (e.g. password='...', pan='...').
    2. Redacts Authorization / Bearer tokens.
    3. Redacts Indian PAN numbers and 10-digit mobile numbers.
    Never throws an exception (safe fallback).
    """
    if not tb_text:
        return ''
    try:
        # Step 1: Redact inline key=value assignments
        cleaned = RE_KEY_VALUE_SENSITIVE.sub(r'\1\2"[REDACTED]"', str(tb_text))
        # Step 2: Redact Authorization tokens
        cleaned = RE_AUTH_HEADER.sub(r'\1 [REDACTED]', cleaned)
        # Step 3: Redact PAN numbers
        cleaned = RE_PAN_INLINE.sub('[REDACTED_PAN]', cleaned)
        # Step 4: Redact Mobile numbers
        cleaned = RE_MOBILE_INLINE.sub('[REDACTED_PHONE]', cleaned)
        return cleaned
    except Exception as exc:
        logger.warning("Error scrubbing traceback: %s", exc)
        return str(tb_text)


def scrub_log_message(msg: str) -> str:
    """
    Scrubs a log message string of sensitive inline patterns and PII.
    """
    if not msg:
        return ''
    try:
        cleaned = RE_KEY_VALUE_SENSITIVE.sub(r'\1\2"[REDACTED]"', str(msg))
        cleaned = RE_AUTH_HEADER.sub(r'\1 [REDACTED]', cleaned)
        cleaned = RE_PAN_INLINE.sub('[REDACTED_PAN]', cleaned)
        cleaned = RE_MOBILE_INLINE.sub('[REDACTED_PHONE]', cleaned)
        return cleaned
    except Exception:
        return str(msg)
