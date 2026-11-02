import os
import re
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from apps.public.forms import ContactInquiryForm
from apps.administration.models import ContactInquiry, InquiryRole, SupportTicket, TicketCategory, TicketStatus
from services.captcha_service import generate_captcha

from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q, F
from apps.shops.models import Shop, ShopCategory
from apps.offers.models import Offer, OfferStatus
from apps.wallets.models import CreditPackage
from services.location_service import (
    annotate_shops_with_distance, calculate_distance, format_distance_display, safe_parse_coordinates
)


def landing_page_view(request):
    """
    Public Homepage / Hero Landing with Smart Auth Routing & Session Memory:
    1. Authenticated users are automatically restored to their last visited screen
       or directed to their respective role dashboard (Buyer, Seller, Admin).
    2. Public landing page is rendered exclusively for anonymous / logged-out visitors.
    """
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        is_buyer = bool(profile and profile.is_buyer)
        is_seller = bool(profile and profile.is_seller)
        is_admin = bool((profile and profile.is_admin) or request.user.is_staff or request.user.is_superuser)

        last_url = None
        if hasattr(request, 'session'):
            last_url = request.session.get('last_visited_url')
        if not last_url:
            last_url = request.COOKIES.get('last_visited_url')

        if is_buyer:
            if last_url and (last_url.startswith('/buyer/') or last_url.startswith('/explore/')) and 'logout' not in last_url and 'login' not in last_url:
                return redirect(last_url)
            return redirect('/buyer/dashboard/')

        elif is_seller:
            if last_url and last_url.startswith('/seller/') and 'logout' not in last_url and 'login' not in last_url:
                return redirect(last_url)
            return redirect('/seller/dashboard/')

        elif is_admin:
            if last_url and last_url.startswith('/admin-portal/') and 'logout' not in last_url and 'login' not in last_url:
                return redirect(last_url)
            return redirect('/admin-portal/dashboard/')

        return redirect('/buyer/dashboard/')
    lat = request.GET.get('lat')
    lng = request.GET.get('lng')

    categories = ShopCategory.objects.filter(is_active=True, parent__isnull=True)[:8]
    featured_shops_qs = (
        Shop.objects.filter(is_approved=True, is_active=True)
        .select_related('category')
        .order_by('-is_spotlight', '-is_featured', '-rating', '-created_at')
    )
    
    lat_val, lng_val = safe_parse_coordinates(lat, lng)
    if lat_val is not None and lng_val is not None:
        try:
            featured_shops = annotate_shops_with_distance(featured_shops_qs, lat_val, lng_val)[:6]
        except Exception:
            featured_shops = featured_shops_qs[:6]
    else:
        featured_shops = featured_shops_qs[:6]

    now = timezone.now()
    featured_offers = Offer.objects.filter(
        status=OfferStatus.ACTIVE,
        is_featured=True,
        shop__is_active=True,
        shop__is_approved=True,
        start_date__lte=now,
        end_date__gte=now,
        current_redemptions__lt=F('max_redemptions')
    ).select_related('shop', 'shop__category')[:6]

    # If no featured offers yet, show active offers
    if not featured_offers.exists():
        featured_offers = Offer.objects.filter(
            status=OfferStatus.ACTIVE,
            shop__is_active=True,
            shop__is_approved=True,
            start_date__lte=now,
            end_date__gte=now,
            current_redemptions__lt=F('max_redemptions')
        ).select_related('shop', 'shop__category')[:6]

    context = {
        'categories': categories,
        'featured_shops': featured_shops,
        'featured_offers': featured_offers,
        'user_lat': lat,
        'user_lng': lng,
        'stats': {
            'total_shops': Shop.objects.filter(is_approved=True, is_active=True).count() or 12,
            'total_offers': Offer.objects.filter(
                status=OfferStatus.ACTIVE,
                shop__is_active=True,
                shop__is_approved=True,
                start_date__lte=now,
                end_date__gte=now,
                current_redemptions__lt=F('max_redemptions')
            ).count() or 24,
            'cities_count': 3,
        }
    }
    return render(request, 'public/index.html', context)


