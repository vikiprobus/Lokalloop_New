from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.cache import cache

from apps.accounts.decorators import admin_required
from apps.accounts.models import Profile, Role, BuyerProfile, SellerProfile, AccountDeletionLog
from apps.shops.models import Shop, ShopCategory, ShopSubcategory, ShopVerification
from apps.offers.models import Offer, OfferStatus
from apps.redemptions.models import OfferRedemption, QRCode
from apps.wallets.models import SellerWallet, CreditPurchase, CreditPackage, WalletTransaction
from apps.rewards.models import (
    BuyerRewardAccount, RewardTransaction,
    RewardCatalogItem, BuyerRewardClaim, BumperBillSubmission, CatalogRewardType
)
from apps.administration.models import AdminAction, SupportTicket, TicketStatus, ErrorLog, ErrorLogLevel, PlatformSetting, MerchantAgreementLog, ContactInquiry
from services.wallet_service import get_or_create_wallet
from services.notification_service import create_notification
from apps.accounts.utils import generate_unique_merchant_id


def log_admin_action(admin_user, action_type: str, target_model: str, target_id: str, details: str, request=None):
    ip_addr = None
    if request:
        ip_addr = request.META.get('REMOTE_ADDR')
    AdminAction.objects.create(
        admin=admin_user,
        action_type=action_type,
        target_model=target_model,
        target_id=str(target_id),
        details=details,
        ip_address=ip_addr
    )


@admin_required
def admin_dashboard_view(request):
    """
    Platform Governance & Executive KPI Overview
    """
    total_buyers = Profile.objects.filter(role=Role.BUYER, user__is_active=True).count()
    total_sellers = Profile.objects.filter(role=Role.SELLER, user__is_active=True).count()
    total_shops = Shop.objects.count()
    active_offers = Offer.objects.filter(status=OfferStatus.ACTIVE).count()
    total_redemptions = OfferRedemption.objects.count()

    total_credits_sold = CreditPurchase.objects.filter(status='SUCCESS').aggregate(total=Sum('credits_added'))['total'] or 0
    total_revenue_inr = CreditPurchase.objects.filter(status='SUCCESS').aggregate(total=Sum('amount_inr'))['total'] or 0
    total_rewards_issued = RewardTransaction.objects.filter(points__gt=0).aggregate(total=Sum('points'))['total'] or 0

    recent_sellers = SellerProfile.objects.select_related('user').order_by('-created_at')[:5]
    recent_redemptions = OfferRedemption.objects.select_related('buyer', 'buyer__buyer_profile', 'seller', 'shop', 'offer').order_by('-redeemed_at')[:6]
    pending_tickets = SupportTicket.objects.filter(status=TicketStatus.OPEN).count()
    unresolved_errors = ErrorLog.objects.filter(is_resolved=False).count()

    recent_deletions = AccountDeletionLog.objects.order_by('-deleted_at')[:6]

    context = {
        'total_buyers': total_buyers,
        'total_sellers': total_sellers,
        'total_shops': total_shops,
        'active_offers': active_offers,
        'total_redemptions': total_redemptions,
        'total_credits_sold': total_credits_sold,
        'total_revenue_inr': total_revenue_inr,
        'total_rewards_issued': total_rewards_issued,
        'recent_sellers': recent_sellers,
        'recent_redemptions': recent_redemptions,
        'recent_deletions': recent_deletions,
        'pending_tickets': pending_tickets,
        'unresolved_errors': unresolved_errors,
    }
    return render(request, 'admin/dashboard.html', context)


from datetime import timedelta, datetime
from services.export_service import export_to_excel_response

@admin_required
def admin_buyers_view(request):
    """
    Registered Buyers Directory with Location and Date Joined Filters & Excel Export
    """
    query = request.GET.get('q', '').strip()
    selected_city = request.GET.get('city', 'all').strip()
    selected_joined = request.GET.get('joined', 'all').strip()
    selected_status = request.GET.get('status', 'all').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    export_format = request.GET.get('export', '').lower().strip()

    buyers = User.objects.filter(profile__role=Role.BUYER).select_related('profile', 'buyer_profile', 'reward_account')
    total_buyers_count = buyers.filter(is_active=True).count()

    # Status filter
    if selected_status == 'active':
        buyers = buyers.filter(is_active=True)
    elif selected_status in ['deleted', 'inactive']:
        buyers = buyers.filter(is_active=False)

    # Text search (BUG-109: Include Public Unique ID, Shopper ID, and Nickname)
    if query:
        buyers = buyers.filter(
            Q(username__icontains=query) | Q(email__icontains=query) |
            Q(first_name__icontains=query) | Q(last_name__icontains=query) |
            Q(profile__city__icontains=query) | Q(profile__phone__icontains=query) |
            Q(buyer_profile__public_unique_id__icontains=query) |
            Q(buyer_profile__shopper_id__icontains=query) |
            Q(buyer_profile__nickname__icontains=query)
        )

    # City filter
    if selected_city and selected_city != 'all':
        buyers = buyers.filter(profile__city__iexact=selected_city)

    # Date joined filter
    now = timezone.now()
    if selected_joined == 'today':
        buyers = buyers.filter(date_joined__date=now.date())
    elif selected_joined == '7d':
        buyers = buyers.filter(date_joined__gte=now - timedelta(days=7))
    elif selected_joined == '30d':
        buyers = buyers.filter(date_joined__gte=now - timedelta(days=30))
    elif selected_joined == 'month':
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        buyers = buyers.filter(date_joined__gte=start_of_month)
    elif selected_joined == '3m':
        buyers = buyers.filter(date_joined__gte=now - timedelta(days=90))
    elif selected_joined == 'custom':
        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
                buyers = buyers.filter(date_joined__date__gte=parsed_from)
            except ValueError:
                pass
        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
                buyers = buyers.filter(date_joined__date__lte=parsed_to)
            except ValueError:
                pass

    buyers = buyers.order_by('-date_joined')
    filtered_count = buyers.count()

    # Excel Export Trigger
    if export_format == 'excel':
        headers = [
            "Shopper ID / Username", "Gender", "District / City", "State",
            "Points Balance", "Tier", "Total Redemptions", "Joined Date"
        ]
        rows = []
        for b in buyers:
            bp = getattr(b, 'buyer_profile', None)
            ra = getattr(b, 'reward_account', None)
            prof = getattr(b, 'profile', None)
            rows.append([
                getattr(bp, 'shopper_id', '') or b.username,
                getattr(bp, 'gender', '') or "N/A",
                getattr(bp, 'district', '') or getattr(prof, 'city', '') or "N/A",
                getattr(bp, 'state', '') or "N/A",
                getattr(ra, 'current_points', 0),
                getattr(ra, 'tier_name', 'BRONZE'),
                getattr(bp, 'total_redemptions', 0),
                b.date_joined.strftime("%d-%b-%Y %I:%M %p") if b.date_joined else "N/A"
            ])
        return export_to_excel_response(
            filename_prefix="lokalloop_buyers",
            sheet_title="Registered Buyers",
            headers=headers,
            rows=rows,
            title="LokalLoop — Registered Shoppers / Buyers Directory"
        )

    # Distinct cities for dropdown
    distinct_cities = (
        Profile.objects.filter(role=Role.BUYER)
        .exclude(city__isnull=True)
        .exclude(city='')
        .values_list('city', flat=True)
        .distinct()
        .order_by('city')
    )

    context = {
        'buyers': buyers[:100],
        'query': query,
        'selected_city': selected_city,
        'selected_joined': selected_joined,
        'selected_status': selected_status,
        'date_from': date_from,
        'date_to': date_to,
        'distinct_cities': distinct_cities,
        'total_buyers_count': total_buyers_count,
        'filtered_count': filtered_count,
        'has_filters': bool(query or (selected_city and selected_city != 'all') or (selected_joined and selected_joined != 'all') or (selected_status and selected_status != 'all')),
    }
    return render(request, 'admin/buyers.html', context)


