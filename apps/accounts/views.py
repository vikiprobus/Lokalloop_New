CITY_DEFAULT_COORDS = {
    'pune': (18.5204, 73.8567),
    'mumbai': (19.0760, 72.8777),
    'bengaluru': (12.9716, 77.5946),
    'bangalore': (12.9716, 77.5946),
    'delhi': (28.6139, 77.2090),
    'hyderabad': (17.3850, 78.4867),
    'chennai': (13.0827, 80.2707),
    'kolkata': (22.5726, 88.3639),
    'ahmedabad': (23.0225, 72.5714),
    'nagpur': (21.1458, 79.0882),
    'thane': (19.2183, 72.9781),
    'nashik': (19.9975, 73.7898),
    'chhatrapati sambhajinagar': (19.8762, 75.3433),
    'aurangabad': (19.8762, 75.3433),
    'solapur': (17.6599, 75.9064),
    'kolhapur': (16.7050, 74.2433),
    'navi mumbai': (19.0330, 73.0297),
}

def get_city_default_coords(city_name: str) -> tuple[float, float]:
    if not city_name:
        return (18.5204, 73.8567)
    key = str(city_name).strip().lower()
    return CITY_DEFAULT_COORDS.get(key, (18.5204, 73.8567))

from apps.offers.models import Offer, OfferStatus
from apps.redemptions.models import OfferRedemption, RedemptionStatus, QRCode
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

import json
import secrets
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.core.cache import cache
from django.contrib.auth.hashers import make_password, check_password
from config.middleware import get_client_ip
from apps.administration.models import AdminAction
from .decorators import buyer_required
from .models import Profile, Role, BuyerProfile, SellerProfile, AccountDeletionLog, DeletionRole, DeletionReasonCategory
from .forms import (
    AnonymousBuyerRegistrationForm,
    BuyerRecoveryLookupForm,
    BuyerRecoveryResetForm,
    BuyerRegistrationForm,
    SellerRegistrationForm,
    SellerRecoveryLookupForm,
    SellerRecoveryResetForm,
    LoginForm,
    ChangePasswordForm,
    SellerAccountDeletionForm,
    BuyerAccountDeletionForm
)
from .shopper_utils import (
    SECURITY_QUESTIONS,
    INDIAN_DISTRICTS,
    MAHARASHTRA_DISTRICTS,
    MAHARASHTRA_DISTRICT_CODES,
    calculate_age,
    get_age_band_code,
    generate_internal_composite_id,
    generate_public_unique_id,
    generate_unique_shopper_id
)
from apps.shops.models import Shop, ShopCategory, ShopSubcategory, ShopVerification
from services.wallet_service import get_or_create_wallet
from services.reward_service import get_or_create_reward_account
from services.notification_service import create_notification
from services.captcha_service import generate_captcha, validate_captcha, verify_slider_payload
from apps.accounts.utils import generate_unique_merchant_id