def explore_view(request):
    """
    Search & Exploration Directory for Shops & Offers with Real-time Distance Sorting
    """
    query = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', '').strip()
    city = request.GET.get('city', 'Bengaluru').strip()
    view_type = request.GET.get('type', 'shops')  # 'shops' or 'offers'
    lat = request.GET.get('lat')
    lng = request.GET.get('lng')

    categories = ShopCategory.objects.filter(is_active=True)
    
    # 1. Query Shops
    shops = Shop.objects.filter(is_approved=True, is_active=True).select_related('category')
    # If user searches by live geolocation, do not artificially restrict to single city unless specified
    lat_val, lng_val = safe_parse_coordinates(lat, lng)
    if city and city != 'all' and not (lat_val is not None and lng_val is not None and request.GET.get('city') is None):
        shops = shops.filter(city__iexact=city)
    if category_slug:
        shops = shops.filter(Q(category__slug=category_slug) | Q(subcategory__slug=category_slug) | Q(category__parent__slug=category_slug))
    if query:
        shops = shops.filter(
            Q(name__icontains=query) |
            Q(locality__icontains=query) |
            Q(tagline__icontains=query) |
            Q(description__icontains=query)
        )

    # Geo distance sorting if lat/lng available
    if lat_val is not None and lng_val is not None:
        try:
            shops_list = annotate_shops_with_distance(shops, lat_val, lng_val)
        except Exception:
            shops_list = list(shops)
    else:
        shops_list = list(shops)

    # 2. Query Offers
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
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(shop__name__icontains=query) |
            Q(shop__locality__icontains=query)
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
    return render(request, 'public/explore.html', context)


def record_shop_view(shop):
    """
    Atomically increments shop view count to eliminate lost-update race conditions (BUG-63),
    and synchronizes daily views into ShopMetric for merchant telemetry (BUG-64).
    """
    from apps.analytics.models import ShopMetric
    Shop.objects.filter(pk=shop.pk).update(views_count=F('views_count') + 1)
    shop.refresh_from_db(fields=['views_count'])

    today = timezone.localdate()
    updated = ShopMetric.objects.filter(shop=shop, date=today).update(views_count=F('views_count') + 1)
    if updated == 0:
        ShopMetric.objects.get_or_create(shop=shop, date=today, defaults={'views_count': 1})


def shop_detail_view(request, slug):
    """
    Public / Buyer Shop Page with live distance calculation
    """
    shop = get_object_or_404(Shop.objects.select_related('category', 'seller'), slug=slug, is_approved=True, is_active=True)
    record_shop_view(shop)

    lat = request.GET.get('lat')
    lng = request.GET.get('lng')
    user_distance_display = ""
    user_distance_km = None
    lat_val, lng_val = safe_parse_coordinates(lat, lng)
    if lat_val is not None and lng_val is not None:
        try:
            user_distance_km = calculate_distance(lat_val, lng_val, float(shop.latitude), float(shop.longitude))
            user_distance_display = format_distance_display(user_distance_km)
            shop.distance_display = user_distance_display
            shop.distance_km = user_distance_km
        except Exception:
            pass

    active_offers = shop.offers.filter(
        status=OfferStatus.ACTIVE,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now(),
        current_redemptions__lt=F('max_redemptions')
    )
    gallery_images = shop.gallery_images.all()

    # Calculate map bounding box for OpenStreetMap embed
    map_bbox = ""
    try:
        s_lat = float(shop.latitude)
        s_lng = float(shop.longitude)
        map_bbox = f"{s_lng - 0.008:.6f}%2C{s_lat - 0.008:.6f}%2C{s_lng + 0.008:.6f}%2C{s_lat + 0.008:.6f}"
    except (TypeError, ValueError, AttributeError):
        pass

    context = {
        'shop': shop,
        'active_offers': active_offers,
        'gallery_images': gallery_images,
        'user_lat': lat,
        'user_lng': lng,
        'user_distance_display': user_distance_display,
        'user_distance_km': user_distance_km,
        'map_bbox': map_bbox,
    }
    # If viewed by authenticated buyer, use buyer wrapper layout
    if request.user.is_authenticated and getattr(request.user, 'profile', None) and request.user.profile.is_buyer:
        return render(request, 'buyer/shop_detail.html', context)

    return render(request, 'public/shop_detail.html', context)


