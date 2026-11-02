import uuid
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Sum, F, Count
from apps.accounts.decorators import buyer_required
from apps.shops.models import Shop, ShopCategory
from apps.offers.models import Offer, OfferStatus
from apps.redemptions.models import QRCode, OfferRedemption, RedemptionStatus
from apps.rewards.models import (
    BuyerRewardAccount, RewardTransaction,
    RewardCatalogItem, BuyerRewardClaim, BumperBillSubmission
)
from apps.notifications.models import Notification
from services.location_service import annotate_shops_with_distance, safe_parse_coordinates
from services.redemption_service import process_redemption
from services.reward_service import (
    get_or_create_reward_account, redeem_catalog_reward, submit_bumper_bill
)


@buyer_required
def buyer_dashboard_view(request):
    """
    Consumer Homepage Dashboard
    """
    buyer = request.user
    profile = buyer.profile
    buyer_profile = getattr(buyer, 'buyer_profile', None)
    reward_account = get_or_create_reward_account(buyer)

    # City filter
    city = buyer_profile.preferred_city if buyer_profile else profile.city or 'Bengaluru'

    nearby_shops = Shop.objects.filter(is_approved=True, is_active=True, city__iexact=city).select_related('category')[:4]
    popular_offers = Offer.objects.filter(
        status=OfferStatus.ACTIVE,
        shop__is_active=True,
        shop__is_approved=True,
        shop__city__iexact=city,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now(),
        current_redemptions__lt=F('max_redemptions')
    ).select_related('shop', 'shop__category')[:4]
    categories = ShopCategory.objects.filter(is_active=True, parent__isnull=True)[:6]
    
    # Recent redemptions
    recent_redemptions = OfferRedemption.objects.filter(buyer=buyer).select_related('offer', 'shop')[:5]

    context = {
        'buyer': buyer,
        'city': city,
        'reward_account': reward_account,
        'nearby_shops': nearby_shops,
        'popular_offers': popular_offers,
        'categories': categories,
        'recent_redemptions': recent_redemptions,
    }
    return render(request, 'buyer/dashboard.html', context)


@buyer_required
def buyer_explore_view(request):
    """
    Buyer Explore Directory with Live Distance Sorting
    """
    query = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', '').strip()
    city = request.GET.get('city', 'Bengaluru').strip()
    view_type = request.GET.get('type', 'offers')  # default to offers for buyers
    lat = request.GET.get('lat')
    lng = request.GET.get('lng')

    categories = ShopCategory.objects.filter(is_active=True)

    shops = Shop.objects.filter(is_approved=True, is_active=True).select_related('category')
    lat_val, lng_val = safe_parse_coordinates(lat, lng)
    if city and city != 'all' and not (lat_val is not None and lng_val is not None and request.GET.get('city') is None):
        shops = shops.filter(city__iexact=city)
    if category_slug:
        shops = shops.filter(Q(category__slug=category_slug) | Q(subcategory__slug=category_slug) | Q(category__parent__slug=category_slug))
    if query:
        shops = shops.filter(
            Q(name__icontains=query) | Q(locality__icontains=query) | Q(tagline__icontains=query)
        )

    if lat_val is not None and lng_val is not None:
        try:
            shops_list = annotate_shops_with_distance(shops, lat_val, lng_val)
        except Exception:
            shops_list = list(shops)
    else:
        shops_list = list(shops)

    offers = Offer.objects.filter(
        status=OfferStatus.ACTIVE,
        shop__is_active=True,
        shop__is_approved=True,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now(),
        current_redemptions__lt=F('max_redemptions')
    ).select_related('shop', 'shop__category')
    if city and city != 'all' and not (lat and lng and request.GET.get('city') is None):
        offers = offers.filter(shop__city__iexact=city)
    if category_slug:
        offers = offers.filter(Q(shop__category__slug=category_slug) | Q(shop__subcategory__slug=category_slug) | Q(shop__category__parent__slug=category_slug))
    if query:
        offers = offers.filter(
            Q(title__icontains=query) | Q(shop__name__icontains=query) | Q(shop__locality__icontains=query)
        )

    context = {
        'shops': shops_list,
        'offers': offers,
        'categories': categories,
        'current_category': category_slug,
        'current_city': city,
        'current_query': query,
        'view_type': view_type,
        'user_lat': lat,
        'user_lng': lng,
    }
    return render(request, 'buyer/explore.html', context)


