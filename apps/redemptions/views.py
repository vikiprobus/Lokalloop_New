from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta

from apps.redemptions.models import OfferRedemption, RedemptionStatus
from services.redemption_service import approve_redemption, reject_redemption


@login_required
@require_GET
def seller_pending_redemptions_api(request):
    """
    Real-Time Seller Counter Polling API:
    Returns active PENDING_APPROVAL redemption requests for this merchant's billing desk.
    """
    profile = getattr(request.user, 'profile', None)
    if not (profile and profile.is_seller):
        return JsonResponse({'error': 'Unauthorized: Seller account required.'}, status=403)

    seller_profile = getattr(request.user, 'seller_profile', None)
    is_verified = bool(seller_profile and seller_profile.is_verified)
    has_approved_shop = request.user.shops.filter(is_approved=True).exists() if hasattr(request.user, 'shops') else False
    if seller_profile and not (is_verified or has_approved_shop):
        return JsonResponse({'error': 'Access restricted: Merchant account is pending compliance verification.'}, status=403)

    now = timezone.now()
    cutoff = now - timedelta(minutes=30)

    # Auto-cancel expired pending requests only if any exist to eliminate unneeded write locks during polling
    expired_ids = list(OfferRedemption.objects.filter(
        seller=request.user,
        status=RedemptionStatus.PENDING_APPROVAL,
        otp_expires_at__lt=now
    ).values_list('id', flat=True))
    if expired_ids:
        OfferRedemption.objects.filter(id__in=expired_ids).update(status=RedemptionStatus.CANCELLED)

    pending_qs = OfferRedemption.objects.filter(
        seller=request.user,
        status=RedemptionStatus.PENDING_APPROVAL,
        redeemed_at__gte=cutoff,
        otp_expires_at__gt=now
    ).select_related('buyer', 'offer', 'shop', 'buyer__buyer_profile').order_by('-redeemed_at')

    requests_data = []
    for r in pending_qs:
        bp = getattr(r.buyer, 'buyer_profile', None)
        cust_name = ""
        if bp and bp.nickname:
            cust_name = bp.nickname
        elif bp and bp.public_unique_id:
            cust_name = bp.public_unique_id
        else:
            cust_name = f"Shopper-{str(r.buyer.id).zfill(4)}"

        remaining_seconds = max(0, int((r.otp_expires_at - now).total_seconds())) if r.otp_expires_at else 0

        requests_data.append({
            'id': r.id,
            'customer_name': cust_name,
            'offer_title': r.offer.title,
            'min_purchase': float(r.offer.min_purchase),
            'discount_display': r.offer.discount_display,
            'verification_otp': r.verification_otp,
            'created_at': r.redeemed_at.strftime('%I:%M %p'),
            'remaining_seconds': remaining_seconds
        })

    return JsonResponse({
        'requests': requests_data,
        'count': len(requests_data)
    })


@login_required
@require_POST
def seller_approve_redemption_view(request, redemption_id):
    """
    Seller clicks "Approve":
    - Deducts 1 credit from Seller's wallet.
    - Credits 1 Merchant Loyalty Point to Seller.
    - Credits calculated 1% reward points to Buyer's ledger.
    - Marks redemption status as SUCCESS and updates buyer screen instantly.
    """
    profile = getattr(request.user, 'profile', None)
    if not (profile and profile.is_seller):
        return JsonResponse({'error': 'Unauthorized: Seller account required.'}, status=403)

    seller_profile = getattr(request.user, 'seller_profile', None)
    is_verified = bool(seller_profile and seller_profile.is_verified)
    has_approved_shop = request.user.shops.filter(is_approved=True).exists() if hasattr(request.user, 'shops') else False
    if seller_profile and not (is_verified or has_approved_shop):
        return JsonResponse({'error': 'Access restricted: Merchant account is pending compliance verification.'}, status=403)

    success, msg, redemption = approve_redemption(redemption_id, request.user)
    if success:
        return JsonResponse({
            'success': True,
            'message': msg,
            'redemption_id': redemption.id,
            'status': redemption.status
        })
    return JsonResponse({'success': False, 'message': msg}, status=400)