def offer_detail_view(request, slug):
    """
    Offer Detailed Showcase
    """
    offer = get_object_or_404(
        Offer.objects.select_related('shop', 'shop__category'),
        slug=slug,
        shop__is_active=True,
        shop__is_approved=True,
        status=OfferStatus.ACTIVE,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now()
    )
    related_offers = Offer.objects.filter(
        shop__category=offer.shop.category,
        shop__is_active=True,
        shop__is_approved=True,
        status=OfferStatus.ACTIVE,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now()
    ).exclude(pk=offer.pk)[:3]

    is_quota_exhausted = (offer.current_redemptions >= offer.max_redemptions)
    context = {
        'offer': offer,
        'shop': offer.shop,
        'related_offers': related_offers,
        'is_quota_exhausted': is_quota_exhausted,
    }
    if request.user.is_authenticated and getattr(request.user, 'profile', None) and request.user.profile.is_buyer:
        return render(request, 'buyer/offer_detail.html', context)

    return render(request, 'public/offer_detail.html', context)


def for_sellers_view(request):
    """
    Merchant Landing / Value Proposition / Packages
    """
    packages = CreditPackage.objects.filter(is_active=True)
    return render(request, 'public/for_sellers.html', {'packages': packages})


from django.http import HttpResponse


def robots_txt_view(request):
    """
    Standard crawler instructions pointing to sitemap.xml
    """
    sitemap_url = request.build_absolute_uri('/sitemap.xml')
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /admin-portal/
Disallow: /seller/
Disallow: /buyer/scan/