@buyer_required
def buyer_scan_view(request):
    """
    In-Store QR Scanner & Redemption Execution
    Supports scanning:
    1. Merchant Store QR URL -> redirects instantly to store active deals catalog (/shops/<slug>/)
    2. Specific Offer QR token/code -> proceeds to atomic checkout discount redemption
    """
    target_offer_id = request.GET.get('offer')
    preselected_offer = None
    preselected_qr = None
    if target_offer_id:
        preselected_offer = Offer.objects.filter(
            pk=target_offer_id,
            status=OfferStatus.ACTIVE,
            start_date__lte=timezone.now(),
            end_date__gte=timezone.now()
        ).select_related('shop').first()
        if preselected_offer:
            from services.redemption_service import create_or_refresh_offer_qr
            qr_obj, _ = create_or_refresh_offer_qr(preselected_offer)
            preselected_qr = qr_obj

    redemption_result = None

    if request.method == 'POST':
        qr_code_str = request.POST.get('qr_code', '').strip()
        bill_str = request.POST.get('bill_amount', '0.00').strip()
        idempotency_token = request.POST.get('idempotency_key', str(uuid.uuid4()))

        # Check if the scanned string is a store URL or contains a store slug
        import re
        # Pattern matching /store/<slug>/qr/, /shops/<slug>/ or /buyer/shops/<slug>/
        qr_match = re.search(r'/store/([a-zA-Z0-9_\-]+)/qr/?', qr_code_str)
        if qr_match:
            slug = qr_match.group(1)
            shop = Shop.objects.filter(slug=slug, is_approved=True, is_active=True).first()
            if shop:
                return redirect('shop_qr_landing', slug=slug)

        shop_match = re.search(r'/(?:buyer/)?shops/([a-zA-Z0-9_\-]+)/?', qr_code_str)
        if shop_match:
            slug = shop_match.group(1)
            shop = Shop.objects.filter(slug=slug, is_approved=True, is_active=True).first()
            if shop:
                messages.info(request, f"Welcome to {shop.name}! Here are all available deals and offers.")
                return redirect('shop_detail', slug=slug)

        # Also support raw shop slug or shop:slug
        if qr_code_str.startswith('shop:'):
            slug = qr_code_str.replace('shop:', '').strip()
            shop = Shop.objects.filter(slug=slug, is_approved=True, is_active=True).first()
            if shop:
                return redirect('shop_qr_landing', slug=slug)

        try:
            bill_amount = Decimal(bill_str) if bill_str else Decimal('0.00')
        except Exception:
            bill_amount = Decimal('0.00')

        # Extract client GPS coordinates for store proximity validation (BUG-116, BUG-GEO-04)
        buyer_lat_raw = request.POST.get('buyer_lat', '').strip()
        buyer_lng_raw = request.POST.get('buyer_lng', '').strip()
        buyer_acc_raw = request.POST.get('buyer_accuracy', '0').strip()
        buyer_lat, buyer_lng = safe_parse_coordinates(buyer_lat_raw, buyer_lng_raw)
        try:
            buyer_accuracy = float(buyer_acc_raw) if buyer_acc_raw else 0.0
        except Exception:
            buyer_accuracy = 0.0

        from config.middleware import get_client_ip
        client_ip = get_client_ip(request)
        device_token = request.headers.get('x-device-token', '') or request.POST.get('device_token', '')

        success, message, redemption = process_redemption(
            buyer_user=request.user,
            qr_code_str=qr_code_str,
            bill_amount=bill_amount,
            idempotency_key=idempotency_token,
            buyer_lat=buyer_lat,
            buyer_lng=buyer_lng,
            buyer_accuracy=buyer_accuracy,
            client_ip=client_ip,
            device_token=device_token
        )

        if success:
            messages.success(request, message)
            return render(request, 'buyer/redemption_success.html', {'redemption': redemption})
        else:
            messages.error(request, message)

    # List of demo QR codes if buyer needs testing assistance (strictly limited to local DEBUG mode)
    sample_qrs = []
    if getattr(settings, 'DEBUG', False):
        sample_qrs = list(QRCode.objects.filter(
            is_used=False,
            expires_at__gt=timezone.now(),
            offer__status=OfferStatus.ACTIVE,
            offer__shop__is_approved=True,
            offer__shop__is_active=True
        ).select_related('offer', 'offer__shop')[:5])

    context = {
        'preselected_offer': preselected_offer,
        'preselected_qr': preselected_qr,
        'sample_qrs': sample_qrs,
        'generated_idempotency_key': str(uuid.uuid4()),
    }
    return render(request, 'buyer/qr_scanner.html', context)