@admin_required
def admin_sellers_view(request):
    """
    Merchant Profiles & Verification Console with Business Name, City, and Status Filters
    """
    query = request.GET.get('q', '').strip()
    selected_merchant = request.GET.get('store', 'all').strip()
    selected_city = request.GET.get('city', 'all').strip()
    selected_status = request.GET.get('status', 'all').strip()

    sellers = SellerProfile.objects.select_related('user', 'user__wallet', 'user__profile')
    total_sellers_count = sellers.filter(user__is_active=True).count()

    # Text search (BUG-110: Include Merchant ID, Registered Phone, Signatory Name, GSTIN)
    if query:
        sellers = sellers.filter(
            Q(business_name__icontains=query) | Q(user__email__icontains=query) |
            Q(user__username__icontains=query) | Q(user__profile__city__icontains=query) |
            Q(merchant_id__icontains=query) | Q(phone__icontains=query) |
            Q(full_name__icontains=query) | Q(gstin__icontains=query)
        )

    # Store / Business name dropdown
    if selected_merchant and selected_merchant != 'all':
        sellers = sellers.filter(id=selected_merchant)

    # City dropdown
    if selected_city and selected_city != 'all':
        sellers = sellers.filter(user__profile__city__iexact=selected_city)

    # Status dropdown (Approved, Pending / Suspended, Deleted, All)
    if selected_status == 'approved':
        sellers = sellers.filter(is_verified=True, user__is_active=True)
    elif selected_status in ['suspended', 'pending']:
        sellers = sellers.filter(is_verified=False, user__is_active=True)
    elif selected_status in ['deleted', 'inactive']:
        sellers = sellers.filter(user__is_active=False)

    sellers = sellers.order_by('-created_at')
    filtered_count = sellers.count()

    # Excel Export Trigger
    export_format = request.GET.get('export', '').lower().strip()
    if export_format == 'excel':
        headers = [
            "Business Name", "Owner Username", "Owner Email", "Phone", "City",
            "GSTIN", "PAN Number", "Wallet Credits", "Status", "Registered Date"
        ]
        rows = []
        for s in sellers:
            u = s.user
            prof = getattr(u, 'profile', None)
            wal = getattr(u, 'wallet', None)
            phone = s.phone or getattr(prof, 'phone', '') or "N/A"
            city = getattr(prof, 'city', '') or "N/A"
            credits_val = getattr(wal, 'balance_credits', 0) if wal else 0
            rows.append([
                s.business_name,
                s.user.username,
                s.user.email,
                phone,
                city,
                s.gstin or "N/A",
                s.pan_number or "N/A",
                credits_val,
                "Approved" if s.is_verified else "Suspended / Pending",
                s.created_at.strftime("%d-%b-%Y %I:%M %p") if s.created_at else "N/A"
            ])
        return export_to_excel_response(
            filename_prefix="lokalloop_merchants",
            sheet_title="Merchants",
            headers=headers,
            rows=rows,
            title="LokalLoop — Platform Merchants & Sellers Directory"
        )

    # Dynamic dropdown options
    all_merchants = SellerProfile.objects.only('id', 'business_name').order_by('business_name')
    distinct_cities = (
        Profile.objects.filter(role=Role.SELLER)
        .exclude(city__isnull=True)
        .exclude(city='')
        .values_list('city', flat=True)
        .distinct()
        .order_by('city')
    )

    context = {
        'sellers': sellers[:100],
        'query': query,
        'selected_merchant': selected_merchant,
        'selected_city': selected_city,
        'selected_status': selected_status,
        'all_merchants': all_merchants,
        'distinct_cities': distinct_cities,
        'total_sellers_count': total_sellers_count,
        'filtered_count': filtered_count,
        'has_filters': bool(query or (selected_merchant and selected_merchant != 'all') or (selected_city and selected_city != 'all') or (selected_status and selected_status != 'all')),
    }
    return render(request, 'admin/sellers.html', context)


@admin_required
def admin_seller_toggle_approval_view(request, seller_id):
    """
    Approve or Suspend Merchant Account.
    Requires secure POST with CSRF validation.
    When approving:
    - SellerProfile.is_verified = True & verified_at = timezone.now()
    - Assign unique city-coded merchant_id if missing
    - Sync all merchant shops (is_approved=True, is_active=True)
    - Sync ShopVerification.status = 'APPROVED'
    - Grant 100 starter promotional credits
    - Send in-app notification & alert
    """
    if request.method != 'POST':
        messages.error(request, "Invalid request method. POST is required to toggle merchant status.")
        return redirect('/admin-portal/sellers/')

    seller_profile = get_object_or_404(SellerProfile, pk=seller_id)
    seller_profile.is_verified = not seller_profile.is_verified
    seller_user = seller_profile.user

    if seller_profile.is_verified:
        seller_profile.verified_at = timezone.now()
        if not seller_profile.merchant_id or seller_profile.merchant_id.startswith('MER-'):
            city = getattr(seller_user.profile, 'city', 'Pune') if hasattr(seller_user, 'profile') else 'Pune'
            seller_profile.merchant_id = generate_unique_merchant_id(city)
        seller_profile.save()

        # Sync all shops and verifications
        for sh in seller_user.shops.all():
            sh.is_approved = True
            sh.is_active = True
            sh.save(update_fields=['is_approved', 'is_active'])
            if hasattr(sh, 'verification'):
                sh.verification.status = 'APPROVED'
                sh.verification.verified_by = request.user
                sh.verification.verified_at = timezone.now()
                sh.verification.save(update_fields=['status', 'verified_by', 'verified_at'])

        # Ensure 50 starter promotional credits are initialized (50 credits limit per platform policy)
        get_or_create_wallet(seller_user)

        # In-app notification
        create_notification(
            recipient=seller_user,
            title="🎉 Store Approved & Verified!",
            message=f"🎉 Congratulations! Your store {seller_profile.business_name} has been verified and approved. Your official Merchant ID is {seller_profile.merchant_id}. All selling features are now active!",
            link_url="/seller/dashboard/"
        )
    else:
        seller_profile.save()
        for sh in seller_user.shops.all():
            sh.is_approved = False
            sh.is_active = False
            sh.save(update_fields=['is_approved', 'is_active'])
            if hasattr(sh, 'verification'):
                sh.verification.status = 'REJECTED'
                sh.verification.save(update_fields=['status'])
        Offer.objects.filter(shop__seller=seller_user, status=OfferStatus.ACTIVE).update(status=OfferStatus.PAUSED)

    status_str = "Approved" if seller_profile.is_verified else "Suspended"
    log_admin_action(
        admin_user=request.user,
        action_type=f"MERCHANT_{status_str.upper()}",
        target_model="SellerProfile",
        target_id=str(seller_profile.id),
        details=f"Admin set merchant {seller_profile.business_name} ({seller_user.username}) verification status to {status_str}.",
        request=request
    )

    messages.success(request, f"Merchant '{seller_profile.business_name}' status changed to: {status_str}.")
    ref = request.META.get('HTTP_REFERER')
    fallback_url = '/admin-portal/sellers/'
    if ref and url_has_allowed_host_and_scheme(ref, allowed_hosts={request.get_host()}):
        return redirect(ref)
    return redirect(fallback_url)


