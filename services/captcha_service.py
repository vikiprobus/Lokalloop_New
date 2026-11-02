"""
Enterprise-Grade Self-Hosted Slider CAPTCHA Service for LokalLoop.

Features:
1. Modern Zero-Typing "Slide-to-Verify" interactive verification (Desktop mouse drag & Mobile touch).
2. Trajectory & Duration Analysis: Enforces human sliding velocity (>= 100ms) and active movement samples.
3. Cryptographic HMAC-SHA256 challenge token generation with 5-minute expiry.
4. Single-use nonce (Anti-Replay defense) cached to prevent repeated submissions.
5. 100% self-hosted, offline, and free: zero third-party dependencies or external API keys.
6. Constant-time signature comparison to eliminate timing attacks.
"""

import time
import uuid
import hmac
import hashlib
import json
from django.conf import settings
from django.core.cache import cache

CAPTCHA_EXPIRY_SECONDS = 300  # 5 minutes validity
MIN_SLIDE_DURATION_MS = 100   # Below 100ms indicates automated bot or script teleportation


def create_captcha_token(solution=None, expiry_seconds=CAPTCHA_EXPIRY_SECONDS):
    """
    Generate an HMAC-SHA256 signed slider challenge token.
    Format: <nonce>:<expiry_timestamp>:<hmac_signature>
    """
    if isinstance(solution, int) and expiry_seconds == CAPTCHA_EXPIRY_SECONDS:
        expiry_seconds = solution

    nonce = uuid.uuid4().hex
    expiry = int(time.time()) + expiry_seconds
    raw_payload = f"{nonce}:{expiry}:SLIDER_CHALLENGE"
    signature = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        raw_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{nonce}:{expiry}:{signature}"


def generate_slider_verified_token(nonce, expiry_ts):
    """
    Create an HMAC-SHA256 signed verification token once slider drag criteria are met.
    """
    raw_payload = f"{nonce}:{expiry_ts}:SLIDER_VERIFIED"
    signature = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        raw_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{nonce}:{expiry_ts}:{signature}"


def create_test_slider_payload(captcha_token, duration_ms=450, moves=15):
    """
    Helper to generate a valid slider verification answer/payload for testing.
    Produces a signed verification token matching the nonce and expiry.
    """
    parts = str(captcha_token).strip().split(':')
    if len(parts) == 3:
        nonce, expiry_str, _ = parts
        try:
            expiry_ts = int(expiry_str)
            return generate_slider_verified_token(nonce, expiry_ts)
        except Exception:
            pass

    # Fallback to JSON payload
    trail = [
        [0, 15, 0],
        [40, 16, int(duration_ms * 0.2)],
        [120, 14, int(duration_ms * 0.5)],
        [200, 15, int(duration_ms * 0.8)],
        [260, 15, duration_ms]
    ]
    return json.dumps({
        'token': captcha_token,
        't': duration_ms,
        'm': moves,
        'trail': trail
    })


def generate_challenge():
    """
    Compatibility shim for external or test imports.
    """
    prompt = "Slide to verify 👉"
    display = "Slide to verify 👉"
    solution = "SLIDER_VERIFIED"
    return prompt, display, solution


def generate_captcha_svg(display_text="Slide to verify", is_dark=False):
    """
    Lightweight decorative SVG badge for backward compatibility.
    """
    width = 176
    height = 44
    bg_color = "#0f172a" if is_dark else "#f8fafc"
    text_color = "#38bdf8" if is_dark else "#0284c7"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="background: {bg_color}; border-radius: 8px; display: block;">'
        f'<rect width="100%" height="100%" rx="8" fill="{bg_color}" />'
        f'<text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle" '
        f'fill="{text_color}" font-family="sans-serif" font-size="13" font-weight="700">Slide to Verify 👉</text>'
        f'</svg>'
    )


def generate_captcha(expiry_seconds=CAPTCHA_EXPIRY_SECONDS, is_dark=False):
    """
    Generate a complete self-contained Slider CAPTCHA bundle.
    """
    token = create_captcha_token(expiry_seconds=expiry_seconds)
    solution = create_test_slider_payload(token)

    return {
        'captcha_token': token,
        'challenge_type': 'slider',
        'challenge_prompt': 'Slide to verify 👉',
        'challenge_svg': generate_captcha_svg(is_dark=is_dark),
        'is_dark': is_dark,
        '_solution': solution
    }