@buyer_required
def buyer_rewards_view(request):
    """
    Buyer Loyalty Points Ledger, Reward Burn Catalog, and Bumper Submissions
    """
    reward_account = get_or_create_reward_account(request.user)
    transactions = RewardTransaction.objects.filter(account=reward_account).select_related('redemption', 'redemption__offer')[:50]
    catalog_items = list(RewardCatalogItem.objects.filter(is_active=True).order_by('points_required', 'title'))
    for item in catalog_items:
        item.shortfall = max(0, item.points_required - reward_account.current_points)
    claimed_rewards = BuyerRewardClaim.objects.filter(buyer=request.user).select_related('catalog_item').order_by('-claimed_at')
    bumper_submissions = BumperBillSubmission.objects.filter(buyer=request.user).select_related('shop').order_by('-submitted_at')
    available_shops = Shop.objects.filter(is_approved=True, is_active=True).order_by('name')

    context = {
        'account': reward_account,
        'transactions': transactions,
        'catalog_items': catalog_items,
        'claimed_rewards': claimed_rewards,
        'bumper_submissions': bumper_submissions,
        'available_shops': available_shops,
        'today_str': timezone.localdate().strftime('%Y-%m-%d'),
    }
    return render(request, 'buyer/rewards.html', context)


@buyer_required
def buyer_redeem_catalog_reward_view(request, item_id):
    """
    Burn points to redeem an item from the dynamic catalog.
    """
    if request.method != 'POST':
        return redirect('buyer_rewards')

    success, msg, claim = redeem_catalog_reward(request.user, item_id)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('buyer_rewards')


@buyer_required
def buyer_submit_bumper_bill_view(request):
    """
    Submit a purchase receipt of ₹10,000 to ₹1,00,000 for bumper reward verification.
    Enforces mandatory bill receipt snapshot upload and valid purchase date.
    """
    if request.method != 'POST':
        return redirect('buyer_rewards')

    shop_id = request.POST.get('shop_id')
    bill_number = request.POST.get('bill_number')
    bill_date = request.POST.get('bill_date')
    bill_amount = request.POST.get('bill_amount')
    bill_image = request.FILES.get('bill_image')

    if not bill_image:
        messages.error(request, "Receipt snapshot / photo is required to verify your bumper purchase bill.")
        return redirect('buyer_rewards')

    if not bill_date:
        messages.error(request, "Please enter the purchase / bill date from your receipt.")
        return redirect('buyer_rewards')

    success, msg, submission = submit_bumper_bill(
        buyer_user=request.user,
        shop_id=shop_id,
        bill_number=bill_number,
        bill_amount=bill_amount,
        bill_image=bill_image,
        bill_date=bill_date,
        require_image=True
    )

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('buyer_rewards')


@buyer_required
def buyer_history_view(request):
    """
    Past Redemptions & Total Savings
    """
    redemptions = OfferRedemption.objects.filter(buyer=request.user).select_related('offer', 'shop', 'seller')
    
    successful_agg = OfferRedemption.objects.filter(
        buyer=request.user,
        status=RedemptionStatus.SUCCESS
    ).aggregate(
        total_saved=Sum('discount_given'),
        total_pts=Sum('reward_points_earned')
    )
    total_saved = successful_agg['total_saved'] or Decimal('0.00')
    total_pts = successful_agg['total_pts'] or 0

    context = {
        'redemptions': redemptions,
        'total_saved': total_saved,
        'total_pts': total_pts,
    }
    return render(request, 'buyer/history.html', context)


@buyer_required
def buyer_notifications_view(request):
    """
    Buyer In-App Notifications
    """
    # BUG-103: Force immediate queryset evaluation before issuing update query
    notifications = list(Notification.objects.filter(recipient=request.user))
    # Mark all read
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)

    return render(request, 'buyer/notifications.html', {'notifications': notifications})