@admin_required
def admin_shops_view(request):
    """
    Platform Shops & Outlets Moderation with Merchant Store and City Filters
    """
    query = request.GET.get('q', '').strip()
    selected_store = request.GET.get('store', 'all').strip()
    selected_city = request.GET.get('city', 'all').strip()

    shops = Shop.objects.select_related('seller', 'seller__seller_profile', 'category')
    total_shops_count = shops.count()

    # Text search
    if query:
        shops = shops.filter(
            Q(name__icontains=query) | Q(locality__icontains=query) | Q(city__icontains=query) |
            Q(seller__seller_profile__business_name__icontains=query)
        )

    # Store (Merchant) dropdown: filter by merchant's seller profile ID or user ID
    if selected_store and selected_store != 'all':
        shops = shops.filter(seller__seller_profile__id=selected_store)

    # City dropdown
    if selected_city and selected_city != 'all':
        shops = shops.filter(city__iexact=selected_city)

    shops = shops.order_by('-created_at')
    filtered_count = shops.count()

    # Excel Export Trigger
    export_format = request.GET.get('export', '').lower().strip()
    if export_format == 'excel':
        headers = [
            "Store Name", "Merchant Business", "Owner Username", "Category",
            "Locality", "City", "PIN Code", "Phone", "Rating", "Active Offers", "Status"
        ]
        rows = []
        for s in shops:
            rows.append([
                s.name,
                (getattr(getattr(s.seller, 'seller_profile', None), 'business_name', '') or (s.seller.username if s.seller else '')),
                s.seller.username,
                s.category.name if s.category else "Uncategorized",
                s.locality,
                s.city,
                s.pincode,
                s.phone,
                float(s.rating) if s.rating else 0.0,
                s.active_offers_count,
                "Live / Active" if (s.is_approved and s.is_active) else "Inactive / Pending"
            ])
        return export_to_excel_response(
            filename_prefix="lokalloop_shops",
            sheet_title="Shops & Outlets",
            headers=headers,
            rows=rows,
            title="LokalLoop — Outlets & Storefronts Directory"
        )

    # Dropdown dynamic values
    merchant_stores = (
        SellerProfile.objects.filter(user__shops__isnull=False)
        .distinct()
        .order_by('business_name')
    )
    distinct_cities = (
        Shop.objects.exclude(city__isnull=True)
        .exclude(city='')
        .values_list('city', flat=True)
        .distinct()
        .order_by('city')
    )

    context = {
        'shops': shops[:100],
        'query': query,
        'selected_store': selected_store,
        'selected_city': selected_city,
        'merchant_stores': merchant_stores,
        'distinct_cities': distinct_cities,
        'total_shops_count': total_shops_count,
        'filtered_count': filtered_count,
        'has_filters': bool(query or (selected_store and selected_store != 'all') or (selected_city and selected_city != 'all')),
    }
    return render(request, 'admin/shops.html', context)


@admin_required
def admin_offers_view(request):
    """
    Platform Offers Moderation with Store Name and Approval/Live Status Filters
    """
    query = request.GET.get('q', '').strip()
    selected_store = request.GET.get('store', 'all').strip()
    selected_status = request.GET.get('status', 'all').strip()

    offers = Offer.objects.select_related('shop', 'shop__seller', 'shop__seller__seller_profile')
    total_offers_count = offers.count()

    # Text search
    if query:
        offers = offers.filter(
            Q(title__icontains=query) | Q(shop__name__icontains=query) | Q(shop__city__icontains=query)
        )

    # Store dropdown
    if selected_store and selected_store != 'all':
        offers = offers.filter(shop__id=selected_store)

    # Status dropdown
    if selected_status and selected_status != 'all':
        # Handles user-friendly status mappings
        if selected_status == 'APPROVED' or selected_status == 'ACTIVE':
            offers = offers.filter(status=OfferStatus.ACTIVE)
        elif selected_status == 'PAUSED':
            offers = offers.filter(status=OfferStatus.PAUSED)
        elif selected_status == 'PENDING' or selected_status == 'DRAFT':
            offers = offers.filter(status=OfferStatus.DRAFT)
        elif selected_status == 'EXPIRED':
            offers = offers.filter(status=OfferStatus.EXPIRED)
        else:
            offers = offers.filter(status=selected_status)

    offers = offers.order_by('-created_at')
    filtered_count = offers.count()

    # Excel Export Trigger
    export_format = request.GET.get('export', '').lower().strip()
    if export_format == 'excel':
        headers = [
            "Campaign Title", "Store Name", "Locality", "City", "Discount Type",
            "Discount Value", "Min Bill (INR)", "Claims / Quota", "Debit Credits",
            "Buyer Points", "End Date", "Status"
        ]
        rows = []
        for o in offers:
            rows.append([
                o.title,
                o.shop.name,
                o.shop.locality,
                o.shop.city,
                o.get_offer_type_display() if hasattr(o, 'get_offer_type_display') else o.offer_type,
                float(o.discount_value),
                float(o.min_purchase),
                f"{o.current_redemptions} / {o.max_redemptions}",
                o.credits_per_redemption,
                o.reward_points_per_redemption,
                o.end_date.strftime("%d-%b-%Y") if o.end_date else "N/A",
                o.get_status_display() if hasattr(o, 'get_status_display') else o.status
            ])
        return export_to_excel_response(
            filename_prefix="lokalloop_campaigns",
            sheet_title="Offers & Campaigns",
            headers=headers,
            rows=rows,
            title="LokalLoop — Promotional Offers & Campaign Moderation"
        )

    # Dropdown dynamic values: stores that have offers
    merchant_shops = Shop.objects.filter(offers__isnull=False).distinct().order_by('name')

    context = {
        'offers': offers[:100],
        'query': query,
        'selected_store': selected_store,
        'selected_status': selected_status,
        'merchant_shops': merchant_shops,
        'status_choices': OfferStatus.choices,
        'total_offers_count': total_offers_count,
        'filtered_count': filtered_count,
        'has_filters': bool(query or (selected_store and selected_store != 'all') or (selected_status and selected_status != 'all')),
    }
    return render(request, 'admin/offers.html', context)