def refresh_captcha_api(request):
    """
    Secure AJAX endpoint to refresh CAPTCHA challenge without reloading the page.
    """
    is_dark = request.GET.get('dark') == '1'
    captcha = generate_captcha(is_dark=is_dark)
    response = JsonResponse({
        'success': True,
        'captcha_token': captcha['captcha_token'],
        'challenge_type': 'slider',
        'challenge_prompt': captcha.get('challenge_prompt', 'Slide to verify 👉'),
        'challenge_svg': captcha.get('challenge_svg', ''),
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@csrf_exempt
def verify_slider_captcha_api(request):
    """
    Secure AJAX endpoint called when the slider reaches 100%.
    Validates interaction trajectory, duration, and client token.
    Issues a server-signed verified token.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed.'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    captcha_token = data.get('captcha_token') or data.get('token')
    if not captcha_token:
        return JsonResponse({'success': False, 'error': 'Missing captcha token.'}, status=400)

    is_valid, error_msg, verified_token = verify_slider_payload(captcha_token, data)
    if not is_valid:
        return JsonResponse({'success': False, 'error': error_msg}, status=400)

    response = JsonResponse({
        'success': True,
        'verified_token': verified_token,
        'message': 'Slider verification successful.'
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def generate_shopper_id_api(request):
    """
    Secure AJAX endpoint to get a 3-block Public Unique ID (e.g. LQ7M-8K2R-5D9X)
    with strict parameter validation, sanitization, and anti-caching headers.
    """
    from .shopper_utils import sanitize_user_input, MAHARASHTRA_DISTRICT_CODES

    # IP rate-limiting: maximum 60 ID generations per minute
    ip = get_client_ip(request)
    cache_key = f"rl:shopper_id:{ip}"
    request_count = cache.get(cache_key, 0)
    if request_count >= 60:
        response = JsonResponse({
            'error': 'Rate limit exceeded. Please wait 1 minute before generating more IDs.',
            'retry_after': 60
        }, status=429)
        response['Retry-After'] = '60'
        return response
    cache.set(cache_key, request_count + 1, timeout=60)

    # Strict validation of district input against known districts
    raw_district = request.GET.get('district', 'Pune').strip()
    sanitized_district = sanitize_user_input(raw_district, max_length=50)
    district = sanitized_district if sanitized_district in MAHARASHTRA_DISTRICT_CODES else 'Pune'

    # Strict sanitization of name input
    raw_name = request.GET.get('first_name', 'Vikram').strip() or 'Vikram'
    first_name = sanitize_user_input(raw_name, max_length=30, allow_alphanumeric_only=True) or 'Vikram'

    birth_year = request.GET.get('birth_year')
    birth_month = request.GET.get('birth_month', 1)

    if birth_year:
        try:
            b_year = int(birth_year)
            b_month = int(birth_month)
            if 1920 <= b_year <= 2026 and 1 <= b_month <= 12:
                age = calculate_age(b_year, b_month)
            else:
                age = 22
        except (ValueError, TypeError):
            age = 22
    else:
        try:
            val_age = int(request.GET.get('age', 22))
            age = val_age if 1 <= val_age <= 120 else 22
        except (ValueError, TypeError):
            age = 22

    internal_composite = generate_internal_composite_id(district, age, first_name)
    public_id = generate_public_unique_id(internal_composite)
    legacy_shopper_id = generate_unique_shopper_id(district)

    response = JsonResponse({
        'shopper_id': legacy_shopper_id,
        'public_unique_id': public_id,
        'unique_id': public_id,
        'district': district,
        'age': age
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def buyer_register_view(request):
    if request.user.is_authenticated:
        return redirect('/buyer/dashboard/')

    if request.method == 'POST':
        form = AnonymousBuyerRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                first_name = form.cleaned_data.get('first_name', '').strip()
                gender = form.cleaned_data.get('gender', '').strip().upper()
                birth_month = form.cleaned_data.get('birth_month') or 1
                birth_year = form.cleaned_data.get('birth_year') or 2000
                age = calculate_age(birth_year, birth_month)
                state = form.cleaned_data.get('state', 'Maharashtra')
                district = form.cleaned_data.get('district', 'Pune')
                password = form.cleaned_data['password']
                sec_question = form.cleaned_data.get('security_question', '')
                sec_answer = form.cleaned_data.get('security_answer', '').strip().lower()
                sec_answer_hash = make_password(sec_answer) if sec_answer else ''

                # Generate algorithmic internal composite ID & public 3-block unique ID
                internal_composite_id = generate_internal_composite_id(district, age, first_name)
                public_unique_id = generate_public_unique_id(internal_composite_id)

                # Support legacy shopper_id passed or default to public_unique_id
                submitted_shopper_id = form.cleaned_data.get('shopper_id', '').strip().upper()
                if submitted_shopper_id and not User.objects.filter(username__iexact=submitted_shopper_id).exists():
                    primary_username = submitted_shopper_id
                else:
                    primary_username = public_unique_id

                internal_email = f"{public_unique_id.lower().replace('-', '_')}@anon.lokalloop.internal"

                user = User.objects.create_user(
                    username=primary_username,
                    email=internal_email,
                    password=password,
                    first_name=first_name,
                    last_name="Shopper"
                )
                Profile.objects.create(
                    user=user,
                    role=Role.BUYER,
                    phone="",
                    city=district
                )
                device_token = secrets.token_hex(32)
                buyer_profile = BuyerProfile.objects.create(
                    user=user,
                    shopper_id=primary_username,
                    public_unique_id=public_unique_id,
                    internal_composite_id=internal_composite_id,
                    birth_month=birth_month,
                    birth_year=birth_year,
                    age=age,
                    gender=gender,
                    state=state,
                    district=district,
                    handset_device_token=device_token,
                    security_question=sec_question,
                    security_answer_hash=sec_answer_hash,
                    preferred_city=district
                )
                # Auto grant welcome reward points if enabled
                acct = get_or_create_reward_account(user)
                
                if acct.current_points > 0:
                    create_notification(
                        recipient=user,
                        title="Welcome Shopper! 🎁",
                        message=f"Shopper ID {public_unique_id} activated! +{acct.current_points} Welcome Reward Points added. No phone or email stored.",
                        link_url="/buyer/rewards/"
                    )

            login(request, user)
            # Handset persistence: 1 year session lifetime
            request.session.set_expiry(60 * 60 * 24 * 365)
            
            request.session['just_registered_shopper'] = {
                'shopper_id': primary_username,
                'public_unique_id': public_unique_id,
                'first_name': first_name,
                'gender': gender,
                'district': district,
                'state': state,
            }
            if acct.current_points > 0:
                messages.success(request, f"Welcome to LokalLoop, User {public_unique_id}! +{acct.current_points} welcome points credited.")
            else:
                messages.success(request, f"Welcome to LokalLoop, User {public_unique_id}!")

            # Check for redirect next url or pending claim
            next_url = request.GET.get('next')
            pending_shop = request.session.pop('pending_claim_shop', None)
            pending_offer = request.session.pop('pending_claim_offer', None)
            if pending_shop and pending_offer:
                request.session['post_onboarding_next'] = f"/store/{pending_shop}/qr/?claim_offer={pending_offer}"
            elif next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                request.session['post_onboarding_next'] = next_url

            # Redirect to Gennie Nickname Onboarding Screen
            response = redirect('/buyer/onboarding/')
            response.set_cookie(
                'll_handset_token',
                device_token,
                max_age=60 * 60 * 24 * 365,
                httponly=True,
                samesite='Lax',
                secure=not getattr(settings, 'DEBUG', True)
            )
            return response
    else:
        form = AnonymousBuyerRegistrationForm(initial={
            'state': 'Maharashtra',
            'district': 'Pune',
        })

    context = {
        'form': form,
        'captcha': generate_captcha(),
        'indian_districts_json': json.dumps(INDIAN_DISTRICTS),
        'maharashtra_districts': MAHARASHTRA_DISTRICTS,
    }
    return render(request, 'auth/buyer_register.html', context)


def buyer_onboarding_view(request):
    """
    Gennie Interactive Nickname Onboarding Flow.
    Presented immediately after registration to personalize the shopper's experience.
    """
    if not request.user.is_authenticated:
        return redirect('/buyer/login/')

    buyer_profile = getattr(request.user, 'buyer_profile', None)
    public_unique_id = buyer_profile.public_unique_id if buyer_profile and buyer_profile.public_unique_id else request.user.username
    first_name = request.user.first_name or "Shopper"

    context = {
        'public_unique_id': public_unique_id,
        'first_name': first_name,
        'nickname': buyer_profile.nickname if buyer_profile else '',
    }
    return render(request, 'auth/buyer_onboarding.html', context)


def buyer_set_nickname_view(request):
    """
    Endpoint (View & AJAX) to save the buyer's personalized Gennie nickname.
    """
    if not request.user.is_authenticated:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'error': 'Unauthorized'}, status=401)
        return redirect('/buyer/login/')

    if request.method == 'POST':
        from .shopper_utils import sanitize_user_input
        raw_nickname = request.POST.get('nickname', '').strip()
        nickname = sanitize_user_input(raw_nickname, max_length=30, allow_alphanumeric_only=True)
        buyer_profile = getattr(request.user, 'buyer_profile', None)
        if buyer_profile:
            buyer_profile.nickname = nickname
            buyer_profile.is_nickname_set = True
            buyer_profile.save(update_fields=['nickname', 'is_nickname_set'])

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({
                'success': True,
                'nickname': nickname or request.user.first_name or 'Shopper',
                'message': f"Nickname set to {nickname}!"
            })

        display_name = nickname or request.user.first_name or "Shopper"
        messages.success(request, f"✨ Gennie is thrilled to call you {display_name}!")
        raw_next = request.session.pop('post_onboarding_next', None)
        if raw_next and url_has_allowed_host_and_scheme(url=raw_next, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = raw_next
        else:
            next_url = '/buyer/dashboard/'
        return redirect(next_url)

    return redirect('/buyer/dashboard/')


def buyer_recover_view(request):
    """
    Self-service Zero-Personal-Info Account Recovery:
    Step 1: Enter Shopper ID -> fetches security question.
    Step 2: Enter Secret Answer + New Strong Password -> resets password and logs in.
    """
    if request.user.is_authenticated:
        return redirect('/buyer/dashboard/')

    from config.middleware import get_client_ip
    from django.core.cache import cache

    client_ip = get_client_ip(request)
    fail_key_ip = f"recover_fail_ip:{client_ip}"

    step = 1
    shopper_id = request.POST.get('shopper_id', '').strip() if request.method == 'POST' else request.GET.get('id', '').strip()
    fail_key_id = f"recover_fail_id:{shopper_id.lower()}" if shopper_id else None

    user_target = None
    buyer_profile = None

    def _find_user_by_id(sid):
        if not sid:
            return None
        sid = sid.strip()
        return (
            User.objects.filter(buyer_profile__public_unique_id__iexact=sid, is_active=True).first() or
            User.objects.filter(username__iexact=sid, is_active=True).first() or
            User.objects.filter(buyer_profile__shopper_id__iexact=sid, is_active=True).first()
        )

    candidate_user = _find_user_by_id(shopper_id) if shopper_id else None
    canonical_key = f"recover_fail_canonical_user:{candidate_user.id}" if candidate_user else None

    # Check recovery brute-force lockout (IP, identifier, and canonical user)
    if (
        cache.get(fail_key_ip, 0) >= 5 or
        (fail_key_id and cache.get(fail_key_id, 0) >= 5) or
        (canonical_key and cache.get(canonical_key, 0) >= 5)
    ):
        messages.error(request, "Too many failed recovery attempts. Account recovery is temporarily locked for 15 minutes.")
        return render(request, 'auth/buyer_recover.html', {
            'step': 1,
            'lookup_form': BuyerRecoveryLookupForm(),
            'reset_form': BuyerRecoveryResetForm(),
            'shopper_id': '',
            'buyer_profile': None,
            'captcha': generate_captcha(),
        }, status=429)

    lookup_form = BuyerRecoveryLookupForm(initial={'shopper_id': shopper_id})
    reset_form = BuyerRecoveryResetForm()

    if request.method == 'POST' and 'action_reset' in request.POST:
        if shopper_id:
            user_target = _find_user_by_id(shopper_id)
            if user_target and hasattr(user_target, 'buyer_profile') and user_target.buyer_profile.security_answer_hash:
                buyer_profile = user_target.buyer_profile
        if not user_target or not buyer_profile:
            messages.error(request, "Shopper profile not found. Please verify your Public Unique ID.")
            step = 1
        else:
            step = 2
            canonical_key = f"recover_fail_canonical_user:{user_target.id}"
            reset_form = BuyerRecoveryResetForm(request.POST)
            if reset_form.is_valid():
                provided_answer = reset_form.cleaned_data['security_answer'].strip().lower()
                stored_hash = buyer_profile.security_answer_hash

                if check_password(provided_answer, stored_hash):
                    cache.delete(fail_key_ip)
                    if fail_key_id:
                        cache.delete(fail_key_id)
                    if canonical_key:
                        cache.delete(canonical_key)

                    new_password = reset_form.cleaned_data['new_password']
                    user_target.set_password(new_password)
                    user_target.save()

                    login(request, user_target)
                    messages.success(request, f"Password successfully updated! Welcome back, {user_target.username}.")
                    return redirect('/buyer/dashboard/')
                else:
                    # Increment failure counters across IP, identifier, and canonical user ID
                    ip_fails = cache.get(fail_key_ip, 0) + 1
                    cache.set(fail_key_ip, ip_fails, timeout=900)
                    if fail_key_id:
                        id_fails = cache.get(fail_key_id, 0) + 1
                        cache.set(fail_key_id, id_fails, timeout=900)
                    if canonical_key:
                        c_fails = cache.get(canonical_key, 0) + 1
                        cache.set(canonical_key, c_fails, timeout=900)
                    reset_form.add_error('security_answer', "Incorrect recovery answer. Please try again.")

    elif request.method == 'POST' and 'action_lookup' in request.POST:
        lookup_form = BuyerRecoveryLookupForm(request.POST)
        if lookup_form.is_valid():
            sid = lookup_form.cleaned_data['shopper_id']
            user_target = _find_user_by_id(sid)
            if user_target and hasattr(user_target, 'buyer_profile') and user_target.buyer_profile.security_answer_hash:
                buyer_profile = user_target.buyer_profile
                step = 2
                shopper_id = sid
            else:
                lookup_form.add_error('shopper_id', f"No shopper account found with Public Unique ID '{sid}'. Please check your Shopper Pass.")
                step = 1

    context = {
        'step': step,
        'shopper_id': shopper_id,
        'lookup_form': lookup_form,
        'reset_form': reset_form,
        'buyer_profile': buyer_profile,
        'captcha': generate_captcha(),
    }
    return render(request, 'auth/buyer_recover.html', context)


def buyer_login_view(request):
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile and profile.is_buyer:
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(next_url)
            return redirect('/buyer/dashboard/')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            identifier = form.cleaned_data['identifier'].strip()
            password = form.cleaned_data['password']

            # High Security: Brute-Force & Credential-Stuffing Defense (IP & User Lockout)
            from config.middleware import get_client_ip
            from django.core.cache import cache

            client_ip = get_client_ip(request)
            fail_key_ip = f"auth_fail_ip:{client_ip}"
            fail_key_user = f"auth_fail_user:{identifier.lower()}"

            # Lookup user by unique identifier: public_unique_id, shopper_id, username, or email (BUG-83: non-unique nickname excluded)
            user_obj = User.objects.filter(buyer_profile__public_unique_id__iexact=identifier, is_active=True).first() or \
                       User.objects.filter(buyer_profile__shopper_id__iexact=identifier, is_active=True).first() or \
                       User.objects.filter(username__iexact=identifier, is_active=True).first() or \
                       User.objects.filter(email__iexact=identifier, is_active=True).first()

            fail_key_canonical = f"auth_fail_canonical_user:{user_obj.id}" if user_obj else None

            # If either IP, Identifier, or Canonical User has failed 5+ times in 15 mins, reject immediately (BUG-104)
            if (
                cache.get(fail_key_ip, 0) >= 5 or
                cache.get(fail_key_user, 0) >= 5 or
                (fail_key_canonical and cache.get(fail_key_canonical, 0) >= 5)
            ):
                messages.error(
                    request,
                    "Security Notice: Too many failed login attempts. This account/IP has been temporarily locked for 15 minutes to prevent unauthorized access."
                )
                return render(request, 'auth/buyer_login.html', {'form': form, 'captcha': generate_captcha()}, status=429)

            if user_obj:
                user = authenticate(request, username=user_obj.username, password=password)
                if user is not None:
                    # Reset failure counters on successful authentication
                    cache.delete(fail_key_ip)
                    cache.delete(fail_key_user)
                    if fail_key_canonical:
                        cache.delete(fail_key_canonical)

                    profile = getattr(user, 'profile', None)
                    if profile and profile.is_buyer:
                        login(request, user)
                        # Handset persistence: 1 year session lifetime
                        request.session.set_expiry(60 * 60 * 24 * 365)

                        buyer_prof = getattr(user, 'buyer_profile', None)
                        greeting_name = (buyer_prof.nickname if buyer_prof and buyer_prof.nickname else user.first_name) or user.username
                        messages.success(request, f"Welcome back, {greeting_name}!")

                        next_url = request.GET.get('next') or request.POST.get('next')
                        if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                            target_url = next_url
                        else:
                            target_url = '/buyer/dashboard/'
                        response = redirect(target_url)
                        if buyer_prof and buyer_prof.handset_device_token:
                            response.set_cookie(
                                'll_handset_token',
                                buyer_prof.handset_device_token,
                                max_age=60 * 60 * 24 * 365,
                                httponly=True,
                                samesite='Lax',
                                secure=not getattr(settings, 'DEBUG', True)
                            )
                        return response
                    elif profile and profile.is_seller:
                        messages.warning(request, "This is a Merchant account. Please log in through the Seller Portal.")
                        return redirect('/seller/login/')
                    elif profile and profile.is_admin:
                        return redirect('/admin-portal/dashboard/')

            # Failure branch: timing attack equalization + failure count increment
            check_password(password, 'pbkdf2_sha256$260000$dummy$dummy')
            ip_fails = cache.get(fail_key_ip, 0) + 1
            user_fails = cache.get(fail_key_user, 0) + 1
            cache.set(fail_key_ip, ip_fails, timeout=900)
            cache.set(fail_key_user, user_fails, timeout=900)
            max_fail = max(ip_fails, user_fails)
            if fail_key_canonical:
                c_fails = cache.get(fail_key_canonical, 0) + 1
                cache.set(fail_key_canonical, c_fails, timeout=900)
                max_fail = max(max_fail, c_fails)

            remaining = max(0, 5 - max_fail)
            if remaining > 0:
                messages.error(request, f"Invalid Public Unique ID or password. ({remaining} attempts remaining before temporary lockout)")
            else:
                messages.error(request, "Security Alert: Account/IP temporarily locked for 15 minutes due to multiple failed login attempts.")
    else:
        form = LoginForm()

    return render(request, 'auth/buyer_login.html', {'form': form, 'captcha': generate_captcha()})


def get_subcategories_api(request):
    """
    JSON API for dynamic dependent subcategory dropdown population.
    """
    cat_id = request.GET.get('category_id')
    if not cat_id:
        return JsonResponse({'subcategories': []})

    try:
        category_id = int(cat_id)
        if category_id <= 0:
            return JsonResponse({'subcategories': []})
    except (ValueError, TypeError):
        return JsonResponse({'subcategories': []})

    subs = ShopSubcategory.objects.filter(category_id=category_id, is_active=True).values('id', 'name', 'slug')
    return JsonResponse({'subcategories': list(subs)})


def seller_register_view(request):
    if request.user.is_authenticated:
        return redirect('/seller/dashboard/')

    if request.method == 'POST':
        form = SellerRegistrationForm(request.POST)
        if not request.POST.get('agreement_accepted'):
            form.add_error(None, "You must accept the Merchant Digital Agreement before submitting.")

        if form.is_valid():
            with transaction.atomic():
                email = form.cleaned_data['email'].strip().lower()
                full_name = form.cleaned_data['full_name'].strip()
                phone = form.cleaned_data['phone'].strip()
                business_name = form.cleaned_data['business_name'].strip()
                designation = form.cleaned_data.get('designation', 'OWNER')

                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=form.cleaned_data['password'],
                    first_name=full_name
                )
                city = form.cleaned_data.get('city', 'Pune')
                Profile.objects.create(
                    user=user,
                    role=Role.SELLER,
                    phone=phone,
                    city=city
                )
                merchant_id = generate_unique_merchant_id(city)
                seller_profile = SellerProfile.objects.create(
                    user=user,
                    merchant_id=merchant_id,
                    business_name=business_name,
                    full_name=full_name,
                    designation=designation,
                    email=email,
                    phone=phone,
                    gstin=form.cleaned_data.get('gst_number', '').upper(),
                    pan_number=form.cleaned_data.get('business_pan', '').upper(),
                    agreement_accepted=True,
                    agreement_accepted_at=timezone.now(),
                    is_verified=False,
                    verified_at=None,
                    security_question=form.cleaned_data.get('security_question') or SECURITY_QUESTIONS[0][0]
                )
                sec_ans = form.cleaned_data.get('security_answer', '')
                if sec_ans:
                    seller_profile.set_security_answer(sec_ans)
                    seller_profile.save(update_fields=['security_answer_hash'])
                
                shop = Shop.objects.create(
                    seller=user,
                    category=form.cleaned_data['category'],
                    subcategory=form.cleaned_data.get('subcategory'),
                    name=business_name,
                    description=f"Welcome to {business_name}, located at {form.cleaned_data.get('locality')}, {city}.",
                    address=form.cleaned_data['address'],
                    locality=form.cleaned_data['locality'],
                    city=city,
                    state=form.cleaned_data.get('state', 'Maharashtra'),
                    pincode=form.cleaned_data.get('pincode', '411001'),
                    latitude=form.cleaned_data.get('latitude') or get_city_default_coords(city)[0],
                    longitude=form.cleaned_data.get('longitude') or get_city_default_coords(city)[1],
                    phone=phone,
                    display_phone=form.cleaned_data.get('display_phone') or phone,
                    email=email,
                    operating_hours=form.cleaned_data.get('operating_hours') or '09:00 AM - 10:00 PM (All Days)',
                    is_approved=False,
                    is_active=False
                )

                # Create compliance document record
                ShopVerification.objects.create(
                    shop=shop,
                    shop_act_license_number=form.cleaned_data.get('shop_act_license_number', '').upper(),
                    shop_act_valid_till=form.cleaned_data.get('shop_act_valid_till'),
                    fssai_license_number=form.cleaned_data.get('fssai_license_number', '').strip(),
                    fssai_valid_till=form.cleaned_data.get('fssai_valid_till'),
                    fda_license_number=form.cleaned_data.get('fda_license_number', '').strip().upper(),
                    fda_valid_till=form.cleaned_data.get('fda_valid_till'),
                    gst_number=form.cleaned_data.get('gst_number', '').upper(),
                    business_pan=form.cleaned_data.get('business_pan', '').upper(),
                    status='PENDING'
                )

                # Log Merchant Digital Agreement acceptance for compliance audit
                from apps.administration.models import MerchantAgreementLog
                from config.middleware import get_client_ip
                client_ip = get_client_ip(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')[:490]
                MerchantAgreementLog.objects.create(
                    user=user,
                    merchant_id=merchant_id,
                    business_name=business_name,
                    agreement_title="Merchant Digital Agreement",
                    agreement_url=request.build_absolute_uri('/terms/'),
                    agreement_version="v1.0",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    is_accepted=True
                )

                create_notification(
                    recipient=user,
                    title="Merchant Registration Submitted ⏳",
                    message=f"Your merchant account has been registered with ID {merchant_id}. Your documents are under compliance review. Features will unlock upon approval.",
                    link_url="/seller/dashboard/"
                )

            login(request, user)
            messages.success(request, f"Registration submitted! Your official Merchant ID is {merchant_id}. Compliance review is pending.")
            return redirect('/thank-you/?role=merchant')
        else:
            first_err = ""
            for field, errs in form.errors.items():
                if errs:
                    field_title = field.replace('_', ' ').title()
                    first_err = f"{field_title}: {errs[0]}"
                    break
            messages.error(request, f"Registration incomplete — {first_err}")
    else:
        form = SellerRegistrationForm()

    # Pre-serialize category tree with subcategories and compliance flags for instant dynamic UI
    categories = ShopCategory.objects.filter(is_active=True).prefetch_related('shop_subcategories')
    category_tree = {}
    for cat in categories:
        category_tree[str(cat.id)] = {
            'id': cat.id,
            'name': cat.name,
            'slug': cat.slug,
            'icon': cat.icon,
            'requires_fssai': cat.requires_fssai,
            'requires_fda': cat.requires_fda,
            'subcategories': [
                {'id': sub.id, 'name': sub.name, 'slug': sub.slug}
                for sub in cat.shop_subcategories.filter(is_active=True)
            ]
        }

    context = {
        'form': form,
        'captcha': generate_captcha(),
        'category_tree_json': json.dumps(category_tree),
        'categories': categories,
    }
    return render(request, 'auth/seller_register.html', context)


def seller_login_view(request):
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile and profile.is_seller:
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(next_url)
            return redirect('/seller/dashboard/')

    client_ip = get_client_ip(request)
    fail_key_ip = f"login_fail_ip_{client_ip}"
    fail_key_user = None

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            identifier = form.cleaned_data['identifier'].strip()
            password = form.cleaned_data['password']
            fail_key_user = f"login_fail_user_seller_{identifier.lower()}"

            # Lookup by username, email, or city-coded Merchant ID
            user_obj = User.objects.filter(username__iexact=identifier).first() or \
                       User.objects.filter(email__iexact=identifier).first()
            if not user_obj:
                sp = SellerProfile.objects.filter(merchant_id__iexact=identifier).first()
                if sp:
                    user_obj = sp.user

            fail_key_canonical = f"login_fail_seller_canonical:{user_obj.id}" if user_obj else None

            # Brute-force rate limiting: 5 failed attempts locks out for 15 minutes (BUG-104)
            if (
                cache.get(fail_key_ip, 0) >= 5 or
                cache.get(fail_key_user, 0) >= 5 or
                (fail_key_canonical and cache.get(fail_key_canonical, 0) >= 5)
            ):
                messages.error(
                    request,
                    "Security Notice: Too many failed login attempts. This account/IP has been temporarily locked for 15 minutes to prevent unauthorized access."
                )
                return render(request, 'auth/seller_login.html', {'form': form, 'captcha': generate_captcha()}, status=429)

            if user_obj:
                user = authenticate(request, username=user_obj.username, password=password)
                if user is not None:
                    profile = getattr(user, 'profile', None)
                    if profile and profile.is_seller:
                        cache.delete(fail_key_ip)
                        cache.delete(fail_key_user)
                        if fail_key_canonical:
                            cache.delete(fail_key_canonical)
                        login(request, user)
                        messages.success(request, f"Logged into Merchant Portal: {user.seller_profile.business_name if hasattr(user, 'seller_profile') else user.username}")
                        next_url = request.GET.get('next') or request.POST.get('next')
                        if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                            target_url = next_url
                        else:
                            target_url = '/seller/dashboard/'
                        return redirect(target_url)
                    elif profile and profile.is_buyer:
                        messages.warning(request, "This is a Buyer account. Please sign in to the Buyer Portal.")
                        return redirect('/buyer/login/')

            # Failed login attempt tracking
            ip_fails = cache.get(fail_key_ip, 0) + 1
            user_fails = cache.get(fail_key_user, 0) + 1
            cache.set(fail_key_ip, ip_fails, timeout=900)
            cache.set(fail_key_user, user_fails, timeout=900)
            max_fail = max(ip_fails, user_fails)
            if fail_key_canonical:
                c_fails = cache.get(fail_key_canonical, 0) + 1
                cache.set(fail_key_canonical, c_fails, timeout=900)
                max_fail = max(max_fail, c_fails)
            remaining = max(0, 5 - max_fail)

            if remaining > 0:
                messages.error(request, f"Invalid merchant credentials. ({remaining} attempts remaining before temporary lockout)")
            else:
                messages.error(request, "Security Alert: Account/IP temporarily locked for 15 minutes due to multiple failed login attempts.")
    else:
        form = LoginForm()

    return render(request, 'auth/seller_login.html', {'form': form, 'captcha': generate_captcha()})


def admin_login_view(request):
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if (profile and profile.is_admin) or request.user.is_superuser:
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(next_url)
            return redirect('/admin-portal/dashboard/')

    ip = get_client_ip(request)
    lockout_key = f"admin_lockout_{ip}"
    fail_count_key = f"admin_fails_{ip}"

    # Check if this IP is currently locked out
    if cache.get(lockout_key):
        AdminAction.objects.create(
            admin=None,
            action_type="SECURITY_ADMIN_LOGIN_LOCKED",
            target_model="AdminAuth",
            target_id=ip,
            details=f"Blocked request from locked IP {ip}. Brute-force lockout active.",
            ip_address=ip
        )
        return HttpResponse(
            "Security Alert: Admin portal access temporarily locked due to consecutive failed attempts. Please retry in 15 minutes.",
            status=429
        )

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            identifier = form.cleaned_data['identifier'].strip()
            password = form.cleaned_data['password']

            user_obj = User.objects.filter(username__iexact=identifier).first() or \
                       User.objects.filter(email__iexact=identifier).first()

            if user_obj:
                user = authenticate(request, username=user_obj.username, password=password)
                if user is not None:
                    profile = getattr(user, 'profile', None)
                    if (profile and profile.is_admin) or user.is_superuser or user.is_staff:
                        # Reset failure counters on successful admin authentication
                        cache.delete(fail_count_key)
                        cache.delete(lockout_key)

                        AdminAction.objects.create(
                            admin=user,
                            action_type="ADMIN_LOGIN_SUCCESS",
                            target_model="AdminAuth",
                            target_id=str(user.id),
                            details=f"Administrator '{user.username}' successfully authenticated from IP {ip}.",
                            ip_address=ip
                        )

                        login(request, user)
                        messages.success(request, f"Welcome Administrator ({user.username}). Platform console ready.")
                        next_url = request.GET.get('next') or request.POST.get('next')
                        if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                            target_url = next_url
                        else:
                            target_url = '/admin-portal/dashboard/'
                        return redirect(target_url)

            # Failed admin authentication: increment failure counter
            fails = cache.get(fail_count_key, 0) + 1
            cache.set(fail_count_key, fails, timeout=900)

            AdminAction.objects.create(
                admin=None,
                action_type="ADMIN_LOGIN_FAILED",
                target_model="AdminAuth",
                target_id=identifier[:50],
                details=f"Failed admin authentication for '{identifier}' from IP {ip}. Attempt {fails}/5.",
                ip_address=ip
            )

            if fails >= 5:
                cache.set(lockout_key, True, timeout=900)
                AdminAction.objects.create(
                    admin=None,
                    action_type="SECURITY_ADMIN_LOGIN_LOCKED",
                    target_model="AdminAuth",
                    target_id=ip,
                    details=f"Admin login temporarily locked for IP {ip} after 5 failed attempts.",
                    ip_address=ip
                )
                return HttpResponse(
                    "Security Alert: Admin portal access temporarily locked due to consecutive failed attempts. Please retry in 15 minutes.",
                    status=429
                )

            remaining = 5 - fails
            messages.error(
                request,
                f"Unauthorized. Only platform administrators can access this portal. ({remaining} attempts remaining before temporary lockout)"
            )
    else:
        form = LoginForm()

    return render(request, 'auth/admin_login.html', {'form': form, 'captcha': generate_captcha(is_dark=True)})


def logout_view(request):
    if hasattr(request, 'session'):
        request.session.pop('last_visited_url', None)
    logout(request)
    messages.info(request, "You have been logged out safely.")
    response = redirect('/')
    response.delete_cookie('last_visited_url')
    return response


def login_chooser_view(request):
    next_url = request.GET.get('next', '')
    if next_url:
        if '/seller/' in next_url:
            return redirect(f'/seller/login/?next={next_url}')
        elif '/admin' in next_url:
            return redirect(f'/admin-login/?next={next_url}')
        elif '/buyer/' in next_url:
            return redirect(f'/buyer/login/?next={next_url}')

    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile and profile.is_buyer:
            return redirect('/buyer/dashboard/')
        elif profile and profile.is_seller:
            return redirect('/seller/dashboard/')
        elif (profile and profile.is_admin) or request.user.is_superuser:
            return redirect('/admin-portal/dashboard/')
        return redirect('/')

    return render(request, 'auth/login_chooser.html')


@login_required(login_url='/buyer/login/')
def buyer_change_password_view(request):
    """
    Enterprise-Grade Password Change view for Customer / Buyer Portal.
    Validates current password, enforces strong rules, maintains active session.
    """
    profile = getattr(request.user, 'profile', None)
    if profile and not profile.is_buyer:
        return redirect('/seller/dashboard/' if profile.is_seller else '/admin-portal/dashboard/')

    if request.method == 'POST':
        form = ChangePasswordForm(user=request.user, data=request.POST)
        if form.is_valid():
            new_password = form.cleaned_data['new_password']
            request.user.set_password(new_password)
            request.user.save()

            # Prevent logout by updating session hash
            update_session_auth_hash(request, request.user)

            create_notification(
                recipient=request.user,
                title="Security Alert 🔐",
                message="Your account security password was changed successfully.",
                link_url="/buyer/change-password/"
            )

            messages.success(request, "Your password has been updated securely.")
            return redirect('/buyer/change-password/')
    else:
        form = ChangePasswordForm(user=request.user)

    context = {
        'form': form,
        'buyer_profile': getattr(request.user, 'buyer_profile', None),
    }
    return render(request, 'buyer/change_password.html', context)


@login_required(login_url='/seller/login/')
def seller_change_password_view(request):
    """
    Enterprise-Grade Password Change view for Merchant / Seller Portal.
    Validates current password, enforces strong rules, maintains active session.
    """
    profile = getattr(request.user, 'profile', None)
    if profile and not profile.is_seller:
        return redirect('/buyer/dashboard/' if profile.is_buyer else '/admin-portal/dashboard/')

    if request.method == 'POST':
        form = ChangePasswordForm(user=request.user, data=request.POST)
        if form.is_valid():
            new_password = form.cleaned_data['new_password']
            request.user.set_password(new_password)
            request.user.save()

            update_session_auth_hash(request, request.user)

            create_notification(
                recipient=request.user,
                title="Security Alert 🔐",
                message="Your account security password was changed successfully.",
                link_url="/seller/change-password/"
            )

            messages.success(request, "Your password has been updated securely.")
            return redirect('/seller/change-password/')
    else:
        form = ChangePasswordForm(user=request.user)

    context = {
        'form': form,
        'seller_profile': getattr(request.user, 'seller_profile', None),
    }
    return render(request, 'seller/change_password.html', context)


def seller_recover_view(request):
    """
    Enterprise-Grade Password Recovery Engine for Merchants & Store Partners:
    Step 1: Enter Merchant Email, Verified Mobile Phone, or Merchant ID -> fetches security question.
    Step 2: Enter Secret Answer + New Strong Password -> verifies hash & resets password.
    Includes brute-force lockout (max 5 failures per 15 mins per IP/Account).
    """
    if request.user.is_authenticated:
        return redirect('/seller/dashboard/')

    from config.middleware import get_client_ip
    from django.core.cache import cache

    client_ip = get_client_ip(request)
    fail_key_ip = f"recover_fail_seller_ip:{client_ip}"

    step = 1
    identifier = request.POST.get('identifier', '').strip() if request.method == 'POST' else request.GET.get('identifier', '').strip()
    fail_key_id = f"recover_fail_seller_id:{identifier.lower()}" if identifier else None

    user_target = None
    seller_profile = None

    def _find_seller_by_identifier(ident):
        if not ident:
            return None
        ident_clean = ident.strip()
        return (
            User.objects.filter(seller_profile__merchant_id__iexact=ident_clean, is_active=True).first() or
            User.objects.filter(email__iexact=ident_clean, is_active=True).first() or
            User.objects.filter(username__iexact=ident_clean, is_active=True).first() or
            User.objects.filter(seller_profile__phone=ident_clean, is_active=True).first() or
            User.objects.filter(profile__phone=ident_clean, profile__role=Role.SELLER, is_active=True).first()
        )

    candidate_user = _find_seller_by_identifier(identifier) if identifier else None
    canonical_key = f"recover_fail_seller_canonical_user:{candidate_user.id}" if candidate_user else None

    # Check recovery brute-force lockout (IP, identifier, and canonical user)
    if (
        cache.get(fail_key_ip, 0) >= 5 or
        (fail_key_id and cache.get(fail_key_id, 0) >= 5) or
        (canonical_key and cache.get(canonical_key, 0) >= 5)
    ):
        messages.error(request, "Too many failed recovery attempts. Account recovery is temporarily locked for 15 minutes.")
        return render(request, 'auth/seller_recover.html', {
            'step': 1,
            'lookup_form': SellerRecoveryLookupForm(),
            'reset_form': SellerRecoveryResetForm(),
            'identifier': '',
            'seller_profile': None,
            'captcha': generate_captcha(),
        }, status=429)

    lookup_form = SellerRecoveryLookupForm(initial={'identifier': identifier})
    reset_form = SellerRecoveryResetForm()

    if request.method == 'POST' and 'action_reset' in request.POST:
        if identifier:
            user_target = _find_seller_by_identifier(identifier)
            if user_target and hasattr(user_target, 'seller_profile') and user_target.seller_profile.security_answer_hash:
                seller_profile = user_target.seller_profile
        if not user_target or not seller_profile:
            messages.error(request, "Merchant account not found. Please verify your identifier.")
            step = 1
        else:
            step = 2
            canonical_key = f"recover_fail_seller_canonical_user:{user_target.id}"
            reset_form = SellerRecoveryResetForm(request.POST)
            if reset_form.is_valid():
                provided_answer = reset_form.cleaned_data['security_answer']

                if seller_profile.check_security_answer(provided_answer):
                    cache.delete(fail_key_ip)
                    if fail_key_id:
                        cache.delete(fail_key_id)
                    if canonical_key:
                        cache.delete(canonical_key)

                    new_password = reset_form.cleaned_data['new_password']
                    user_target.set_password(new_password)
                    user_target.save()

                    messages.success(request, f"Password successfully updated for {seller_profile.business_name}! Please sign in with your new credentials.")
                    return redirect('/seller/login/?reset=success')
                else:
                    # Increment failure counters across IP, identifier, and canonical user ID
                    ip_fails = cache.get(fail_key_ip, 0) + 1
                    cache.set(fail_key_ip, ip_fails, timeout=900)
                    if fail_key_id:
                        id_fails = cache.get(fail_key_id, 0) + 1
                        cache.set(fail_key_id, id_fails, timeout=900)
                    if canonical_key:
                        c_fails = cache.get(canonical_key, 0) + 1
                        cache.set(canonical_key, c_fails, timeout=900)
                    reset_form.add_error('security_answer', "Incorrect recovery answer. Please try again.")
                    step = 2

    elif request.method == 'POST' and 'action_lookup' in request.POST:
        lookup_form = SellerRecoveryLookupForm(request.POST)
        if lookup_form.is_valid():
            ident = lookup_form.cleaned_data['identifier']
            user_target = _find_seller_by_identifier(ident)
            if user_target and hasattr(user_target, 'seller_profile') and user_target.seller_profile.security_answer_hash:
                seller_profile = user_target.seller_profile
                step = 2
                identifier = ident
            else:
                lookup_form.add_error('identifier', f"No merchant found matching '{ident}' with security recovery configured.")
                step = 1

    context = {
        'step': step,
        'identifier': identifier,
        'lookup_form': lookup_form,
        'reset_form': reset_form,
        'seller_profile': seller_profile,
        'captcha': generate_captcha(),
    }
    return render(request, 'auth/seller_recover.html', context)



def seller_check_deletion_eligibility_api(request):
    """
    JSON API returning deletion eligibility, active blockers, and confirmation details
    for merchant account deletion modals.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    profile = getattr(request.user, 'profile', None)
    if not profile or not profile.is_seller:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    seller_profile = getattr(request.user, 'seller_profile', None)
    stores = Shop.objects.filter(seller=request.user)
    stores_count = stores.count()
    active_campaigns = Offer.objects.filter(shop__seller=request.user, status=OfferStatus.ACTIVE, end_date__gt=timezone.now())
    active_campaigns_count = active_campaigns.count()
    pending_redemptions = OfferRedemption.objects.filter(seller=request.user, status=RedemptionStatus.PENDING_APPROVAL)
    pending_redemptions_count = pending_redemptions.count()

    can_delete = (pending_redemptions_count == 0)
    blocker_message = ""
    if pending_redemptions_count > 0:
        blocker_message = f"You have {pending_redemptions_count} pending counter redemptions awaiting verification. Please approve or reject them before closing your account."

    return JsonResponse({
        'can_delete': can_delete,
        'stores_count': stores_count,
        'active_campaigns_count': active_campaigns_count,
        'pending_redemptions_count': pending_redemptions_count,
        'has_security_question': bool(seller_profile and seller_profile.security_question),
        'security_question': seller_profile.security_question if (seller_profile and seller_profile.security_question) else '',
        'blocker_message': blocker_message,
        'merchant_id': getattr(seller_profile, 'merchant_id', ''),
        'business_name': getattr(seller_profile, 'business_name', '')
    })


def get_safe_referer(request, default_url):
    """
    Hardened defense against Open Redirect attacks via spoofed HTTP_REFERER headers.
    Validates that the referer matches the currently authorized server host and scheme.
    """
    ref = request.META.get('HTTP_REFERER')
    if ref and url_has_allowed_host_and_scheme(url=ref, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return ref
    return default_url


@login_required(login_url='/seller/login/')
def seller_delete_account_view(request):
    """
    Enterprise-Grade Seller / Merchant Account Deletion & Deactivation Workflow.
    Enforces DPDP Act 2023, Indian IT Rules, and statutory GST retention:
    1. Zero-liabilities check (no pending redemptions, active campaign handling).
    2. Immediate public delisting of all shops and active deals.
    3. Soft-deletion preserving financial audit logs (OfferRedemption, Wallet, Invoices).
    4. Immutable AccountDeletionLog creation and session invalidation.
    """
    profile = getattr(request.user, 'profile', None)
    if profile and not profile.is_seller:
        return redirect('/buyer/dashboard/' if profile.is_buyer else '/admin-portal/dashboard/')

    seller_profile = getattr(request.user, 'seller_profile', None)
    stores = Shop.objects.filter(seller=request.user)
    stores_count = stores.count()
    active_campaigns = Offer.objects.filter(shop__seller=request.user, status=OfferStatus.ACTIVE, end_date__gt=timezone.now())
    active_campaigns_count = active_campaigns.count()
    pending_redemptions = OfferRedemption.objects.filter(seller=request.user, status=RedemptionStatus.PENDING_APPROVAL)
    pending_redemptions_count = pending_redemptions.count()

    client_ip = get_client_ip(request)
    lockout_key = f"del_lockout_seller_{client_ip}_{request.user.id}"
    attempts_key = f"del_attempts_seller_{client_ip}_{request.user.id}"

    if request.method == 'POST':
        # Rate-limiting guard
        if cache.get(lockout_key):
            error_msg = "Too many failed deletion attempts. Account deletion is temporarily locked for 15 minutes."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'success': False, 'error': error_msg}, status=429)
            messages.error(request, error_msg)
            return redirect(get_safe_referer(request, '/seller/change-password/'))

        # Check pending redemptions blocker
        if pending_redemptions_count > 0:
            error_msg = f"Cannot delete account with {pending_redemptions_count} pending counter redemptions. Please approve or reject pending redemptions first."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'success': False, 'error': error_msg}, status=400)
            messages.error(request, error_msg)
            return redirect(get_safe_referer(request, '/seller/change-password/'))

        form = SellerAccountDeletionForm(request.user, data=request.POST)
        if form.is_valid():
            # Check active campaigns unpublish confirmation
            confirm_bulk_unpublish = form.cleaned_data.get('confirm_bulk_unpublish', False)
            if active_campaigns_count > 0 and not confirm_bulk_unpublish:
                form.add_error('confirm_bulk_unpublish', f"You have {active_campaigns_count} active campaign(s). Please confirm automatic unpublishing to proceed.")
                context = {
                    'form': form,
                    'stores_count': stores_count,
                    'active_campaigns_count': active_campaigns_count,
                    'pending_redemptions_count': pending_redemptions_count,
                    'seller_profile': seller_profile,
                }
                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                    return JsonResponse({'success': False, 'error': f"You have {active_campaigns_count} active campaign(s). Please confirm automatic unpublishing."}, status=400)
                return render(request, 'seller/delete_account.html', context)

            # Success - Perform soft-delete and delisting inside atomic transaction
            cache.delete(attempts_key)
            cache.delete(lockout_key)

            reason_category = form.cleaned_data.get('reason_category')
            reason_notes = form.cleaned_data.get('reason_notes', '')

            with transaction.atomic():
                # 1. Delist all shops immediately
                delisted_stores_count = Shop.objects.filter(seller=request.user).update(is_active=False)

                # 2. Bulk unpublish / pause all active and draft deals
                Offer.objects.filter(shop__seller=request.user).update(status=OfferStatus.PAUSED)

                # 3. Invalidate active promotional QR codes
                QRCode.objects.filter(offer__shop__seller=request.user).update(is_used=True)

                # 4. Create immutable statutory AccountDeletionLog
                user_identifier = getattr(seller_profile, 'merchant_id', '') or request.user.email or request.user.username
                AccountDeletionLog.objects.create(
                    user=request.user,
                    user_identifier=user_identifier,
                    role=DeletionRole.SELLER,
                    reason_category=reason_category,
                    reason_notes=reason_notes,
                    ip_address=client_ip,
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                    stores_delisted_count=delisted_stores_count,
                    financial_records_retained=True,
                )

                # 5. Soft-delete user credentials, anonymize PII & release unique constraints
                ts = int(timezone.now().timestamp())
                rnd = secrets.token_hex(3)
                domain = request.user.email.split('@')[-1] if '@' in request.user.email else 'lokalloop.local'
                request.user.username = f"deleted_seller_{ts}_{rnd}"
                request.user.email = f"deleted_{ts}_{rnd}@{domain}"
                request.user.first_name = ''
                request.user.last_name = ''
                request.user.is_active = False
                request.user.set_unusable_password()
                request.user.save(update_fields=['username', 'email', 'first_name', 'last_name', 'is_active', 'password'])

                if profile:
                    profile.is_active = False
                    profile.phone = ''
                    profile.save(update_fields=['is_active', 'phone'])

                if seller_profile:
                    seller_profile.merchant_id = None
                    seller_profile.phone = ''
                    seller_profile.email = ''
                    seller_profile.full_name = ''
                    seller_profile.security_question = ''
                    seller_profile.security_answer_hash = ''
                    seller_profile.is_verified = False
                    seller_profile.save(update_fields=[
                        'merchant_id', 'phone', 'email', 'full_name',
                        'security_question', 'security_answer_hash', 'is_verified'
                    ])

            # 6. Invalidate active sessions across all devices and log out (BUG-66C / BUG-100)
            try:
                from django.contrib.sessions.models import Session
                user_id_str = str(request.user.id)
                session_keys_to_delete = []
                for s in Session.objects.filter(expire_date__gte=timezone.now()).only('session_key', 'session_data').iterator(chunk_size=500):
                    try:
                        s_data = s.get_decoded()
                        if str(s_data.get('_auth_user_id')) == user_id_str:
                            session_keys_to_delete.append(s.session_key)
                    except Exception:
                        pass
                if session_keys_to_delete:
                    Session.objects.filter(session_key__in=session_keys_to_delete).delete()
            except Exception:
                pass

            logout(request)
            success_msg = "Your merchant account has been closed and all store listings have been permanently delisted. Statutory financial records are preserved in accordance with GST & IT rules."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'success': True, 'redirect_url': '/seller/login/?deleted=success', 'message': success_msg})

            messages.success(request, success_msg)
            return redirect('/seller/login/?deleted=success')
        else:
            # Increment failed attempts
            attempts = cache.get(attempts_key, 0) + 1
            cache.set(attempts_key, attempts, timeout=900)
            if attempts >= 5:
                cache.set(lockout_key, True, timeout=900)
                error_msg = "Too many failed deletion attempts. Account deletion locked for 15 minutes."
            else:
                first_err = list(form.errors.values())[0][0] if form.errors else "Invalid submission."
                error_msg = f"{first_err} ({5 - attempts} attempts remaining)."

            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'success': False, 'error': error_msg}, status=400)

            messages.error(request, error_msg)
            context = {
                'form': form,
                'stores_count': stores_count,
                'active_campaigns_count': active_campaigns_count,
                'pending_redemptions_count': pending_redemptions_count,
                'seller_profile': seller_profile,
            }
            return render(request, 'seller/delete_account.html', context)

    form = SellerAccountDeletionForm(request.user)
    context = {
        'form': form,
        'stores_count': stores_count,
        'active_campaigns_count': active_campaigns_count,
        'pending_redemptions_count': pending_redemptions_count,
        'seller_profile': seller_profile,
    }
    return render(request, 'seller/delete_account.html', context)