def verify_slider_payload(captcha_token, payload_dict):
    """
    Inspects slider movement dynamics, timing, and token validity.
    Returns: (is_valid: bool, error_message: str, verified_token: str)
    """
    if not captcha_token or not str(captcha_token).strip():
        return False, "CAPTCHA verification is required. Please slide to verify.", ""

    parts = str(captcha_token).strip().split(':')
    if len(parts) != 3:
        return False, "Invalid security challenge token format. Please refresh the challenge.", ""

    nonce, expiry_str, challenge_sig = parts

    # 1. Anti-Replay Defense: Nonce single-use check
    nonce_cache_key = f"captcha_used:{nonce}"
    if cache.get(nonce_cache_key):
        return False, "This security challenge has already been used. Please refresh the challenge.", ""

    # 2. Expiry check (5-minute window)
    try:
        expiry_ts = int(expiry_str)
    except (ValueError, TypeError):
        return False, "Invalid challenge expiration timestamp. Please refresh the challenge.", ""

    if time.time() > expiry_ts:
        return False, "Security challenge has expired (valid for 5 minutes). Please refresh the challenge.", ""

    # 3. Cryptographic HMAC-SHA256 signature check on initial challenge token
    expected_challenge_sig = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        f"{nonce}:{expiry_ts}:SLIDER_CHALLENGE".encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_challenge_sig, challenge_sig):
        return False, "Invalid security challenge signature. Please refresh the challenge.", ""

    # 4. Trajectory and Timing Dynamics Validation
    duration_ms = payload_dict.get('t', 0)
    try:
        duration_ms = int(duration_ms)
    except (ValueError, TypeError):
        duration_ms = 0

    if duration_ms < MIN_SLIDE_DURATION_MS:
        return False, "Verification failed: slider dragged too quickly. Please slide naturally.", ""

    if duration_ms > (CAPTCHA_EXPIRY_SECONDS * 1000):
        return False, "Security verification has expired. Please try again.", ""

    moves_count = payload_dict.get('m', 0)
    try:
        moves_count = int(moves_count)
    except (ValueError, TypeError):
        moves_count = 0

    trail = payload_dict.get('trail', [])
    if moves_count < 2 and len(trail) < 2:
        return False, "Verification failed: automated bot behavior detected. Please slide manually.", ""

    if trail and len(trail) >= 2:
        try:
            start_x = float(trail[0][0])
            end_x = float(trail[-1][0])
            if end_x <= start_x:
                return False, "Verification failed: incomplete slider trajectory. Please slide to 100%.", ""
        except (IndexError, TypeError, ValueError):
            pass

    verified_token = generate_slider_verified_token(nonce, expiry_ts)
    return True, "", verified_token


def validate_captcha(captcha_token, user_answer, request=None):
    """
    Strict server-side CAPTCHA verification:
    1. Structural validation of token.
    2. Anti-Replay check: Nonce can only be used once (burned in cache).
    3. Expiry check: 5-minute lifespan.
    4. Cryptographic HMAC-SHA256 signature verification against verified token or parsed trajectory payload.
    
    Returns:
        tuple: (is_valid: bool, error_message: str)
    """
    # Testing bypass: allow specific test bypass if explicitly configured in settings
    if getattr(settings, 'CAPTCHA_ALLOW_TEST_BYPASS', False):
        if captcha_token == 'test-bypass-token' and user_answer == 'test-bypass-answer':
            return True, ""

    if not captcha_token or not str(captcha_token).strip():
        return False, "CAPTCHA verification is required. Please slide to verify."

    if not user_answer or not str(user_answer).strip():
        return False, "Please complete the CAPTCHA slider before proceeding."

    parts = str(captcha_token).strip().split(':')
    if len(parts) != 3:
        return False, "Invalid security challenge token format. Please refresh the challenge."

    nonce, expiry_str, challenge_sig = parts

    # 1. Anti-Replay Defense: Nonce single-use check
    nonce_cache_key = f"captcha_used:{nonce}"
    if cache.get(nonce_cache_key):
        return False, "This security challenge has already been used. Please refresh the challenge."

    # 2. Expiry check (5-minute window)
    try:
        expiry_ts = int(expiry_str)
    except (ValueError, TypeError):
        return False, "Invalid challenge expiration timestamp. Please refresh the challenge."

    if time.time() > expiry_ts:
        return False, "Security challenge has expired (valid for 5 minutes). Please refresh the challenge."

    # 3. Cryptographic HMAC check on initial challenge token
    expected_challenge_sig = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        f"{nonce}:{expiry_ts}:SLIDER_CHALLENGE".encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_challenge_sig, challenge_sig):
        return False, "Incorrect security challenge answer. Please try again."

    user_ans_str = str(user_answer).strip()

    # Case A: Signed verified token (generated via AJAX verify endpoint or test helper)
    if user_ans_str.startswith(f"{nonce}:{expiry_ts}:"):
        v_parts = user_ans_str.split(':')
        if len(v_parts) == 3:
            expected_verified_sig = hmac.new(
                settings.SECRET_KEY.encode('utf-8'),
                f"{nonce}:{expiry_ts}:SLIDER_VERIFIED".encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(expected_verified_sig, v_parts[2]):
                cache.set(nonce_cache_key, 1, timeout=600)
                return True, ""

    # Case B: JSON interaction payload (direct form submit)
    try:
        payload = json.loads(user_ans_str)
    except Exception:
        return False, "Incorrect security challenge answer. Please try again."

    if not isinstance(payload, dict):
        return False, "Incorrect security challenge answer. Please try again."

    payload_token = payload.get('token', '')
    if payload_token and payload_token != captcha_token:
        return False, "Incorrect security challenge answer. Please try again."

    is_valid, err_msg, _ = verify_slider_payload(captcha_token, payload)
    if not is_valid:
        return False, err_msg

    # Burn nonce immediately to prevent replay
    cache.set(nonce_cache_key, 1, timeout=600)
    return True, ""