@admin_required
def admin_redemptions_view(request):
    """
    Platform Redemptions Audit Log with Excel Export
    """
    query = request.GET.get('q', '').strip()
    redemptions = OfferRedemption.objects.select_related('buyer', 'buyer__buyer_profile', 'seller', 'shop', 'offer').order_by('-redeemed_at')
    total_redemptions_count = redemptions.count()

    if query:
        redemptions = redemptions.filter(
            Q(buyer__username__icontains=query) | Q(shop__name__icontains=query) |
            Q(idempotency_key__icontains=query) | Q(offer__title__icontains=query) |
            Q(buyer__buyer_profile__public_unique_id__icontains=query) |
            Q(buyer__buyer_profile__nickname__icontains=query) |
            Q(verification_otp__icontains=query) |
            Q(qr_code__code__icontains=query)
        )
    filtered_count = redemptions.count()

    # Excel Export Trigger
    export_format = request.GET.get('export', '').lower().strip()
    if export_format == 'excel':
        headers = [
            "Redemption ID", "Timestamp", "Idempotency Key", "Store Name",
            "Merchant Owner", "Shopper Nickname", "Public Unique ID", "Offer Claimed",
            "Bill Amount (INR)", "Discount Saved (INR)", "Credits Deducted", "Reward Points", "Verified"
        ]
        rows = []
        for r in redemptions:
            bp = getattr(r.buyer, 'buyer_profile', None) if r.buyer else None
            nickname = bp.nickname if (bp and bp.nickname) else "Shopper"
            public_id = bp.public_unique_id if (bp and bp.public_unique_id) else (f"LL-{str(r.buyer.id).zfill(4)}" if r.buyer else "N/A")
            rows.append([
                r.id,
                r.redeemed_at.strftime("%d-%b-%Y %I:%M %p") if r.redeemed_at else "N/A",
                r.idempotency_key,
                r.shop.name if r.shop else "N/A",
                r.seller.username if r.seller else "N/A",
                nickname,
                public_id,
                r.offer.title if r.offer else "N/A",
                float(r.bill_amount),
                float(r.discount_given),
                r.credits_deducted,
                r.reward_points_earned,
                "Yes" if r.is_verified_by_merchant else "No"
            ])
        return export_to_excel_response(
            filename_prefix="lokalloop_redemptions",
            sheet_title="Redemptions Audit",
            headers=headers,
            rows=rows,
            title="LokalLoop — Footfall Redemptions & Claims Audit Log"
        )

    context = {
        'redemptions': redemptions[:100],
        'query': query,
        'total_redemptions_count': total_redemptions_count,
        'filtered_count': filtered_count,
        'has_filters': bool(query),
    }
    return render(request, 'admin/redemptions.html', context)


@admin_required
def admin_credits_view(request):
    """
    Credit Packages, Merchant Purchases, and Circulation
    """
    packages = CreditPackage.objects.all()
    purchases = CreditPurchase.objects.select_related('seller', 'package').order_by('-created_at')[:50]
    wallets = SellerWallet.objects.select_related('seller').order_by('-balance_credits')[:25]

    context = {
        'packages': packages,
        'purchases': purchases,
        'wallets': wallets,
    }
    return render(request, 'admin/credits.html', context)


@admin_required
def admin_audit_log_view(request):
    """
    Audit Trail of All Administrative Actions and Statutory Account Deletions
    """
    actions = AdminAction.objects.select_related('admin').order_by('-created_at')[:100]
    deletion_logs = AccountDeletionLog.objects.order_by('-deleted_at')[:100]
    return render(request, 'admin/audit_logs.html', {'actions': actions, 'deletion_logs': deletion_logs})


@admin_required
def admin_tickets_view(request):
    """
    Support & Dispute Tickets and Guest Contact Inquiries / Grievances
    """
    tickets = SupportTicket.objects.select_related('user').order_by('-created_at')[:100]
    contact_inquiries = ContactInquiry.objects.select_related('user').order_by('-created_at')[:100]
    return render(request, 'admin/tickets.html', {
        'tickets': tickets,
        'contact_inquiries': contact_inquiries,
    })


@admin_required
def admin_ticket_update_view(request, ticket_id):
    """
    Update ticket status and notes
    """
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status in TicketStatus.values:
            ticket.status = new_status
        ticket.admin_notes = request.POST.get('admin_notes', ticket.admin_notes)
        ticket.save()

        log_admin_action(
            admin_user=request.user,
            action_type="TICKET_RESOLVED" if ticket.status == "RESOLVED" else "TICKET_UPDATED",
            target_model="SupportTicket",
            target_id=str(ticket.id),
            details=f"Admin updated ticket #{ticket.id} status to {ticket.status}",
            request=request
        )

        messages.success(request, f"Ticket #{ticket.id} updated.")
    return redirect('/admin-portal/tickets/')


@admin_required
def admin_inquiry_update_view(request, inquiry_id):
    """
    Update guest contact inquiry / statutory grievance status and notes
    """
    inquiry = get_object_or_404(ContactInquiry, pk=inquiry_id)
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status in TicketStatus.values:
            inquiry.status = new_status
        inquiry.admin_notes = request.POST.get('admin_notes', inquiry.admin_notes)
        inquiry.save()

        log_admin_action(
            admin_user=request.user,
            action_type="INQUIRY_RESOLVED" if inquiry.status == "RESOLVED" else "INQUIRY_UPDATED",
            target_model="ContactInquiry",
            target_id=str(inquiry.id),
            details=f"Admin updated inquiry {inquiry.ticket_number} status to {inquiry.status}",
            request=request
        )

        messages.success(request, f"Inquiry {inquiry.ticket_number} updated.")
    return redirect('/admin-portal/tickets/')


from apps.administration.models import ErrorLog, ErrorLogLevel