@login_required
@require_POST
def seller_reject_redemption_view(request, redemption_id):
    """
    Seller clicks "Reject":
    - Marks redemption status as CANCELLED.
    - No credits deducted, status CANCELLED.
    """
    profile = getattr(request.user, 'profile', None)
    if not (profile and profile.is_seller):
        return JsonResponse({'error': 'Unauthorized: Seller account required.'}, status=403)

    seller_profile = getattr(request.user, 'seller_profile', None)
    is_verified = bool(seller_profile and seller_profile.is_verified)
    has_approved_shop = request.user.shops.filter(is_approved=True).exists() if hasattr(request.user, 'shops') else False
    if seller_profile and not (is_verified or has_approved_shop):
        return JsonResponse({'error': 'Access restricted: Merchant account is pending compliance verification.'}, status=403)

    reason = request.POST.get('reason', '').strip()
    success, msg, redemption = reject_redemption(redemption_id, request.user, reason=reason)
    if success:
        return JsonResponse({
            'success': True,
            'message': msg,
            'redemption_id': redemption.id,
            'status': redemption.status
        })
    return JsonResponse({'success': False, 'message': msg}, status=400)


@require_GET
def redemption_status_api(request, redemption_id):
    """
    Live Status Polling API for Buyer Waiting Screen:
    Returns the real-time status of a counter redemption request.
    Strict IDOR and Privacy Guard: Restricts status checks to the involved buyer, seller, or staff.
    """
    redemption = get_object_or_404(
        OfferRedemption.objects.select_related('offer', 'shop', 'buyer', 'seller'),
        id=redemption_id
    )

    # High Security & IDOR Authorization Check:
    # Restricts status checks to the involved buyer, seller, staff, or customer with active session token
    is_authorized = False
    if request.user.is_authenticated:
        if request.user == redemption.buyer or request.user == redemption.seller or request.user.is_staff:
            is_authorized = True
    elif request.session.get('active_redemption_id') == redemption.id:
        is_authorized = True

    if not is_authorized:
        return JsonResponse({'error': 'Unauthorized: Access restricted to counter participant or merchant.'}, status=403)

    # Check if pending redemption has expired
    if redemption.status == RedemptionStatus.PENDING_APPROVAL and redemption.is_expired:
        redemption.status = RedemptionStatus.CANCELLED
        redemption.save(update_fields=['status'])

    is_expired = redemption.is_expired or (redemption.status == RedemptionStatus.CANCELLED and redemption.otp_expires_at and timezone.now() > redemption.otp_expires_at)

    rejection_reason = getattr(redemption, 'rejection_reason', '') or ''
    decline_message = f"Redemption request was declined: {rejection_reason}" if rejection_reason else "Redemption request was declined."

    return JsonResponse({
        'id': redemption.id,
        'status': redemption.status,
        'is_approved': redemption.status == RedemptionStatus.SUCCESS,
        'is_cancelled': redemption.status == RedemptionStatus.CANCELLED,
        'is_expired': is_expired,
        'rejection_reason': rejection_reason,
        'verification_otp': redemption.verification_otp,
        'discount_given': float(redemption.discount_given),
        'reward_points_earned': redemption.reward_points_earned,
        'offer_title': redemption.offer.title,
        'shop_name': redemption.shop.name,
        'message': (
            "Redemption token expired. Please generate a fresh token."
            if is_expired
            else (
                "Waiting for store counter approval..."
                if redemption.status == RedemptionStatus.PENDING_APPROVAL
                else ("Redemption approved successfully!" if redemption.status == RedemptionStatus.SUCCESS else decline_message)
            )
        )
    })