@buyer_required
def buyer_delete_account_view(request):
    """
    Enterprise-Grade Shopper / Buyer Account Deletion & Deactivation Workflow.
    Adheres strictly to DPDP Act 2023:
    1. Warning dialog explaining forfeiture of all loyalty rewards & scratch cards.
    2. Re-authentication via current password or secret recovery question.
    3. Revocation of tokens and deactivation.
    4. Immutable AccountDeletionLog and session clearance.
    """
    profile = getattr(request.user, 'profile', None)
    if profile and not profile.is_buyer:
        return redirect('/seller/dashboard/' if profile.is_seller else '/admin-portal/dashboard/')

    buyer_profile = getattr(request.user, 'buyer_profile', None)
    reward_account = getattr(request.user, 'reward_account', None)
    points_balance = reward_account.current_points if reward_account else 0

    client_ip = get_client_ip(request)
    lockout_key = f"del_lockout_buyer_{client_ip}_{request.user.id}"
    attempts_key = f"del_attempts_buyer_{client_ip}_{request.user.id}"

    if request.method == 'POST':
        if cache.get(lockout_key):
            error_msg = "Too many failed deletion attempts. Account deletion locked for 15 minutes."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'success': False, 'error': error_msg}, status=429)
            messages.error(request, error_msg)
            return redirect(get_safe_referer(request, '/buyer/change-password/'))

        form = BuyerAccountDeletionForm(request.user, data=request.POST)
        if form.is_valid():
            cache.delete(attempts_key)
            cache.delete(lockout_key)

            reason_category = form.cleaned_data.get('reason_category')
            reason_notes = form.cleaned_data.get('reason_notes', '')

            with transaction.atomic():
                # 1. Zero out and forfeit reward account points
                if reward_account:
                    forfeited_pts = reward_account.current_points
                    reward_account.current_points = 0
                    reward_account.save(update_fields=['current_points'])
                else:
                    forfeited_pts = 0

                # 1b. Expire dangling bumper bills and unredeemed catalog claims (BUG-66D)
                try:
                    from apps.rewards.models import BumperBillSubmission, BumperBillStatus, BuyerRewardClaim
                    BumperBillSubmission.objects.filter(
                        buyer=request.user,
                        status__in=[BumperBillStatus.PENDING_STORE, BumperBillStatus.STORE_VERIFIED, BumperBillStatus.PENDING]
                    ).update(
                        status=BumperBillStatus.REJECTED,
                        rejection_reason="Shopper account closed under DPDP Act 2023."
                    )
                    BuyerRewardClaim.objects.filter(
                        buyer=request.user,
                        status='ACTIVE'
                    ).update(status='EXPIRED')
                except Exception:
                    pass

                # 2. Create immutable AccountDeletionLog
                user_identifier = getattr(buyer_profile, 'public_unique_id', '') or getattr(buyer_profile, 'shopper_id', '') or request.user.username
                AccountDeletionLog.objects.create(
                    user=request.user,
                    user_identifier=user_identifier,
                    role=DeletionRole.BUYER,
                    reason_category=reason_category,
                    reason_notes=f"Forfeited {forfeited_pts} reward points. {reason_notes}".strip(),
                    ip_address=client_ip,
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                    stores_delisted_count=0,
                    financial_records_retained=True,
                )

                # 3. Soft-delete user credentials, anonymize PII & release unique constraints (BUG-66B, BUG-66E)
                ts = int(timezone.now().timestamp())
                rnd = secrets.token_hex(3)
                domain = request.user.email.split('@')[-1] if '@' in request.user.email else 'lokalloop.local'
                request.user.username = f"deleted_buyer_{ts}_{rnd}"
                request.user.email = f"deleted_{ts}_{rnd}@{domain}"
                request.user.first_name = ''
                request.user.last_name = ''
                request.user.is_active = False
                request.user.set_unusable_password()
                request.user.save(update_fields=['username', 'email', 'first_name', 'last_name', 'is_active', 'password'])

                if profile:
                    profile.is_active = False
                    profile.phone = ''
                    profile.save(update_fields=['is_active', 'phone'])

                if buyer_profile:
                    buyer_profile.public_unique_id = None
                    buyer_profile.shopper_id = f"del_{ts}_{rnd}"
                    buyer_profile.internal_composite_id = ''
                    buyer_profile.nickname = ''
                    buyer_profile.security_question = ''
                    buyer_profile.security_answer_hash = ''
                    buyer_profile.handset_device_token = ''
                    buyer_profile.save(update_fields=[
                        'public_unique_id', 'shopper_id', 'internal_composite_id',
                        'nickname', 'security_question', 'security_answer_hash',
                        'handset_device_token'
                    ])

            # Invalidate all active sessions for this user across all devices (BUG-66C / BUG-100)
            try:
                from django.contrib.sessions.models import Session
                user_id_str = str(request.user.id)
                session_keys_to_delete = []
                for s in Session.objects.filter(expire_date__gte=timezone.now()).only('session_key', 'session_data').iterator(chunk_size=500):
                    try:
                        s_data = s.get_decoded()
                        if str(s_data.get('_auth_user_id')) == user_id_str:
                            session_keys_to_delete.append(s.session_key)
                    except Exception:
                        pass
                if session_keys_to_delete:
                    Session.objects.filter(session_key__in=session_keys_to_delete).delete()
            except Exception:
                pass

            logout(request)
            success_msg = "Your shopper account has been deactivated and personal session credentials cleared in compliance with DPDP Act 2023."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                response = JsonResponse({'success': True, 'redirect_url': '/buyer/login/?deleted=success', 'message': success_msg})
            else:
                messages.success(request, success_msg)
                response = redirect('/buyer/login/?deleted=success')

            response.delete_cookie('ll_handset_token')
            return response
        else:
            attempts = cache.get(attempts_key, 0) + 1
            cache.set(attempts_key, attempts, timeout=900)
            if attempts >= 5:
                cache.set(lockout_key, True, timeout=900)
                error_msg = "Too many failed deletion attempts. Account deletion locked for 15 minutes."
            else:
                first_err = list(form.errors.values())[0][0] if form.errors else "Invalid submission."
                error_msg = f"{first_err} ({5 - attempts} attempts remaining)."

            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'success': False, 'error': error_msg}, status=400)

            messages.error(request, error_msg)
            context = {
                'form': form,
                'points_balance': points_balance,
                'buyer_profile': buyer_profile,
            }
            return render(request, 'buyer/delete_account.html', context)

    form = BuyerAccountDeletionForm(request.user)
    context = {
        'form': form,
        'points_balance': points_balance,
        'buyer_profile': buyer_profile,
    }
    return render(request, 'buyer/delete_account.html', context)