@admin_required
def admin_error_logs_view(request):
    """
    Platform Error & Exception Logs with Severity Filters, Search & Traceback Inspection.
    Automatically purges error logs older than 15 days upon loading.
    """
    # Automatic 15-day lifecycle cleanup (rate-limited via cache once per 24 hours)
    if cache.add('admin_error_logs_purged_today', True, timeout=86400):
        try:
            ErrorLog.purge_old_errors(days=15)
        except Exception:
            pass

    query = request.GET.get('q', '').strip()
    selected_level = request.GET.get('level', 'all').strip()
    selected_status = request.GET.get('status', 'all').strip()

    logs = ErrorLog.objects.select_related('user').order_by('-created_at')
    total_logs_count = logs.count()

    # Search filter
    if query:
        logs = logs.filter(
            Q(incident_id__icontains=query) |
            Q(error_type__icontains=query) |
            Q(message__icontains=query) |
            Q(path__icontains=query) |
            Q(user__username__icontains=query)
        )

    # Severity level filter
    if selected_level and selected_level != 'all':
        logs = logs.filter(level=selected_level)

    # Resolution status filter
    if selected_status == 'unresolved':
        logs = logs.filter(is_resolved=False)
    elif selected_status == 'resolved':
        logs = logs.filter(is_resolved=True)

    filtered_count = logs.count()
    unresolved_count = ErrorLog.objects.filter(is_resolved=False).count()

    context = {
        'logs': logs[:100],
        'total_logs_count': total_logs_count,
        'filtered_count': filtered_count,
        'unresolved_count': unresolved_count,
        'query': query,
        'selected_level': selected_level,
        'selected_status': selected_status,
        'level_choices': ErrorLogLevel.choices,
        'has_filters': bool(query or (selected_level and selected_level != 'all') or (selected_status and selected_status != 'all')),
    }
    return render(request, 'admin/error_logs.html', context)


@admin_required
def admin_error_log_resolve_view(request, log_id):
    """
    Toggle resolved status on an error log entry.
    Requires secure POST with CSRF validation.
    """
    if request.method != 'POST':
        messages.error(request, "Invalid request method. POST is required.")
        return redirect('/admin-portal/error-logs/')

    log_entry = get_object_or_404(ErrorLog, pk=log_id)
    log_entry.is_resolved = not log_entry.is_resolved
    log_entry.save(update_fields=['is_resolved'])
    status_str = "Resolved" if log_entry.is_resolved else "Marked Unresolved"
    messages.success(request, f"Error #{log_entry.id} {status_str}.")
    return redirect('/admin-portal/error-logs/')


@admin_required
def admin_error_log_clear_view(request):
    """
    Clear resolved or all error logs
    """
    if request.method == 'POST':
        action = request.POST.get('action', 'resolved')
        if action == 'all':
            deleted_count, _ = ErrorLog.objects.all().delete()
            messages.success(request, f"Purged all {deleted_count} error log entries.")
        else:
            deleted_count, _ = ErrorLog.objects.filter(is_resolved=True).delete()
            messages.success(request, f"Cleaned up {deleted_count} resolved error logs.")
    return redirect('/admin-portal/error-logs/')


@admin_required
def admin_settings_view(request):
    """
    Platform Governance & Geo-Fence Proximity Settings.
    Allows 1-click toggling and real-time adjustment of in-store proximity radius.
    """
    settings_obj = PlatformSetting.get_settings()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'toggle_geofence':
            settings_obj.geofence_enabled = not settings_obj.geofence_enabled
            settings_obj.save()
            state = "Enabled" if settings_obj.geofence_enabled else "Disabled"
            log_admin_action(
                admin_user=request.user,
                action_type="SETTINGS_UPDATE",
                target_model="PlatformSetting",
                target_id="1",
                details=f"Geo-Fence proximity verification toggled to {state}",
                request=request
            )
            messages.success(request, f"In-store Geo-Fence verification is now {state}.")
            return redirect('admin_settings')

        elif action == 'update_proximity':
            try:
                radius = int(request.POST.get('max_redemption_distance_meters', 50))
                tolerance = int(request.POST.get('gps_accuracy_tolerance_meters', 25))
                geofence_enabled = request.POST.get('geofence_enabled') == 'on'

                if radius < 5 or radius > 5000:
                    messages.error(request, "Proximity radius must be between 5m and 5,000m.")
                    return redirect('admin_settings')

                if tolerance < 0 or tolerance > 200:
                    messages.error(request, "GPS tolerance buffer must be between 0m and 200m.")
                    return redirect('admin_settings')

                settings_obj.max_redemption_distance_meters = radius
                settings_obj.gps_accuracy_tolerance_meters = tolerance
                settings_obj.geofence_enabled = geofence_enabled
                if request.POST.get('support_phone'):
                    settings_obj.support_phone = request.POST.get('support_phone').strip()
                settings_obj.save()

                log_admin_action(
                    admin_user=request.user,
                    action_type="SETTINGS_UPDATE",
                    target_model="PlatformSetting",
                    target_id="1",
                    details=f"Updated Geo-Fence proximity: Radius={radius}m, Tolerance={tolerance}m, Enabled={geofence_enabled}",
                    request=request
                )
                messages.success(request, f"Proximity parameters updated: Radius {radius}m (+{tolerance}m indoor GPS buffer).")
            except (ValueError, TypeError):
                messages.error(request, "Invalid numeric values provided.")

            return redirect('admin_settings')

        elif action == 'update_signup_bonus':
            val_str = request.POST.get('signup_bonus_enabled', '').strip().lower()
            is_enabled = val_str in ['yes', 'true', '1', 'on']
            settings_obj.signup_bonus_enabled = is_enabled
            settings_obj.save(update_fields=['signup_bonus_enabled'])

            state = "Enabled" if is_enabled else "Disabled"
            log_admin_action(
                admin_user=request.user,
                action_type="SETTINGS_UPDATE",
                target_model="PlatformSetting",
                target_id="1",
                details=f"Sign-up Welcome Bonus updated to {state} (Buyers: 25 pts, Sellers: 50 credits)",
                request=request
            )
            messages.success(request, f"Sign-up Welcome Bonus successfully updated: {state}.")
            return redirect('admin_settings')

    # Audit & Security stats
    geofence_violations_count = AdminAction.objects.filter(action_type="SECURITY_GEOFENCE_VIOLATION").count()
    recent_violations = AdminAction.objects.filter(action_type="SECURITY_GEOFENCE_VIOLATION").order_by('-created_at')[:10]
    total_geofence_redemptions = OfferRedemption.objects.filter(is_geofence_verified=True).count()
    total_redemptions = OfferRedemption.objects.count()

    context = {
        'settings': settings_obj,
        'effective_radius': settings_obj.max_redemption_distance_meters + settings_obj.gps_accuracy_tolerance_meters,
        'geofence_violations_count': geofence_violations_count,
        'recent_violations': recent_violations,
        'total_geofence_redemptions': total_geofence_redemptions,
        'total_redemptions': total_redemptions,
    }
    return render(request, 'admin/settings.html', context)


@admin_required
def admin_rewards_catalog_view(request):
    """
    Dynamic Admin Reward Burn Catalog:
    Admin management interface allowing Root Admin to create, edit, and toggle rewards dynamically.
    """
    catalog_items = RewardCatalogItem.objects.annotate(
        claims_count=Count('claims')
    ).order_by('points_required', 'title')

    total_claims = BuyerRewardClaim.objects.count()
    total_points_burned = BuyerRewardClaim.objects.aggregate(total=Sum('points_spent'))['total'] or 0
    active_rewards_count = catalog_items.filter(is_active=True).count()
    recent_claims = BuyerRewardClaim.objects.select_related('buyer', 'catalog_item').order_by('-claimed_at')[:8]

    context = {
        'catalog_items': catalog_items,
        'total_claims': total_claims,
        'total_points_burned': total_points_burned,
        'active_rewards_count': active_rewards_count,
        'recent_claims': recent_claims,
        'reward_types': CatalogRewardType.choices,
    }
    return render(request, 'admin/rewards_catalog.html', context)


