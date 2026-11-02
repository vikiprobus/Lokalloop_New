import io
import base64
import uuid
from decimal import Decimal
from datetime import timedelta
import qrcode
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.offers.models import Offer, OfferStatus
from apps.redemptions.models import QRCode, OfferRedemption, RedemptionStatus
from apps.wallets.models import SellerWallet
from apps.rewards.models import RewardType
from services.wallet_service import deduct_credits, get_or_create_wallet
from services.reward_service import award_points, get_or_create_reward_account
from services.notification_service import notify_redemption_to_buyer, notify_redemption_to_seller, notify_low_credits


def generate_qr_image_data_uri(content_string: str) -> str:
    """
    Generates a high-contrast base64 Data URI PNG for display on screen/poster.
    Eliminates reliance on external image servers or cloud storage for prototype.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(content_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0F172A", back_color="#FFFFFF")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64_str = base64.b64encode(buf.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{b64_str}"


def generate_store_qr(store_url: str) -> str:
    """
    Generates a high-contrast base64 Data URI PNG for a merchant's store URL.
    Used for customer camera scanning to view all active deals.
    """
    return generate_qr_image_data_uri(store_url)


def create_or_refresh_offer_qr(offer: Offer, expiry_minutes: int = 1440) -> tuple[QRCode, str]:
    """
    Retrieves or generates an active QR code for an offer with expiration.
    Returns (qr_instance, data_uri_image)
    """
    now = timezone.now()
    active_qr = QRCode.objects.filter(
        offer=offer,
        is_used=False,
        expires_at__gt=now
    ).first()

    if active_qr and active_qr.is_expired:
        active_qr = None

    if not active_qr:
        code_str = str(uuid.uuid4())
        calculated_expiry = now + timedelta(minutes=expiry_minutes)
        if offer.end_date and offer.end_date < calculated_expiry:
            calculated_expiry = offer.end_date
        if calculated_expiry < now:
            calculated_expiry = now

        active_qr = QRCode.objects.create(
            offer=offer,
            code=code_str,
            expires_at=calculated_expiry
        )

    # BUG-105: Encode navigable landing URL so physical prints and screens can be scanned by standard smartphone cameras
    qr_url = f"/qr/{active_qr.code}/"
    qr_image = generate_qr_image_data_uri(qr_url)
    return active_qr, qr_image


@transaction.atomic
def process_redemption(
    buyer_user,
    qr_code_str: str,
    bill_amount: Decimal = Decimal('0.00'),
    idempotency_key: str = None,
    buyer_lat: float | None = None,
    buyer_lng: float | None = None,
    buyer_accuracy: float = 0.0,
    client_ip: str | None = None,
    device_token: str | None = None
) -> tuple[bool, str, OfferRedemption | None]:
    """
    ATOMIC REDEMPTION ENGINE
    Strict server-side validation & execution:
    1. Buyer check
    2. QR code existence and validity
    3. Expiry verification
    4. Offer status & validity
    5. Shop & Seller relation
    6. Admin-Configurable Geo-Fence In-Store Proximity Validation
    7. Duplicate redemption idempotency check
    8. Seller credit balance sufficiency
    9. Calculate discount & reward points
    10. Deduct seller credits & record wallet ledger
    11. Grant buyer reward points & record reward ledger
    12. Mark QR redeemed
    13. Increment offer redemption counter
    14. Log atomic OfferRedemption record with distance metadata
    15. Dispatch notifications to Buyer and Seller
    
    If ANY step fails, entire transaction rolls back cleanly.
    """
    if not buyer_user or not buyer_user.is_authenticated:
        return False, "Authentication required. Please log in as a buyer.", None

    if not qr_code_str:
        return False, "QR code token is missing.", None

    # BUG-105: If a full URL or path was scanned (e.g. https://domain/qr/<uuid>/ or /qr/<uuid>/), extract the code
    import re
    cleaned_code = str(qr_code_str).strip()
    match = re.search(r'(?:/qr/|^)([a-f0-9\-]{32,36})', cleaned_code, re.IGNORECASE)
    if match:
        cleaned_code = match.group(1)

    # Anomaly & IP Fraud Guardrail Check
    if client_ip:
        from services.leaderboard_service import check_ip_fraud_guardrail
        allowed, err_msg = check_ip_fraud_guardrail(client_ip, buyer_user.id)
        if not allowed:
            return False, err_msg, None

    # Idempotency token check
    if not idempotency_key:
        idempotency_key = f"redemp_{buyer_user.id}_{cleaned_code[:12]}"

    existing_redemption = OfferRedemption.objects.filter(idempotency_key=idempotency_key).first()
    if existing_redemption:
        return True, "This redemption has already been processed successfully.", existing_redemption

    # Pessimistic Row-Level Locking across QRCode, Offer, SellerWallet, and BuyerRewardAccount
    try:
        qr = QRCode.objects.select_for_update().get(code=cleaned_code)
    except QRCode.DoesNotExist:
        return False, "Invalid or unrecognized QR code.", None

    # Expiry and used checks
    if qr.is_used:
        return False, "This QR code has already been redeemed.", None

    if qr.is_expired:
        return False, "This QR code has expired. Please ask the shop for a fresh QR.", None

    # Lock Offer row pessimistically
    try:
        offer = Offer.objects.select_for_update().get(id=qr.offer_id)
    except Offer.DoesNotExist:
        return False, "Referenced promotion does not exist.", None

    shop = offer.shop
    seller = shop.seller

    # Check offer quota and validity AFTER acquiring row-level lock
    if offer.current_redemptions >= offer.max_redemptions:
        return False, "This promotion has reached its maximum redemption limit.", None

    if not offer.is_currently_valid:
        return False, f"Offer '{offer.title}' is currently inactive or fully claimed.", None

    if bill_amount and bill_amount < offer.min_purchase:
        return False, f"Minimum bill amount of ₹{int(offer.min_purchase)} required for this offer.", None

    # --- GEO-FENCED IN-STORE PROXIMITY VALIDATION ---
    from apps.administration.models import PlatformSetting, AdminAction
    from services.location_service import calculate_distance_in_meters

    platform_setting = PlatformSetting.get_settings()
    redemption_distance = None
    is_geofence_verified = False

    shop_has_coords = (
        shop.latitude is not None and 
        shop.longitude is not None and 
        (float(shop.latitude) != 0.0 or float(shop.longitude) != 0.0)
    )

    if platform_setting.geofence_enabled and shop_has_coords:
        if buyer_lat is None or buyer_lng is None:
            return False, "Proximity Check Required: Location access must be enabled to verify in-store presence.", None

        try:
            b_lat = float(buyer_lat)
            b_lng = float(buyer_lng)
            s_lat = float(shop.latitude)
            s_lng = float(shop.longitude)
            redemption_distance = calculate_distance_in_meters(b_lat, b_lng, s_lat, s_lng)
        except (ValueError, TypeError):
            return False, "Proximity Check Failed: Invalid location coordinates provided.", None

        acc_buffer = 0.0
        try:
            if buyer_accuracy and float(buyer_accuracy) > 0:
                acc_buffer = min(float(buyer_accuracy), 100.0)
        except Exception:
            acc_buffer = 0.0

        effective_allowed_radius = (
            platform_setting.max_redemption_distance_meters + 
            max(platform_setting.gps_accuracy_tolerance_meters, int(acc_buffer))
        )

        if redemption_distance > effective_allowed_radius:
            # Suspicious remote scan attempt - record security audit log with IP (BUG-GEO-05)
            try:
                AdminAction.objects.create(
                    admin=None,
                    action_type="SECURITY_GEOFENCE_VIOLATION",
                    target_model="OfferRedemption",
                    target_id=str(offer.id),
                    ip_address=client_ip,
                    details=(
                        f"Proximity Check Blocked: Shopper '{buyer_user.username}' attempted redemption "
                        f"{int(redemption_distance)}m away from '{shop.name}' (allowed threshold: {effective_allowed_radius}m). "
                        f"Shopper GPS: ({b_lat:.5f}, {b_lng:.5f}), Store GPS: ({s_lat:.5f}, {s_lng:.5f})."
                    )
                )
            except Exception:
                pass

            return False, (
                f"Proximity Check Failed: You are approx. {int(redemption_distance)}m away from {shop.name}. "
                f"You must be physically inside the store (within {effective_allowed_radius}m) to redeem counter discounts."
            ), None

        is_geofence_verified = True
    elif shop_has_coords and buyer_lat is not None and buyer_lng is not None:
        try:
            redemption_distance = calculate_distance_in_meters(float(buyer_lat), float(buyer_lng), float(shop.latitude), float(shop.longitude))
        except Exception:
            redemption_distance = None

    # --- STRICT ANTI-ABUSE FREQUENCY & EARNING CAPS ---
    now = timezone.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Maximum 3 offer redemptions per buyer per calendar day
    today_redemptions = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_day
    )
    if today_redemptions.count() >= 3:
        return False, "Daily redemption limit reached (Maximum 3 offer redemptions per calendar day).", None

    # 2. Strictly 1 offer redemption per seller per buyer per day
    if today_redemptions.filter(seller=seller).exists():
        return False, "Daily limit reached for this merchant (Strictly 1 offer redemption per seller per day).", None

    # 3. Dynamic 1% Point Calculation: 1% of offer's min_purchase, capped at 25 points
    from services.reward_service import calculate_offer_reward_points
    from django.db.models import Sum
    base_reward_points = calculate_offer_reward_points(offer.min_purchase)

    # 4. Maximum 25 reward points per buyer per calendar day
    today_points = today_redemptions.aggregate(total=Sum('reward_points_earned'))['total'] or 0
    if today_points >= 25:
        return False, "Daily reward points limit reached (Maximum 25 points per calendar day).", None

    # 5. Weekly cap: 150 points
    week_redemptions = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_week
    )
    week_points = week_redemptions.aggregate(total=Sum('reward_points_earned'))['total'] or 0
    if week_points >= 150:
        return False, "Weekly reward points limit reached (Maximum 150 points per calendar week).", None

    # 6. Monthly cap: 500 points
    month_redemptions = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_month
    )
    month_points = month_redemptions.aggregate(total=Sum('reward_points_earned'))['total'] or 0
    if month_points >= 500:
        return False, "Monthly reward points limit reached (Maximum 500 points per calendar month).", None

    # Calculate remaining points allowed under daily, weekly, and monthly caps
    points_allowed_today = max(0, 25 - today_points)
    points_allowed_week = max(0, 150 - week_points)
    points_allowed_month = max(0, 500 - month_points)
    allowed_points_cap = min(points_allowed_today, points_allowed_week, points_allowed_month)

    reward_points = min(base_reward_points, allowed_points_cap)

    # Lock SellerWallet row pessimistically
    required_credits = offer.credits_per_redemption
    seller_wallet = get_or_create_wallet(seller)
    seller_wallet = SellerWallet.objects.select_for_update().get(id=seller_wallet.id)

    if seller_wallet.balance_credits < required_credits:
        return False, "Merchant promotional credits exhausted. Discount cannot be redeemed.", None

    # Lock BuyerRewardAccount row pessimistically
    buyer_reward_account = get_or_create_reward_account(buyer_user)
    from apps.rewards.models import BuyerRewardAccount
    buyer_reward_account = BuyerRewardAccount.objects.select_for_update().get(id=buyer_reward_account.id)

    # Calculate actual discount
    calculated_discount = Decimal('0.00')
    bill_calc = bill_amount if bill_amount > 0 else Decimal('500.00')  # default simulated bill
    if offer.offer_type == 'PERCENTAGE':
        pct = offer.discount_value / Decimal('100.0')
        calculated_discount = bill_calc * pct
        if offer.max_discount and calculated_discount > offer.max_discount:
            calculated_discount = offer.max_discount
    elif offer.offer_type == 'FLAT':
        calculated_discount = min(offer.discount_value, bill_calc)
    else:
        calculated_discount = min(Decimal('100.00'), bill_calc)  # value for BOGO/Special

    # Prevent negative checkout by ensuring discount never exceeds bill amount (BUG-47)
    calculated_discount = min(calculated_discount, bill_calc)

    # 1. Create Redemption record with Idempotency Key collision protection
    from django.db import IntegrityError
    try:
        redemption = OfferRedemption.objects.create(
            qr_code=qr,
            offer=offer,
            shop=shop,
            buyer=buyer_user,
            seller=seller,
            bill_amount=bill_amount or Decimal('500.00'),
            discount_given=calculated_discount,
            credits_deducted=required_credits,
            reward_points_earned=reward_points,
            idempotency_key=idempotency_key,
            status=RedemptionStatus.SUCCESS,
            redemption_distance_meters=redemption_distance,
            is_geofence_verified=is_geofence_verified,
            ip_address=client_ip,
            device_token=device_token or ""
        )
    except IntegrityError:
        # Concurrent duplicate request or network double-tap caught by DB unique constraint
        existing = OfferRedemption.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return True, "This redemption has already been processed successfully.", existing
        return False, "A concurrent transaction with this key is already in progress.", None

    # 2. Increment offer redemptions atomically at database level using F()
    from django.db.models import F
    Offer.objects.filter(id=offer.id).update(
        current_redemptions=F('current_redemptions') + 1
    )
    offer.refresh_from_db()

    # 3. Mark QR code as used only if the campaign reaches its maximum redemption quota
    # Standee / counter QR codes remain valid for subsequent customers until quota is exhausted
    if offer.current_redemptions >= offer.max_redemptions:
        qr.is_used = True
        qr.save(update_fields=['is_used'])
        if offer.status != OfferStatus.EXPIRED:
            Offer.objects.filter(id=offer.id).update(status=OfferStatus.EXPIRED)
            offer.refresh_from_db()

    # 4. Deduct merchant credits via wallet service (uses atomic F-expression & select_for_update)
    deduct_credits(
        seller_user=seller,
        amount_credits=required_credits,
        reference_id=f"REDEMPTION_#{redemption.id}",
        notes=f"Redeemed: {offer.title} by {buyer_user.username}"
    )

    # Credit 1 Merchant Loyalty Point to Seller & check loyalty perk unlocks (50 & 100 pts)
    SellerWallet.objects.filter(seller=seller).update(loyalty_points=F('loyalty_points') + 1)
    sw = SellerWallet.objects.filter(seller=seller).first()
    if sw:
        from services.leaderboard_service import check_and_unlock_seller_loyalty_perks
        check_and_unlock_seller_loyalty_perks(sw)

    # 5. Award buyer loyalty points via reward service
    if reward_points > 0:
        award_points(
            buyer_user=buyer_user,
            points=reward_points,
            transaction_type=RewardType.REDEMPTION_REWARD,
            redemption=redemption,
            notes=f"Redeemed offer '{offer.title}' at {shop.name}"
        )

    # 6. Update buyer profile count atomically (BUG-75)
    from django.db.models import F
    from apps.accounts.models import BuyerProfile
    BuyerProfile.objects.filter(user=buyer_user).update(
        total_redemptions=F('total_redemptions') + 1
    )

    # 7. Notifications
    try:
        notify_redemption_to_buyer(buyer_user, offer, calculated_discount, reward_points)
        notify_redemption_to_seller(seller, offer, buyer_user, required_credits)
        updated_wallet = SellerWallet.objects.get(seller=seller)
        if updated_wallet.balance_credits < 15:
            notify_low_credits(seller, updated_wallet.balance_credits)
    except Exception:
        pass  # Notification errors should not abort transaction

    return True, "Success! Offer redeemed and rewards credited to your account.", redemption


@transaction.atomic
def initiate_redemption(
    buyer_user,
    offer: Offer,
    bill_amount: Decimal = Decimal('0.00'),
    idempotency_key: str = None,
    buyer_lat: float | None = None,
    buyer_lng: float | None = None,
    buyer_accuracy: float = 0.0,
    client_ip: str | None = None,
    device_token: str | None = None
) -> tuple[bool, str, OfferRedemption | None]:
    """
    Step 1 of Master Counter Store QR & Live Seller Acceptance Workflow:
    Creates an OfferRedemption in PENDING_APPROVAL status with a 4-digit counter token.
    Shows a clean waiting screen on buyer's phone.
    No credits are deducted and no points are awarded until merchant clicks "Approve".
    """
    if not buyer_user or not buyer_user.is_authenticated:
        return False, "Authentication required. Please log in as a buyer.", None

    if not offer:
        return False, "Offer not found or invalid.", None

    # Anomaly & IP Fraud Guardrail Check
    if client_ip:
        from services.leaderboard_service import check_ip_fraud_guardrail
        allowed, err_msg = check_ip_fraud_guardrail(client_ip, buyer_user.id)
        if not allowed:
            return False, err_msg, None

    shop = offer.shop
    seller = shop.seller

    # Check offer quota and validity
    if offer.current_redemptions >= offer.max_redemptions:
        return False, "This promotion has reached its maximum redemption limit.", None

    if not offer.is_currently_valid:
        return False, f"Offer '{offer.title}' is currently inactive or fully claimed.", None

    if bill_amount and bill_amount < offer.min_purchase:
        return False, f"Minimum bill amount of ₹{int(offer.min_purchase)} required for this offer.", None

    # --- GEO-FENCED IN-STORE PROXIMITY VALIDATION (BUG-GEO-01) ---
    from apps.administration.models import PlatformSetting, AdminAction
    from services.location_service import calculate_distance_in_meters, safe_parse_coordinates

    platform_setting = PlatformSetting.get_settings()
    redemption_distance = None
    is_geofence_verified = False

    shop_has_coords = (
        shop.latitude is not None and 
        shop.longitude is not None and 
        (float(shop.latitude) != 0.0 or float(shop.longitude) != 0.0)
    )

    if shop_has_coords and buyer_lat is not None and buyer_lng is not None:
        b_lat, b_lng = safe_parse_coordinates(buyer_lat, buyer_lng)
        if b_lat is not None and b_lng is not None:
            try:
                s_lat = float(shop.latitude)
                s_lng = float(shop.longitude)
                redemption_distance = calculate_distance_in_meters(b_lat, b_lng, s_lat, s_lng)
            except (ValueError, TypeError):
                redemption_distance = None

            acc_buffer = 0.0
            try:
                if buyer_accuracy and float(buyer_accuracy) > 0:
                    acc_buffer = min(float(buyer_accuracy), 100.0)
            except Exception:
                acc_buffer = 0.0

            effective_allowed_radius = (
                platform_setting.max_redemption_distance_meters + 
                max(platform_setting.gps_accuracy_tolerance_meters, int(acc_buffer))
            )

            if platform_setting.geofence_enabled and redemption_distance is not None and redemption_distance > effective_allowed_radius:
                try:
                    AdminAction.objects.create(
                        admin=None,
                        action_type="SECURITY_GEOFENCE_VIOLATION",
                        target_model="OfferRedemption",
                        target_id=str(offer.id),
                        ip_address=client_ip,
                        details=(
                            f"Proximity Check Blocked: Shopper '{buyer_user.username}' attempted counter claim "
                            f"{int(redemption_distance)}m away from '{shop.name}' (allowed threshold: {effective_allowed_radius}m). "
                            f"Shopper GPS: ({b_lat:.5f}, {b_lng:.5f}), Store GPS: ({s_lat:.5f}, {s_lng:.5f})."
                        )
                    )
                except Exception:
                    pass

                return False, (
                    f"Proximity Check Failed: You are approx. {int(redemption_distance)}m away from {shop.name}. "
                    f"You must be physically inside the store (within {effective_allowed_radius}m) to redeem counter discounts."
                ), None

            if redemption_distance is not None and redemption_distance <= effective_allowed_radius:
                is_geofence_verified = True

    # Anti-Abuse Frequency & Earning Caps (BUG-94: localized to store/buyer timezone)
    now = timezone.now()
    local_now = timezone.localtime(now)
    start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week = (local_now - timedelta(days=local_now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_month = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Maximum 3 offer redemptions per buyer per calendar day
    today_redemptions = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_day
    )
    if today_redemptions.count() >= 3:
        return False, "Daily redemption limit reached (Maximum 3 offer redemptions per calendar day).", None

    # 2. Strictly 1 offer redemption per seller per buyer per day
    if today_redemptions.filter(seller=seller).exists():
        return False, "Daily limit reached for this merchant (Strictly 1 offer redemption per seller per day).", None

    # 3. Dynamic 1% Point Calculation: 1% of offer's min_purchase, capped at 25 points
    from services.reward_service import calculate_offer_reward_points
    from django.db.models import Sum
    base_reward_points = calculate_offer_reward_points(offer.min_purchase)

    # 4. Maximum 25 reward points per buyer per calendar day
    today_points = today_redemptions.aggregate(total=Sum('reward_points_earned'))['total'] or 0
    if today_points >= 25:
        return False, "Daily reward points limit reached (Maximum 25 points per calendar day).", None

    # 5. Weekly cap: 150 points
    week_redemptions = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_week
    )
    week_points = week_redemptions.aggregate(total=Sum('reward_points_earned'))['total'] or 0
    if week_points >= 150:
        return False, "Weekly reward points limit reached (Maximum 150 points per calendar week).", None

    # 6. Monthly cap: 500 points
    month_redemptions = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_month
    )
    month_points = month_redemptions.aggregate(total=Sum('reward_points_earned'))['total'] or 0
    if month_points >= 500:
        return False, "Monthly reward points limit reached (Maximum 500 points per calendar month).", None

    points_allowed_today = max(0, 25 - today_points)
    points_allowed_week = max(0, 150 - week_points)
    points_allowed_month = max(0, 500 - month_points)
    allowed_points_cap = min(points_allowed_today, points_allowed_week, points_allowed_month)

    reward_points = min(base_reward_points, allowed_points_cap)

    # Check seller wallet credits (must have at least offer.credits_per_redemption credits)
    required_credits = offer.credits_per_redemption
    seller_wallet = get_or_create_wallet(seller)
    if seller_wallet.balance_credits < required_credits:
        return False, f"Merchant promotional credits exhausted (Required: {required_credits} credits). Discount cannot be redeemed.", None

    # Calculate actual discount
    calculated_discount = Decimal('0.00')
    bill_calc = bill_amount if bill_amount > 0 else (offer.min_purchase if offer.min_purchase > 0 else Decimal('500.00'))
    if offer.offer_type == 'PERCENTAGE':
        pct = offer.discount_value / Decimal('100.0')
        calculated_discount = bill_calc * pct
        if offer.max_discount and calculated_discount > offer.max_discount:
            calculated_discount = offer.max_discount
    elif offer.offer_type == 'FLAT':
        calculated_discount = min(offer.discount_value, bill_calc)
    else:
        calculated_discount = min(Decimal('100.00'), bill_calc)

    # Prevent negative checkout by ensuring discount never exceeds bill amount (BUG-47)
    calculated_discount = min(calculated_discount, bill_calc)

    # Generate 4-digit counter token (CSPRNG via secrets)
    import secrets
    token_4_digit = f"{secrets.randbelow(9000) + 1000}"

    # Generate active QR reference
    qr_obj, _ = create_or_refresh_offer_qr(offer)

    # Check if there is an existing pending redemption for this buyer and shop
    recent_pending = OfferRedemption.objects.filter(
        buyer=buyer_user,
        shop=shop,
        status=RedemptionStatus.PENDING_APPROVAL,
    ).order_by('-redeemed_at').first()
    if recent_pending:
        if recent_pending.is_expired:
            # Stale / Expired redemption: auto-cancel so buyer can generate a fresh active token
            recent_pending.status = RedemptionStatus.CANCELLED
            recent_pending.save(update_fields=['status'])
        else:
            return True, "Waiting for store counter approval...", recent_pending

    if not idempotency_key:
        idempotency_key = f"pending_{buyer_user.id}_{offer.id}_{uuid.uuid4().hex[:8]}"

    from django.db import IntegrityError
    try:
        redemption = OfferRedemption.objects.create(
            qr_code=qr_obj,
            offer=offer,
            shop=shop,
            buyer=buyer_user,
            seller=seller,
            bill_amount=bill_amount or offer.min_purchase or Decimal('500.00'),
            discount_given=calculated_discount,
            credits_deducted=required_credits,
            reward_points_earned=reward_points,
            idempotency_key=idempotency_key,
            status=RedemptionStatus.PENDING_APPROVAL,
            verification_otp=token_4_digit,
            otp_expires_at=now + timedelta(minutes=15),
            is_verified_by_merchant=False,
            redemption_distance_meters=redemption_distance,
            is_geofence_verified=is_geofence_verified,
            ip_address=client_ip,
            device_token=device_token or ""
        )
    except IntegrityError:
        existing = OfferRedemption.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return True, "Waiting for store counter approval...", existing
        return False, "A concurrent transaction with this key is already in progress.", None

    return True, "Waiting for store counter approval...", redemption


@transaction.atomic
def approve_redemption(redemption_id: int, seller_user) -> tuple[bool, str, OfferRedemption | None]:
    """
    Step 2 of Master Counter Store QR Workflow:
    Merchant clicks "Approve":
    - Deducts 1 credit/ticket from Seller's wallet.
    - Credits 1 Merchant Loyalty Point to Seller.
    - Triggers seller loyalty perks check (50 & 100 points unlock featured/spotlight store).
    - Credits calculated 1% reward points to Buyer's ledger.
    - Marks redemption status as SUCCESS and updates buyer screen instantly.
    """
    try:
        redemption = OfferRedemption.objects.select_for_update().get(id=redemption_id, seller=seller_user)
    except OfferRedemption.DoesNotExist:
        return False, "Redemption request not found or unauthorized.", None

    if redemption.status != RedemptionStatus.PENDING_APPROVAL:
        return False, f"Request is not pending approval (Current status: {redemption.get_status_display()}).", redemption

    now = timezone.now()
    if redemption.is_expired or (redemption.otp_expires_at and now > redemption.otp_expires_at):
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])
        return False, "This redemption counter token has expired (15-minute validity elapsed). Please ask the shopper to generate a fresh token.", redemption

    # Pessimistically lock Offer row to eliminate concurrency race conditions on quota cap
    try:
        offer = Offer.objects.select_for_update().get(id=redemption.offer_id)
    except Offer.DoesNotExist:
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])
        return False, "Referenced promotion does not exist.", redemption

    if offer.current_redemptions >= offer.max_redemptions:
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])
        if offer.status != OfferStatus.EXPIRED:
            Offer.objects.filter(id=offer.id).update(status=OfferStatus.EXPIRED)
        return False, f"Offer '{offer.title}' has reached its maximum redemption quota.", redemption

    if not offer.is_currently_valid:
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])
        return False, f"Offer '{offer.title}' is no longer active or valid.", redemption

    shop = redemption.shop
    buyer_user = redemption.buyer

    # Re-verify daily anti-abuse caps inside atomic transaction lock (BUG-94)
    local_now = timezone.localtime(now)
    start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_approved = OfferRedemption.objects.filter(
        buyer=buyer_user,
        status=RedemptionStatus.SUCCESS,
        redeemed_at__gte=start_of_day
    )
    if today_approved.count() >= 3:
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])
        return False, "Daily redemption limit reached for customer (Maximum 3 offer redemptions per calendar day).", redemption

    if today_approved.filter(seller=seller_user).exists():
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])
        return False, "Customer has already redeemed an offer at this store today (Strictly 1 offer redemption per seller per day).", redemption

    # 1. Deduct offer.credits_per_redemption credits from Seller wallet
    required_credits = offer.credits_per_redemption
    seller_wallet = get_or_create_wallet(seller_user)
    seller_wallet = SellerWallet.objects.select_for_update().get(id=seller_wallet.id)
    if seller_wallet.balance_credits < required_credits:
        return False, f"Insufficient promotional credits (Required: {required_credits} credits). Please recharge credits to approve discounts.", None

    deduct_credits(
        seller_user=seller_user,
        amount_credits=required_credits,
        reference_id=f"REDEMPTION_#{redemption.id}",
        notes=f"Approved counter discount: {offer.title} for customer {buyer_user.username}"
    )

    # 2. Credit 1 Merchant Loyalty Point to Seller & check loyalty perk unlocks (50 & 100 pts)
    from django.db.models import F
    SellerWallet.objects.filter(pk=seller_wallet.pk).update(
        loyalty_points=F('loyalty_points') + 1
    )
    seller_wallet.refresh_from_db()
    from services.leaderboard_service import check_and_unlock_seller_loyalty_perks
    check_and_unlock_seller_loyalty_perks(seller_wallet)

    # 3. Credit calculated 1% reward points to Buyer
    if redemption.reward_points_earned > 0:
        award_points(
            buyer_user=buyer_user,
            points=redemption.reward_points_earned,
            transaction_type=RewardType.REDEMPTION_REWARD,
            redemption=redemption,
            notes=f"Approved counter discount for '{offer.title}' at {shop.name}"
        )

    # 4. Increment offer redemptions
    Offer.objects.filter(id=offer.id).update(
        current_redemptions=F('current_redemptions') + 1
    )
    offer.refresh_from_db()
    if offer.current_redemptions >= offer.max_redemptions:
        if offer.status != OfferStatus.EXPIRED:
            Offer.objects.filter(id=offer.id).update(status=OfferStatus.EXPIRED)
        if redemption.qr_code:
            redemption.qr_code.is_used = True
            redemption.qr_code.save(update_fields=['is_used'])

    # 6. Update buyer total redemptions atomically (BUG-75)
    from django.db.models import F
    from apps.accounts.models import BuyerProfile
    BuyerProfile.objects.filter(user=buyer_user).update(
        total_redemptions=F('total_redemptions') + 1
    )

    # 7. Update redemption status to SUCCESS
    redemption.status = RedemptionStatus.SUCCESS
    redemption.is_verified_by_merchant = True
    redemption.verified_at = timezone.now()
    redemption.save(update_fields=['status', 'is_verified_by_merchant', 'verified_at'])

    # Cancel any remaining pending tickets for this customer and seller
    OfferRedemption.objects.filter(
        buyer=buyer_user,
        seller=seller_user,
        status=RedemptionStatus.PENDING_APPROVAL
    ).exclude(id=redemption.id).update(status=RedemptionStatus.CANCELLED)

    # 8. Notifications
    try:
        notify_redemption_to_buyer(buyer_user, offer, redemption.discount_given, redemption.reward_points_earned)
        notify_redemption_to_seller(seller_user, offer, buyer_user, required_credits)
        if seller_wallet.balance_credits < 15:
            notify_low_credits(seller_user, seller_wallet.balance_credits)
    except Exception:
        pass

    return True, "Redemption approved successfully.", redemption


@transaction.atomic
def reject_redemption(redemption_id: int, seller_user, reason: str = "") -> tuple[bool, str, OfferRedemption | None]:
    """
    Merchant clicks "Reject":
    - Marks redemption status as CANCELLED.
    - No credits deducted from Seller.
    - No points awarded to Buyer.
    """
    try:
        redemption = OfferRedemption.objects.select_for_update().get(id=redemption_id, seller=seller_user)
    except OfferRedemption.DoesNotExist:
        return False, "Redemption request not found or unauthorized.", None

    if redemption.status != RedemptionStatus.PENDING_APPROVAL:
        return False, f"Request is not pending approval (Current status: {redemption.get_status_display()}).", redemption

    redemption.status = RedemptionStatus.CANCELLED
    redemption.rejection_reason = (reason or '').strip()
    redemption.save(update_fields=['status', 'rejection_reason'])

    return True, "Redemption request cancelled.", redemption