Sitemap: {sitemap_url}
"""
    return HttpResponse(content.strip(), content_type="text/plain; charset=utf-8")


def sitemap_xml_view(request):
    """
    Generates dynamic XML sitemap with all public pages, shops, and active offers
    """
    host = request.build_absolute_uri('/')[:-1]
    shops = Shop.objects.filter(is_approved=True, is_active=True).values_list('slug', flat=True)
    offers = Offer.objects.filter(
        status=OfferStatus.ACTIVE,
        shop__is_active=True,
        shop__is_approved=True,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now(),
        current_redemptions__lt=F('max_redemptions')
    ).values_list('slug', flat=True)

    urls = [
        f"{host}/",
        f"{host}/explore/",
        f"{host}/explore/?type=offers",
        f"{host}/for-sellers/",
        f"{host}/privacy-policy/",
        f"{host}/buyer/login/",
        f"{host}/buyer/register/",
        f"{host}/seller/login/",
        f"{host}/seller/register/",
    ]

    for s_slug in shops:
        urls.append(f"{host}/shops/{s_slug}/")

    for o_slug in offers:
        urls.append(f"{host}/offers/{o_slug}/")

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        xml_lines.append(f'  <url><loc>{u}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>')
    xml_lines.append('</urlset>')

    return HttpResponse('\n'.join(xml_lines), content_type="application/xml; charset=utf-8")


def terms_view(request):
    """
    Official Terms & Conditions (GENNIA / Trisha Infotech Solution)
    """
    return render(request, 'public/terms.html')


def refund_policy_view(request):
    """
    Official Refund Policy (GENNIA / Trisha Infotech Solution)
    """
    return render(request, 'public/refund_policy.html')


def privacy_policy_view(request):
    """
    Official Privacy Policy (GENNIA / Trisha Infotech Solution - DPDPA 2023)
    """
    return render(request, 'public/privacy_policy.html')


def cookie_policy_view(request):
    """
    Official Cookie Policy (GENNIA / Trisha Infotech Solution)
    """
    return render(request, 'public/cookie_policy.html')


def thank_you_view(request):
    """
    Post-registration confirmation and onboarding thank-you page with GA4 conversion event
    """
    role = request.GET.get('role', 'shopper')
    just_registered_shopper = request.session.pop('just_registered_shopper', None)
    return render(request, 'public/thank_you.html', {
        'role': role,
        'shopper_pass': just_registered_shopper
    })


def custom_404_view(request, exception=None):
    """
    Custom branded 404 error page returning real HTTP status 404
    """
    return render(request, '404.html', status=404)


def offline_view(request):
    """
    Offline fallback page served by Service Worker when network connection is unavailable
    """
    return render(request, 'offline.html')


import uuid
from decimal import Decimal
from django.contrib import messages
from apps.redemptions.models import QRCode
from services.redemption_service import process_redemption, create_or_refresh_offer_qr


def shop_qr_landing_view(request, slug):
    """
    Universal Merchant Counter QR Landing Page (Camera & Google Lens Friendly)
    Enables any customer scanning the physical standee QR to see active discounts,
    view shop info, and 1-click claim counter vouchers.
    """
    shop = get_object_or_404(Shop.objects.select_related('category', 'seller'), slug=slug, is_approved=True, is_active=True)
    record_shop_view(shop)

    active_offers = list(shop.offers.filter(
        status=OfferStatus.ACTIVE,
        start_date__lte=timezone.now(),
        end_date__gte=timezone.now(),
        current_redemptions__lt=F('max_redemptions')
    ))

    # Check for direct claim action
    claim_offer_id = request.POST.get('claim_offer_id') or request.GET.get('claim_offer')
    redemption_voucher = None

    if request.method == 'POST' and claim_offer_id:
        if not request.user.is_authenticated:
            # Save intended offer in session and redirect to instant anonymous registration
            request.session['pending_claim_offer'] = claim_offer_id
            request.session['pending_claim_shop'] = slug
            messages.info(request, "Create your free anonymous Shopper Pass to claim this counter discount!")
            return redirect(f"/buyer/register/?next=/store/{slug}/qr/?claim_offer={claim_offer_id}")

        selected_offer = shop.offers.filter(
            pk=claim_offer_id,
            status=OfferStatus.ACTIVE,
            start_date__lte=timezone.now(),
            end_date__gte=timezone.now(),
            current_redemptions__lt=F('max_redemptions')
        ).first()
        if selected_offer:
            # Generate or fetch active QR code
            qr_obj, _ = create_or_refresh_offer_qr(selected_offer)
            idempotency_token = request.POST.get('idempotency_key', str(uuid.uuid4()))
            bill_str = request.POST.get('bill_amount', str(selected_offer.min_purchase or '0.00')).strip()
            try:
                bill_amount = Decimal(bill_str) if bill_str else Decimal('0.00')
            except Exception:
                bill_amount = Decimal('0.00')

            from services.redemption_service import initiate_redemption
            from services.location_service import safe_parse_coordinates
            from config.middleware import get_client_ip
            client_ip = get_client_ip(request)
            device_token = request.headers.get('x-device-token', '') or request.POST.get('device_token', '')

            buyer_lat_raw = request.POST.get('buyer_lat', '').strip()
            buyer_lng_raw = request.POST.get('buyer_lng', '').strip()
            buyer_acc_raw = request.POST.get('buyer_accuracy', '0').strip()
            buyer_lat, buyer_lng = safe_parse_coordinates(buyer_lat_raw, buyer_lng_raw)
            try:
                buyer_accuracy = float(buyer_acc_raw) if buyer_acc_raw else 0.0
            except Exception:
                buyer_accuracy = 0.0

            success, msg, redemption = initiate_redemption(
                buyer_user=request.user,
                offer=selected_offer,
                bill_amount=bill_amount,
                idempotency_key=idempotency_token,
                buyer_lat=buyer_lat,
                buyer_lng=buyer_lng,
                buyer_accuracy=buyer_accuracy,
                client_ip=client_ip,
                device_token=device_token
            )
            if success:
                redemption_voucher = redemption
                request.session['active_redemption_id'] = redemption.id
            else:
                messages.error(request, msg)

    # If user just arrived from post-signup redirect with pending claim offer
    auto_claim_offer = None
    if claim_offer_id and not redemption_voucher:
        auto_claim_offer = shop.offers.filter(
            pk=claim_offer_id,
            status=OfferStatus.ACTIVE,
            start_date__lte=timezone.now(),
            end_date__gte=timezone.now(),
            current_redemptions__lt=F('max_redemptions')
        ).first()

    context = {
        'shop': shop,
        'active_offers': active_offers,
        'redemption_voucher': redemption_voucher,
        'auto_claim_offer': auto_claim_offer,
        'generated_idempotency_key': str(uuid.uuid4()),
    }
    return render(request, 'public/shop_qr_landing.html', context)


def qr_token_landing_view(request, token):
    """
    Resolves a specific Offer QRCode token (e.g. /qr/<token>/) and routes
    to the shop's counter landing page with that offer pre-selected.
    Strictly validates token expiration, used status, and promotion active state.
    """
    qr_obj = QRCode.objects.select_related('offer', 'offer__shop').filter(code=token).first()
    if qr_obj and qr_obj.offer and qr_obj.offer.shop:
        shop = qr_obj.offer.shop
        if qr_obj.is_expired or qr_obj.is_used or not qr_obj.offer.is_currently_valid:
            messages.warning(request, f"This QR code for '{qr_obj.offer.title}' has expired or is no longer valid. Explore current active offers at {shop.name} below.")
            return redirect(f"/store/{shop.slug}/qr/")
        return redirect(f"/store/{shop.slug}/qr/?claim_offer={qr_obj.offer.id}")

    messages.error(request, "This QR code is invalid or has expired. Browse active stores below.")
    return redirect('/explore/')


def get_client_ip(request):
    """
    Extracts the client's real IP address from HTTP headers safely.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def contact_us_view(request):
    """
    Enterprise-grade, statutory-compliant Contact Us & Grievance Desk view.
    Handles user inquiries, merchant partner support, billing issues,
    and formal grievances under IT Rules 2021 & Consumer Protection Rules 2020.
    """
    client_ip = get_client_ip(request)
    rate_limit_key = f"contact_inquiry_rl_{client_ip}"

    if request.method == 'POST':
        # Enforce rate limit: max 5 submissions per 10 minutes (600s) per IP
        attempts = cache.get(rate_limit_key, 0)
        if attempts >= 5:
            return HttpResponse(
                "Too many inquiries submitted from this IP address. Please wait a few minutes before trying again.",
                status=429
            )

        form = ContactInquiryForm(request.POST)
        if form.is_valid():
            ticket_number = f"TKT-{uuid.uuid4().hex[:8].upper()}"
            full_name = form.cleaned_data['full_name']
            email = form.cleaned_data['email']
            phone = form.cleaned_data.get('phone', '')
            user_role = form.cleaned_data['user_role']
            subject = form.cleaned_data['subject']
            message = form.cleaned_data['message']

            inquiry = ContactInquiry.objects.create(
                ticket_number=ticket_number,
                full_name=full_name,
                email=email,
                phone=phone,
                user_role=user_role,
                subject=subject,
                message=message,
                status=TicketStatus.OPEN,
                ip_address=client_ip if client_ip else None,
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                user=request.user if request.user.is_authenticated else None
            )

            # If user is authenticated, create mirrored SupportTicket
            if request.user.is_authenticated:
                category_map = {
                    InquiryRole.BUYER: TicketCategory.REDEMPTION_ISSUE,
                    InquiryRole.MERCHANT: TicketCategory.SHOP_INQUIRY,
                    InquiryRole.BILLING: TicketCategory.BILLING,
                    InquiryRole.GRIEVANCE: TicketCategory.GENERAL,
                    InquiryRole.GENERAL: TicketCategory.GENERAL,
                }
                SupportTicket.objects.create(
                    user=request.user,
                    subject=f"[{ticket_number}] {subject}",
                    category=category_map.get(user_role, TicketCategory.GENERAL),
                    message=f"{message}\n\n[Contact: {full_name} | {email} | {phone}]",
                    status=TicketStatus.OPEN
                )

            # Increment rate limit counter
            cache.set(rate_limit_key, attempts + 1, timeout=600)

            messages.success(request, f"Your inquiry has been submitted successfully! Ticket ID: {ticket_number}")
            return redirect(f"/contact-us/?ticket={ticket_number}")
        else:
            captcha_data = generate_captcha()
    else:
        initial_data = {}
        if request.user.is_authenticated:
            initial_data['full_name'] = request.user.get_full_name() or request.user.username
            initial_data['email'] = request.user.email
            profile = getattr(request.user, 'profile', None)
            if profile:
                if profile.phone:
                    initial_data['phone'] = profile.phone
                if getattr(profile, 'role', '') == 'SELLER':
                    initial_data['user_role'] = InquiryRole.MERCHANT
        form = ContactInquiryForm(initial=initial_data)
        captcha_data = generate_captcha()

    context = {
        'form': form,
        'captcha': captcha_data,
        'ticket_query': request.GET.get('ticket', ''),
    }
    return render(request, 'public/contact_us.html', context)


def service_worker_view(request):
    """
    Serve PWA Service Worker at root scope with Service-Worker-Allowed header
    and dynamic cache versioning for zero-friction client updates.
    """
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'js', 'service-worker.js')
    content = ""
    if os.path.exists(sw_path):
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
    cache_version = getattr(settings, 'PWA_CACHE_VERSION', 'v2.3')
    content = re.sub(r"const\s+CACHE_NAME\s*=\s*['\"][^'\"]+['\"];", f"const CACHE_NAME = 'lokalloop-pwa-{cache_version}';", content)
    response = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