@admin_required
def admin_rewards_catalog_create_view(request):
    """
    Dynamically creates a new RewardCatalogItem from Admin console.
    Safely validates all numeric inputs to prevent unhandled ValueError 500 crashes (BUG-61).
    """
    if request.method == 'POST':
        from decimal import Decimal, InvalidOperation
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        reward_type = request.POST.get('reward_type', CatalogRewardType.DISCOUNT_VOUCHER)
        prefix = request.POST.get('voucher_code_prefix', 'VOUCHER').strip().upper()
        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == 'true'

        if not title:
            messages.error(request, "Reward title is required.")
            return redirect('admin_rewards_catalog')

        try:
            points_required = int(request.POST.get('points_required', 50))
            if points_required <= 0:
                messages.error(request, "Points required must be at least 1 point.")
                return redirect('admin_rewards_catalog')
        except (ValueError, TypeError):
            messages.error(request, "Invalid numeric value for points required.")
            return redirect('admin_rewards_catalog')

        try:
            discount_inr = Decimal(request.POST.get('discount_inr', '0.00') or '0.00')
            if discount_inr < Decimal('0.00'):
                messages.error(request, "Monetary discount amount cannot be negative.")
                return redirect('admin_rewards_catalog')
        except (InvalidOperation, TypeError):
            messages.error(request, "Invalid numeric format for discount amount in INR.")
            return redirect('admin_rewards_catalog')

        try:
            stock = int(request.POST.get('stock', -1))
        except (ValueError, TypeError):
            messages.error(request, "Invalid numeric value for stock units.")
            return redirect('admin_rewards_catalog')

        item = RewardCatalogItem.objects.create(
            title=title,
            points_required=points_required,
            description=description,
            reward_type=reward_type,
            voucher_code_prefix=prefix or 'VOUCHER',
            discount_inr=discount_inr,
            stock=stock,
            is_active=is_active
        )

        log_admin_action(
            admin_user=request.user,
            action_type="CREATE_REWARD_CATALOG_ITEM",
            target_model="RewardCatalogItem",
            target_id=str(item.id),
            details=f"Created reward '{item.title}' costing {item.points_required} Pts",
            request=request
        )
        messages.success(request, f"Reward '{item.title}' added to catalog successfully!")

    return redirect('admin_rewards_catalog')


@admin_required
def admin_rewards_catalog_toggle_view(request, item_id):
    """
    Toggles is_active state of a RewardCatalogItem.
    """
    if request.method == 'POST':
        item = get_object_or_404(RewardCatalogItem, pk=item_id)
        item.is_active = not item.is_active
        item.save()

        status_str = "activated" if item.is_active else "deactivated"
        log_admin_action(
            admin_user=request.user,
            action_type="TOGGLE_REWARD_CATALOG_ITEM",
            target_model="RewardCatalogItem",
            target_id=str(item.id),
            details=f"{status_str.capitalize()} catalog reward '{item.title}'",
            request=request
        )
        messages.success(request, f"Reward '{item.title}' has been {status_str}.")

    return redirect('admin_rewards_catalog')


@admin_required
def admin_bumper_bills_view(request):
    """
    Platform-Wide Bumper Purchase Review Dashboard:
    Inspect and clear ₹10,000+ purchase bills (Step 2 of Enterprise Two-Tier Maker-Checker Approval).
    Focuses on STORE_VERIFIED submissions confirmed by store merchants.
    """
    from apps.rewards.models import BumperBillStatus

    submissions = BumperBillSubmission.objects.select_related(
        'buyer', 'shop', 'reviewed_by', 'store_verified_by', 'admin_reviewed_by', 'buyer__buyer_profile'
    ).order_by('-submitted_at')

    status_filter = request.GET.get('status', '').strip().upper()
    query = request.GET.get('q', '').strip()

    if status_filter in ['STORE_VERIFIED', 'PENDING_STORE', 'APPROVED', 'REJECTED']:
        submissions = submissions.filter(status=status_filter)
    elif status_filter == 'PENDING':
        submissions = submissions.filter(status__in=[BumperBillStatus.PENDING_STORE, 'PENDING'])

    # Free-text search across bill number, buyer, shop, and voucher code (BUG-115)
    if query:
        submissions = submissions.filter(
            Q(bill_number__icontains=query) |
            Q(buyer__username__icontains=query) |
            Q(buyer__email__icontains=query) |
            Q(buyer__buyer_profile__public_unique_id__icontains=query) |
            Q(buyer__buyer_profile__nickname__icontains=query) |
            Q(shop__name__icontains=query) |
            Q(voucher_code__icontains=query)
        )

    total_submissions = BumperBillSubmission.objects.count()
    pending_store_submissions = BumperBillSubmission.objects.filter(
        status__in=[BumperBillStatus.PENDING_STORE, 'PENDING']
    ).count()
    store_verified_submissions = BumperBillSubmission.objects.filter(
        status=BumperBillStatus.STORE_VERIFIED
    ).count()
    approved_submissions = BumperBillSubmission.objects.filter(
        status=BumperBillStatus.APPROVED
    ).count()
    rejected_submissions = BumperBillSubmission.objects.filter(
        status=BumperBillStatus.REJECTED
    ).count()
    total_bumper_points = BumperBillSubmission.objects.filter(
        status=BumperBillStatus.APPROVED
    ).aggregate(
        total=Sum('points_awarded')
    )['total'] or 0

    context = {
        'submissions': submissions[:100],
        'total_submissions': total_submissions,
        'pending_store_submissions': pending_store_submissions,
        'store_verified_submissions': store_verified_submissions,
        'approved_submissions': approved_submissions,
        'rejected_submissions': rejected_submissions,
        'total_bumper_points': total_bumper_points,
        'status_filter': status_filter,
        'query': query,
    }
    return render(request, 'admin/bumper_bills.html', context)


@admin_required
def admin_bumper_bill_approve_view(request, submission_id):
    """
    Step 2: Admin grants platform clearance for a store-verified ₹10,000+ purchase bill.
    Transitions status to APPROVED, credits 100 points, and mints VIP voucher.
    """
    if request.method == 'POST':
        from services.reward_service import admin_clear_bumper_bill

        success, msg, sub = admin_clear_bumper_bill(submission_id, request.user, approve=True)
        if success:
            log_admin_action(
                admin_user=request.user,
                action_type="APPROVE_BUMPER_BILL",
                target_model="BumperBillSubmission",
                target_id=str(submission_id),
                details=f"Approved Bumper Bill #{sub.bill_number} (₹{sub.bill_amount}) for {sub.buyer.username}. Credited 100 Pts and VIP voucher {sub.voucher_code}.",
                request=request
            )
            messages.success(request, msg)
        else:
            messages.error(request, msg)

    return redirect('admin_bumper_bills')