@buyer_required
def buyer_account_view(request):
    """
    Consumer Account & Profile Dashboard
    """
    buyer = request.user
    profile = getattr(buyer, 'profile', None)
    buyer_profile = getattr(buyer, 'buyer_profile', None)
    reward_account = get_or_create_reward_account(buyer)

    if request.method == 'POST':
        from apps.accounts.shopper_utils import sanitize_user_input
        action = request.POST.get('action', 'update_profile')
        if action == 'update_profile':
            raw_nickname = request.POST.get('nickname', '').strip()
            raw_city = request.POST.get('city', '').strip()

            if buyer_profile:
                if raw_nickname:
                    clean_nickname = sanitize_user_input(raw_nickname, max_length=30, allow_alphanumeric_only=True)
                    buyer_profile.nickname = clean_nickname
                    buyer_profile.is_nickname_set = True
                if raw_city:
                    clean_city = sanitize_user_input(raw_city, max_length=50, allow_alphanumeric_only=False)
                    buyer_profile.preferred_city = clean_city
                    buyer_profile.district = clean_city
                    if profile:
                        profile.city = clean_city
                        profile.save(update_fields=['city'])
                buyer_profile.save()
                messages.success(request, "Shopper profile updated successfully!")
            return redirect('buyer_account')

    # Aggregates for 4-tile performance dashboard
    successful_agg = OfferRedemption.objects.filter(
        buyer=buyer,
        status=RedemptionStatus.SUCCESS
    ).aggregate(
        total_saved=Sum('discount_given'),
        deals_claimed=Count('id')
    )
    total_saved = successful_agg['total_saved'] or Decimal('0.00')
    deals_claimed = successful_agg['deals_claimed'] or 0
    bumper_entries = BumperBillSubmission.objects.filter(buyer=buyer).count()

    # Tier calculations
    lifetime_pts = reward_account.lifetime_earned
    if lifetime_pts >= 1000:
        tier_title = "Gold Club"
        tier_badge = "🥇 Gold Club"
        tier_class = "gold"
        next_tier_title = "Top Tier Achieved"
        points_needed = 0
        tier_progress = 100
    elif lifetime_pts >= 300:
        tier_title = "Silver Tier"
        tier_badge = "🥈 Silver Tier"
        tier_class = "silver"
        next_tier_title = "Gold Club"
        points_needed = max(0, 1000 - lifetime_pts)
        tier_progress = min(100, max(0, int(((lifetime_pts - 300) / 700) * 100)))
    else:
        tier_title = "Bronze Tier"
        tier_badge = "🥉 Bronze Tier"
        tier_class = "bronze"
        next_tier_title = "Silver Tier"
        points_needed = max(0, 300 - lifetime_pts)
        tier_progress = min(100, max(0, int((lifetime_pts / 300) * 100)))

    shopper_id = (
        buyer_profile.public_unique_id if buyer_profile and buyer_profile.public_unique_id
        else (buyer_profile.shopper_id if buyer_profile and buyer_profile.shopper_id
              else f"LL-{buyer.id:04d}")
    )
    nickname = buyer_profile.nickname if buyer_profile and buyer_profile.nickname else (buyer.first_name or "Shopper")
    city = buyer_profile.preferred_city if buyer_profile and buyer_profile.preferred_city else (profile.city if profile and profile.city else "Pune")
    district = buyer_profile.district if buyer_profile and buyer_profile.district else city

    unread_notifications_count = Notification.objects.filter(recipient=buyer, is_read=False).count()

    context = {
        'buyer': buyer,
        'profile': profile,
        'buyer_profile': buyer_profile,
        'reward_account': reward_account,
        'shopper_id': shopper_id,
        'nickname': nickname,
        'city': city,
        'district': district,
        'total_saved': total_saved,
        'deals_claimed': deals_claimed,
        'bumper_entries': bumper_entries,
        'tier_title': tier_title,
        'tier_badge': tier_badge,
        'tier_class': tier_class,
        'next_tier_title': next_tier_title,
        'points_needed': points_needed,
        'tier_progress': tier_progress,
        'unread_notifications_count': unread_notifications_count,
    }
    return render(request, 'buyer/account.html', context)