@admin_required
def admin_bumper_bill_reject_view(request, submission_id):
    """
    Step 2: Admin rejects a purchase bill during platform clearance.
    """
    if request.method == 'POST':
        from services.reward_service import admin_clear_bumper_bill

        reason = request.POST.get('rejection_reason', '').strip()
        success, msg, sub = admin_clear_bumper_bill(submission_id, request.user, approve=False, rejection_reason=reason)
        if success:
            log_admin_action(
                admin_user=request.user,
                action_type="REJECT_BUMPER_BILL",
                target_model="BumperBillSubmission",
                target_id=str(submission_id),
                details=f"Declined Bumper Bill #{sub.bill_number} for {sub.buyer.username}. Reason: {reason}",
                request=request
            )
            messages.success(request, msg)
        else:
            messages.error(request, msg)

    return redirect('admin_bumper_bills')


@admin_required
def admin_leaderboard_view(request):
    """
    Admin Management of Weekly Shopper Leaderboard, Cycle Rollover, and History.
    """
    from services.leaderboard_service import get_weekly_leaderboard, get_current_cycle_number
    from apps.rewards.models import LeaderboardHistory

    current_cycle = get_current_cycle_number()
    data = get_weekly_leaderboard(cycle_days=7)
    past_cycles = LeaderboardHistory.objects.select_related('winner', 'winner__buyer_profile').order_by('-cycle_number', 'rank')[:30]

    context = {
        'current_cycle': current_cycle,
        'cycle_start': data.get('cycle_start'),
        'cycle_end': data.get('cycle_end'),
        'leaderboard': data.get('leaderboard', []),
        'total_participants': data.get('total_participants', 0),
        'past_cycles': past_cycles,
    }
    return render(request, 'admin/leaderboard.html', context)


@admin_required
def admin_leaderboard_rollover_view(request):
    """
    Admin Triggered Manual Weekly Leaderboard Cycle Rollover.
    """
    if request.method != 'POST':
        messages.error(request, "POST request required to rollover leaderboard cycle.")
        return redirect('admin_leaderboard')

    from services.leaderboard_service import rollover_weekly_leaderboard_cycle

    # Admin manual action forces rollover if explicitly submitted
    success, msg, history = rollover_weekly_leaderboard_cycle(force=True)
    if success:
        log_admin_action(
            admin_user=request.user,
            action_type="ROLLOVER_LEADERBOARD_CYCLE",
            target_model="LeaderboardHistory",
            target_id=str(history.id) if history else "0",
            details=f"Admin initiated manual leaderboard cycle rollover: {msg}",
            request=request
        )
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect('admin_leaderboard')


@admin_required
def admin_categories_view(request):
    """
    Platform Admin Dynamic Category & Subcategory Hierarchy Management.
    Enables adding, editing, and disabling categories/subcategories without touching code.
    """
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_category':
            name = request.POST.get('name', '').strip()
            icon = request.POST.get('icon', 'bi-tag').strip() or 'bi-tag'
            requires_fssai = request.POST.get('requires_fssai') == 'on'
            requires_fda = request.POST.get('requires_fda') == 'on'

            if name:
                cat = ShopCategory.objects.create(
                    name=name,
                    icon=icon,
                    requires_fssai=requires_fssai,
                    requires_fda=requires_fda,
                    is_active=True
                )
                log_admin_action(
                    admin_user=request.user,
                    action_type="CREATE_CATEGORY",
                    target_model="ShopCategory",
                    target_id=str(cat.id),
                    details=f"Created retail category '{name}' (FSSAI={requires_fssai}, FDA={requires_fda}).",
                    request=request
                )
                messages.success(request, f"Category '{name}' created successfully.")
            else:
                messages.error(request, "Category name is required.")

        elif action == 'create_subcategory':
            cat_id = request.POST.get('category_id')
            sub_name = request.POST.get('name', '').strip()
            cat = get_object_or_404(ShopCategory, id=cat_id)

            if sub_name:
                sub = ShopSubcategory.objects.create(
                    category=cat,
                    name=sub_name,
                    is_active=True
                )
                log_admin_action(
                    admin_user=request.user,
                    action_type="CREATE_SUBCATEGORY",
                    target_model="ShopSubcategory",
                    target_id=str(sub.id),
                    details=f"Added subcategory '{sub_name}' under category '{cat.name}'.",
                    request=request
                )
                messages.success(request, f"Subcategory '{sub_name}' added under '{cat.name}'.")
            else:
                messages.error(request, "Subcategory name is required.")

        return redirect('/admin-portal/categories/')

    categories = ShopCategory.objects.prefetch_related('shop_subcategories').annotate(
        shops_count=Count('shops')
    ).order_by('sort_order', 'name')

    total_categories = categories.count()
    total_subcategories = ShopSubcategory.objects.count()

    context = {
        'categories': categories,
        'total_categories': total_categories,
        'total_subcategories': total_subcategories,
    }
    return render(request, 'admin/categories.html', context)


@admin_required
def admin_category_toggle_view(request, category_id):
    """
    Toggle a category's active/inactive status.
    Supports secure POST with CSRF validation while preserving backward compatibility for GET.
    """
    if request.method != 'POST':
        messages.error(request, "Invalid request method. POST with CSRF token is required.")
        return redirect('/admin-portal/categories/')

    cat = get_object_or_404(ShopCategory, id=category_id)
    cat.is_active = not cat.is_active
    cat.save()

    status_str = "Enabled" if cat.is_active else "Disabled"
    log_admin_action(
        admin_user=request.user,
        action_type="TOGGLE_CATEGORY",
        target_model="ShopCategory",
        target_id=str(cat.id),
        details=f"Admin set category '{cat.name}' to {status_str}.",
        request=request
    )
    messages.success(request, f"Category '{cat.name}' {status_str}.")
    return redirect('/admin-portal/categories/')


@admin_required
def admin_subcategory_toggle_view(request, subcategory_id):
    """
    Toggle a subcategory's active/inactive status.
    Supports secure POST with CSRF validation while preserving backward compatibility for GET.
    """
    if request.method != 'POST':
        messages.error(request, "Invalid request method. POST with CSRF token is required.")
        return redirect('/admin-portal/categories/')

    sub = get_object_or_404(ShopSubcategory, id=subcategory_id)
    sub.is_active = not sub.is_active
    sub.save()

    status_str = "Enabled" if sub.is_active else "Disabled"
    log_admin_action(
        admin_user=request.user,
        action_type="TOGGLE_SUBCATEGORY",
        target_model="ShopSubcategory",
        target_id=str(sub.id),
        details=f"Admin set subcategory '{sub.name}' to {status_str}.",
        request=request
    )
    messages.success(request, f"Subcategory '{sub.name}' {status_str}.")
    return redirect('/admin-portal/categories/')


@admin_required
def admin_verifications_view(request):
    """
    Merchant Statutory Compliance & Document Review Dashboard.
    Review Gumasta/Shop Act, FSSAI, FDA, GSTIN, PAN and moderate store status.
    """
    status_filter = request.GET.get('status', 'ALL').upper()
    query = request.GET.get('q', '').strip()

    verifications = ShopVerification.objects.select_related(
        'shop', 'shop__seller', 'shop__category', 'shop__subcategory', 'verified_by'
    )

    if status_filter in ['PENDING', 'APPROVED', 'REJECTED']:
        verifications = verifications.filter(status=status_filter)

    if query:
        verifications = verifications.filter(
            Q(shop__name__icontains=query) |
            Q(business_pan__icontains=query) |
            Q(gst_number__icontains=query) |
            Q(shop_act_license_number__icontains=query) |
            Q(fssai_license_number__icontains=query) |
            Q(fda_license_number__icontains=query)
        )

    verifications = verifications.order_by('-created_at')

    pending_count = ShopVerification.objects.filter(status='PENDING').count()
    approved_count = ShopVerification.objects.filter(status='APPROVED').count()
    rejected_count = ShopVerification.objects.filter(status='REJECTED').count()

    context = {
        'verifications': verifications[:100],
        'status_filter': status_filter,
        'query': query,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
    }
    return render(request, 'admin/verifications.html', context)


@admin_required
def admin_verification_update_view(request, verification_id):
    """
    Admin updates a merchant's statutory document verification status (Approve / Reject).
    When approving:
    - ShopVerification.status = 'APPROVED'
    - Shop.is_approved = True & Shop.is_active = True
    - SellerProfile.is_verified = True & SellerProfile.verified_at = timezone.now()
    - Assign unique city-coded merchant_id if missing
    - Grant 50 starter promotional credits (Bug 8.5 / Bug 9.6 policy limit)
    - Send in-app notification & alert:
      "🎉 Congratulations! Your store [Business Name] has been verified and approved. Your official Merchant ID is [PUN-XXXXXX]. All selling features are now active!"
    """
    verification = get_object_or_404(ShopVerification, id=verification_id)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        notes = request.POST.get('admin_notes', '').strip()

        if new_status in ['APPROVED', 'REJECTED', 'PENDING']:
            verification.status = new_status
            verification.admin_notes = notes
            verification.verified_by = request.user
            verification.verified_at = timezone.now()
            verification.save()

            # Sync shop approval status & symmetrical delisting
            shop = verification.shop
            if new_status == 'APPROVED':
                shop.is_approved = True
                shop.is_active = True
            elif new_status == 'REJECTED':
                shop.is_approved = False
                shop.is_active = False
                Offer.objects.filter(shop=shop, status=OfferStatus.ACTIVE).update(status=OfferStatus.PAUSED)
            else:
                shop.is_approved = False
                shop.is_active = False
                Offer.objects.filter(shop=shop, status=OfferStatus.ACTIVE).update(status=OfferStatus.PAUSED)
            shop.save(update_fields=['is_approved', 'is_active'])

            seller_user = shop.seller
            seller_profile = getattr(seller_user, 'seller_profile', None)

            if new_status == 'APPROVED':
                if seller_profile:
                    seller_profile.is_verified = True
                    seller_profile.verified_at = timezone.now()
                    if not seller_profile.merchant_id or seller_profile.merchant_id.startswith('MER-'):
                        city = shop.city or getattr(seller_user.profile, 'city', 'Pune')
                        seller_profile.merchant_id = generate_unique_merchant_id(city)
                    seller_profile.save(update_fields=['is_verified', 'verified_at', 'merchant_id'])

                # Grant starter promotional credits (strictly capped to policy limit: 50 credits)
                get_or_create_wallet(seller_user)

                # Send in-app notification & alert
                merchant_id_display = seller_profile.merchant_id if seller_profile else "Active"
                store_name_display = seller_profile.business_name if seller_profile else shop.name
                create_notification(
                    recipient=seller_user,
                    title="🎉 Store Approved & Verified!",
                    message=f"🎉 Congratulations! Your store {store_name_display} has been verified and approved. Your official Merchant ID is {merchant_id_display}. All selling features are now active!",
                    link_url="/seller/dashboard/"
                )
            elif new_status in ['REJECTED', 'PENDING']:
                if seller_profile:
                    has_other_approved_shops = Shop.objects.filter(
                        seller=seller_user,
                        is_approved=True
                    ).exclude(pk=shop.pk).exists()
                    if not has_other_approved_shops:
                        seller_profile.is_verified = False
                        seller_profile.save(update_fields=['is_verified'])

            log_admin_action(
                admin_user=request.user,
                action_type=f"VERIFY_SHOP_{new_status}",
                target_model="ShopVerification",
                target_id=str(verification.id),
                details=f"Admin set compliance status of shop '{shop.name}' to {new_status}. Notes: {notes}",
                request=request
            )
            messages.success(request, f"Compliance status for '{shop.name}' updated to {new_status}.")

    return redirect('/admin-portal/verifications/')


@admin_required
def admin_agreement_logs_view(request):
    """
    Platform Compliance: Merchant Digital Agreement Acceptance Audit Trail
    """
    query = request.GET.get('q', '').strip()
    logs = MerchantAgreementLog.objects.select_related('user').order_by('-accepted_at')

    if query:
        logs = logs.filter(
            Q(merchant_id__icontains=query) |
            Q(business_name__icontains=query) |
            Q(user__username__icontains=query) |
            Q(ip_address__icontains=query)
        )

    context = {
        'logs': logs[:150],
        'query': query,
        'total_count': logs.count(),
    }
    return render(request, 'admin/agreement_logs.html', context)


def custom_404_view(request, exception=None):
    """
    Enterprise custom 404 handler returning HTTP status 404.
    Strictly isolated: ZERO internal file paths or debug traces.
    """
    if not hasattr(request, 'user'):
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
    is_api = (
        request.path.startswith('/api/') or
        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
        'application/json' in request.headers.get('accept', '').lower()
    )
    if is_api:
        return JsonResponse({
            'error': 'Not Found',
            'message': 'The requested endpoint or resource was not found.'
        }, status=404)

    return render(request, '404.html', status=404)


def custom_500_view(request):
    """
    Enterprise custom 500 handler returning HTTP status 500 with unique Incident Reference ID.
    Strictly isolated: ZERO internal stack traces, database schema, or raw inputs.
    """
    if not hasattr(request, 'user'):
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
    incident_id = getattr(request, 'incident_id', None)
    if not incident_id:
        from services.security_logger import generate_incident_id
        incident_id = generate_incident_id()
        request.incident_id = incident_id

    is_api = (
        request.path.startswith('/api/') or
        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
        'application/json' in request.headers.get('accept', '').lower()
    )
    if is_api:
        return JsonResponse({
            'error': 'Internal Server Error',
            'message': 'Something went wrong on our end. Please try again shortly.',
            'incident_id': incident_id
        }, status=500)

    return render(request, '500.html', {'incident_id': incident_id}, status=500)
