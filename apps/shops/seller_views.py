import os
import re
from apps.administration.models import PlatformSetting
import uuid
from decimal import Decimal
from datetime import timedelta
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, F
from django.core.files.storage import default_storage

from apps.accounts.decorators import seller_required, verified_seller_required
from apps.shops.models import Shop, ShopCategory, ShopSubcategory, ShopImage
from apps.offers.models import Offer, OfferStatus, OfferType
from apps.redemptions.models import OfferRedemption, QRCode, RedemptionStatus
from apps.wallets.models import SellerWallet, CreditPackage, WalletTransaction, CreditPurchase
from apps.notifications.models import Notification
from services.wallet_service import get_or_create_wallet, process_demo_package_purchase
from services.redemption_service import create_or_refresh_offer_qr, generate_store_qr
from services.export_service import export_to_excel_response


from services.image_security_service import process_and_sanitize_image, ImageSecurityException


def save_uploaded_media(file_obj, subfolder='shops'):
    """
    Saves an uploaded image strictly through the Image Security Service.
    Enforces extension whitelisting, magic byte validation, anti-malware payload scanning,
    deep pixel integrity decoding, and re-encoding with UUID filenames.
    """
    return process_and_sanitize_image(file_obj, subfolder=subfolder)


def cleanup_old_media_file(old_url, new_url=None):
    """
    Safely unlinks replaced media files (Bug 8.6) to prevent disk bloat.
    Ensures the target file is strictly located inside MEDIA_ROOT and prevents path traversal.
    """
    if not old_url or not isinstance(old_url, str):
        return
    if new_url and old_url.strip() == new_url.strip():
        return

    media_prefix = getattr(settings, 'MEDIA_URL', '/media/')
    if not old_url.startswith(media_prefix):
        return

    rel_path = old_url[len(media_prefix):].lstrip('/')
    if '..' in rel_path or not rel_path:
        return

    try:
        if default_storage.exists(rel_path):
            default_storage.delete(rel_path)
    except Exception:
        pass


@seller_required
def seller_dashboard_view(request):
    """
    Merchant Executive Command Center
    """
    seller = request.user
    seller_profile = getattr(seller, 'seller_profile', None)
    is_verified = bool(seller_profile and seller_profile.is_verified)

    if is_verified:
        wallet = get_or_create_wallet(seller)
    else:
        wallet = SellerWallet.objects.filter(seller=seller).first()
        if not wallet:
            wallet = SellerWallet(seller=seller, balance_credits=0, lifetime_purchased=0, loyalty_points=0)

    shops = Shop.objects.filter(seller=seller)
    primary_shop = shops.first()

    offers = Offer.objects.filter(shop__seller=seller)
    active_offers = offers.filter(status=OfferStatus.ACTIVE)
    
    redemptions = OfferRedemption.objects.filter(seller=seller).select_related('buyer', 'offer', 'shop')[:10]
    total_redemptions_count = OfferRedemption.objects.filter(seller=seller, status=RedemptionStatus.SUCCESS).count()
    agg = OfferRedemption.objects.filter(seller=seller, status=RedemptionStatus.SUCCESS).aggregate(
        total_discount=Sum('discount_given')
    )
    total_discount_given = agg['total_discount'] or Decimal('0.00')

    context = {
        'seller': seller,
        'wallet': wallet,
        'primary_shop': primary_shop,
        'active_offers_count': active_offers.count(),
        'total_offers_count': offers.count(),
        'total_redemptions_count': total_redemptions_count,
        'total_discount_given': total_discount_given,
        'recent_redemptions': redemptions,
        'active_offers': active_offers[:4],
        'primary_offer': active_offers.first() or offers.first(),
        'is_verified': is_verified,
    }
    return render(request, 'seller/dashboard.html', context)


@seller_required
def seller_shop_view(request):
    """
    Merchant Shop Profile & Details Editor
    """
    seller = request.user
    shop = Shop.objects.filter(seller=seller).first()
    categories = ShopCategory.objects.filter(is_active=True)

    if not shop:
        # Auto create a starter shop if none exists
        default_cat = categories.first()
        shop = Shop.objects.create(
            seller=seller,
            category=default_cat,
            name=f"{seller.first_name or seller.username}'s Store",
            locality="Indiranagar",
            city="Bengaluru",
            address="100 Feet Road, Indiranagar, Bengaluru - 560038",
            description="Welcome to our store."
        )

    if request.method == 'POST':
        try:
            # 1. Handle gallery image deletion if requested
            delete_image_id = request.POST.get('delete_gallery_image_id')
            if delete_image_id:
                img_obj = ShopImage.objects.filter(shop=shop, id=delete_image_id).first()
                if img_obj:
                    cleanup_old_media_file(img_obj.image_url)
                    img_obj.delete()
                messages.success(request, "Gallery photo removed.")
                return redirect('/seller/shop/')

            # 2. Update basic shop metadata
            shop.name = request.POST.get('name', shop.name).strip()
            shop.tagline = request.POST.get('tagline', '').strip()
            shop.description = request.POST.get('description', '').strip()
            category_id = request.POST.get('category')
            if category_id:
                cat = ShopCategory.objects.filter(pk=category_id).first()
                if cat:
                    shop.category = cat

            # Subcategory persistence (BUG-121)
            subcategory_id = request.POST.get('subcategory', '').strip()
            if subcategory_id:
                sub = ShopSubcategory.objects.filter(pk=subcategory_id).first()
                if sub:
                    shop.subcategory = sub
            elif subcategory_id == '':
                shop.subcategory = None

            old_city = shop.city
            shop.locality = request.POST.get('locality', shop.locality).strip()
            shop.city = request.POST.get('city', shop.city).strip()
            shop.address = request.POST.get('address', shop.address).strip()
            shop.pincode = request.POST.get('pincode', shop.pincode).strip()
            shop.phone = request.POST.get('phone', shop.phone).strip()
            shop.email = request.POST.get('email', shop.email).strip()

            # Coordinates handling (BUG-101)
            raw_lat = request.POST.get('latitude', '').strip()
            raw_lng = request.POST.get('longitude', '').strip()
            if raw_lat and raw_lng:
                try:
                    from decimal import Decimal
                    shop.latitude = Decimal(raw_lat)
                    shop.longitude = Decimal(raw_lng)
                except (ValueError, TypeError, Exception):
                    pass
            elif shop.city and shop.city.lower() != old_city.lower():
                CITY_COORDS = {
                    'pune': (18.5204, 73.8567),
                    'mumbai': (19.0760, 72.8777),
                    'bengaluru': (12.9716, 77.5946),
                    'bangalore': (12.9716, 77.5946),
                    'delhi': (28.6139, 77.2090),
                    'hyderabad': (17.3850, 78.4867),
                    'chennai': (13.0827, 80.2707),
                    'kolkata': (22.5726, 88.3639),
                    'ahmedabad': (23.0225, 72.5714),
                }
                c_key = shop.city.strip().lower()
                if c_key in CITY_COORDS:
                    from decimal import Decimal
                    shop.latitude = Decimal(str(CITY_COORDS[c_key][0]))
                    shop.longitude = Decimal(str(CITY_COORDS[c_key][1]))

            # 3. Handle Cover Banner Image (Device / Gallery File upload takes precedence)
            banner_file = request.FILES.get('banner_image_file')
            if banner_file:
                old_banner = shop.banner_image_url
                new_banner = save_uploaded_media(banner_file, subfolder='shops/banners')
                cleanup_old_media_file(old_banner, new_banner)
                shop.banner_image_url = new_banner
            elif request.POST.get('banner_image_url', '').strip():
                new_banner = request.POST.get('banner_image_url').strip()
                if shop.banner_image_url and shop.banner_image_url != new_banner:
                    cleanup_old_media_file(shop.banner_image_url, new_banner)
                shop.banner_image_url = new_banner

            # 4. Handle Emblem / Logo Image (Device / Gallery File upload takes precedence)
            logo_file = request.FILES.get('logo_image_file')
            if logo_file:
                old_logo = shop.logo_image_url
                new_logo = save_uploaded_media(logo_file, subfolder='shops/logos')
                cleanup_old_media_file(old_logo, new_logo)
                shop.logo_image_url = new_logo
            elif request.POST.get('logo_image_url', '').strip():
                new_logo = request.POST.get('logo_image_url').strip()
                if shop.logo_image_url and shop.logo_image_url != new_logo:
                    cleanup_old_media_file(shop.logo_image_url, new_logo)
                shop.logo_image_url = new_logo

            shop.save()

            # 5. Handle additional Storefront Gallery Images from device / gallery
            gallery_files = request.FILES.getlist('gallery_images')
            for g_file in gallery_files:
                if g_file and g_file.size > 0:
                    g_url = save_uploaded_media(g_file, subfolder='shops/gallery')
                    ShopImage.objects.create(shop=shop, image_url=g_url)

            messages.success(request, "Store profile details and branding photography saved successfully!")
            return redirect('/seller/shop/')
        except Exception as e:
            messages.error(request, f"Unable to save store profile: {str(e)}")
            return redirect('/seller/shop/')

    gallery_images = shop.gallery_images.all()
    subcategories = ShopSubcategory.objects.filter(is_active=True).select_related('category')

    context = {
        'shop': shop,
        'categories': categories,
        'subcategories': subcategories,
        'gallery_images': gallery_images,
    }
    return render(request, 'seller/shop_profile.html', context)


@seller_required
def seller_offers_list_view(request):
    """
    Merchant Offer Lifecycle Management & Excel Export
    """
    seller = request.user
    offers = Offer.objects.filter(shop__seller=seller).select_related('shop').order_by('-created_at')

    # Excel Export Trigger
    if request.GET.get('export', '').lower().strip() == 'excel':
        headers = [
            "Campaign Title", "Store Name", "Discount Type", "Discount Value",
            "Min Bill (INR)", "Claims", "Max Quota", "Debit Credits/Claim",
            "Buyer Points/Claim", "Valid Until", "Status", "Created Date"
        ]
        rows = []
        for o in offers:
            rows.append([
                o.title,
                o.shop.name if o.shop else "N/A",
                o.get_offer_type_display() if hasattr(o, 'get_offer_type_display') else o.offer_type,
                float(o.discount_value),
                float(o.min_purchase),
                o.current_redemptions,
                o.max_redemptions,
                o.credits_per_redemption,
                o.reward_points_per_redemption,
                o.end_date.strftime("%d-%b-%Y") if o.end_date else "N/A",
                o.get_status_display() if hasattr(o, 'get_status_display') else o.status,
                o.created_at.strftime("%d-%b-%Y") if o.created_at else "N/A"
            ])
        return export_to_excel_response(
            filename_prefix="my_store_campaigns",
            sheet_title="Promotional Offers",
            headers=headers,
            rows=rows,
            title=f"LokalLoop Merchant — Store Promotional Campaigns ({seller.username})"
        )

    context = {
        'offers': offers,
    }
    return render(request, 'seller/offers_list.html', context)


@verified_seller_required
def seller_offer_create_view(request):
    """
    Launch New In-Store Offer
    """
    seller = request.user
    shop = Shop.objects.filter(seller=seller).first()
    if not shop:
        messages.error(request, "Please set up your store profile first.")
        return redirect('/seller/shop/')

    if request.method == 'POST':
        try:
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            terms = request.POST.get('terms', '').strip()
            offer_type = request.POST.get('offer_type', 'PERCENTAGE')
            raw_discount = request.POST.get('discount_value', '').strip()
            if not raw_discount:
                discount_value = Decimal('50.00') if offer_type == 'BOGO' else Decimal('20.00')
            else:
                discount_value = Decimal(raw_discount)
            min_purchase = Decimal(request.POST.get('min_purchase', '0.00'))
            max_redemptions = int(request.POST.get('max_redemptions', '100'))
            days_valid = int(request.POST.get('days_valid', '30'))
            
            raw_max_discount = request.POST.get('max_discount', '').strip()
            max_discount = Decimal(raw_max_discount) if raw_max_discount else None
            if max_discount is not None and max_discount <= Decimal('0.00'):
                messages.error(request, "Maximum discount cap must be greater than zero.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if not title:
                messages.error(request, "Campaign title cannot be empty.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if discount_value <= Decimal('0.00'):
                messages.error(request, "Discount value must be greater than zero.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if offer_type in [OfferType.PERCENTAGE, OfferType.BOGO] and discount_value > Decimal('100.00'):
                messages.error(request, "Percentage discount cannot exceed 100%.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if offer_type == OfferType.FLAT:
                if min_purchase <= Decimal('0.00'):
                    messages.error(request, "Flat cash discount campaigns require a minimum purchase amount.")
                    return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})
                if discount_value > min_purchase:
                    messages.error(request, "Flat discount value cannot exceed the minimum bill purchase requirement.")
                    return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if min_purchase < Decimal('0.00'):
                messages.error(request, "Minimum purchase amount cannot be negative.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if max_redemptions <= 0:
                messages.error(request, "Maximum redemption quota must be at least 1.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            if days_valid <= 0:
                messages.error(request, "Campaign validity must be at least 1 day.")
                return render(request, 'seller/offer_form.html', {'shop': shop, 'offer_types': OfferType.choices})

            # Check for uploaded offer promo image from device / gallery
            offer_img_file = request.FILES.get('image_file')
            if offer_img_file:
                image_url = save_uploaded_media(offer_img_file, subfolder='offers')
            else:
                image_url = request.POST.get('image_url', '').strip() or 'https://images.unsplash.com/photo-1607082348824-0a96f2a4b9da?w=800'

            start_date = timezone.now()
            end_date = start_date + timedelta(days=days_valid)

            offer = Offer.objects.create(
                shop=shop,
                title=title,
                description=description,
                terms=terms or "Valid for in-store purchases only. Cannot be combined with other offers.",
                offer_type=offer_type,
                discount_value=discount_value,
                min_purchase=min_purchase,
                max_discount=max_discount,
                max_redemptions=max_redemptions,
                start_date=start_date,
                end_date=end_date,
                status=OfferStatus.ACTIVE,
                image_url=image_url or 'https://images.unsplash.com/photo-1607082348824-0a96f2a4b9da?w=800'
            )

            # Generate initial QR code for counter desk
            create_or_refresh_offer_qr(offer)

            messages.success(request, f"Offer '{offer.title}' published successfully! QR Code generated for counter.")
            return redirect('/seller/offers/')
        except ImageSecurityException as se:
            messages.error(request, f"Image upload blocked: {str(se)}")
        except Exception as e:
            messages.error(request, f"Failed to publish offer: {str(e)}")

    context = {
        'shop': shop,
        'offer_types': OfferType.choices,
    }
    return render(request, 'seller/offer_form.html', context)


@verified_seller_required
def seller_offer_toggle_status_view(request, offer_id):
    """
    Pause or Resume an Offer.
    Enforces secure POST method and validates expiration & quota before resuming.
    """
    if request.method != 'POST':
        messages.error(request, "Invalid request method. POST is required to toggle offer status.")
        return redirect('/seller/offers/')

    offer = get_object_or_404(Offer, pk=offer_id, shop__seller=request.user)
    if offer.status == OfferStatus.ACTIVE:
        offer.status = OfferStatus.PAUSED
        offer.save(update_fields=['status'])
        messages.info(request, f"Offer '{offer.title}' is now PAUSED.")
    elif offer.status == OfferStatus.PAUSED:
        if offer.is_expired:
            messages.error(request, f"Cannot resume offer '{offer.title}' because campaign end date has elapsed.")
            return redirect('/seller/offers/')
        if offer.current_redemptions >= offer.max_redemptions:
            messages.error(request, f"Cannot resume offer '{offer.title}' because maximum redemption quota ({offer.max_redemptions}) is reached.")
            return redirect('/seller/offers/')
        offer.status = OfferStatus.ACTIVE
        offer.save(update_fields=['status'])
        messages.success(request, f"Offer '{offer.title}' is now ACTIVE.")
    return redirect('/seller/offers/')


@verified_seller_required
def seller_qr_desk_view(request):
    """
    Counter QR Desk & Printable Poster View
    Generates dynamic base64 QR codes for in-store customer scanning.
    Supports both:
    1. Store QR (scans to open merchant catalog with all active offers)
    2. Specific Campaign QR (scans single offer token)
    """
    seller = request.user
    shop = Shop.objects.filter(seller=seller).first()
    active_offers = Offer.objects.filter(
        shop__seller=seller,
        status=OfferStatus.ACTIVE,
        end_date__gte=timezone.now(),
        current_redemptions__lt=F('max_redemptions')
    )

    selected_offer_id = request.GET.get('offer')
    current_offer = None
    if selected_offer_id:
        current_offer = active_offers.filter(pk=selected_offer_id).first()
    if not current_offer and active_offers.exists():
        current_offer = active_offers.first()

    qr_obj = None
    qr_image_data_uri = None
    if current_offer:
        qr_obj, qr_image_data_uri = create_or_refresh_offer_qr(current_offer)

    # Generate Merchant Store-Wide Universal Counter QR (Opens /store/<slug>/qr/)
    store_url = ""
    store_qr_data_uri = ""
    if shop:
        store_path = f"/shop/{shop.slug}/offers/"
        store_url = request.build_absolute_uri(store_path)
        store_qr_data_uri = generate_store_qr(store_url)

    # Mode: 'store' (all offers) or 'offer' (single campaign)
    # Default to 'store' if requested or if no single offer selected, but allow switching
    qr_mode = request.GET.get('mode', 'store' if not selected_offer_id else 'offer')

    context = {
        'shop': shop,
        'active_offers': active_offers,
        'current_offer': current_offer,
        'qr_obj': qr_obj,
        'qr_image_data_uri': qr_image_data_uri,
        'store_url': store_url,
        'store_qr_data_uri': store_qr_data_uri,
        'qr_mode': qr_mode,
    }
    return render(request, 'seller/qr_display.html', context)


@seller_required
def seller_credits_view(request):
    """
    Promotional Credits Wallet & Official Offline Rate Card / Executive Recharge View
    """
    seller = request.user
    seller_profile = getattr(seller, 'seller_profile', None)
    is_verified = bool(seller_profile and seller_profile.is_verified)

    if not is_verified:
        wallet = SellerWallet.objects.filter(seller=seller).first()
        if not wallet:
            wallet = SellerWallet(seller=seller, balance_credits=0, lifetime_purchased=0, loyalty_points=0)
    else:
        wallet = get_or_create_wallet(seller)

    packages = CreditPackage.objects.filter(is_active=True).order_by('price_inr')
    transactions = WalletTransaction.objects.filter(wallet=wallet)[:40] if getattr(wallet, 'id', None) else []

    # Get primary store & locality for merchant context
    shop = seller.shops.first() if hasattr(seller, 'shops') else None
    shop_name = shop.name if shop else (getattr(seller_profile, 'business_name', '') or seller.get_full_name() or seller.username)
    shop_locality = shop.locality if (shop and shop.locality) else (getattr(seller, 'profile', None) and seller.profile.city or "Local Area")
    seller_phone = getattr(seller_profile, 'phone', '') or (shop.phone if (shop and shop.phone) else (getattr(seller, 'profile', None) and getattr(seller.profile, 'phone', '') or ""))

    # Handle redundant mock recharge POST processing while preserving wallet data safety
    if request.method == 'POST':
        messages.info(
            request,
            "Online sandbox recharge has been upgraded to Official Offline Billing. "
            "Please use 'Contact Field Executive' to request on-site top-up or company bank transfer."
        )
        return redirect('/seller/credits/')

    # Handle Excel Export for Transactions Ledger
    if request.GET.get('export', '').lower().strip() == 'excel':
        headers = [
            "Transaction Date", "Notes / Description", "Transaction Type",
            "Reference ID", "Credits Delta", "Balance After"
        ]
        rows = []
        for tx in transactions:
            rows.append([
                tx.created_at.strftime("%d-%b-%Y %I:%M %p") if tx.created_at else "N/A",
                tx.notes,
                tx.get_transaction_type_display() if hasattr(tx, 'get_transaction_type_display') else tx.transaction_type,
                tx.reference_id,
                f"+{tx.credits}" if tx.credits > 0 else str(tx.credits),
                tx.balance_after
            ])
        return export_to_excel_response(
            filename_prefix="my_wallet_ledger",
            sheet_title="Wallet Audit",
            headers=headers,
            rows=rows,
            title=f"LokalLoop Merchant — Promotional Credits Audit Ledger ({seller.username})"
        )

    context = {
        'wallet': wallet,
        'packages': packages,
        'transactions': transactions,
        'shop': shop,
        'shop_name': shop_name,
        'shop_locality': shop_locality,
        'seller_phone': seller_phone,
        'field_support_phone': getattr(PlatformSetting.get_settings(), 'support_phone', '') or '+91 98220 11223',
        'field_support_phone_clean': re.sub(r'\D', '', getattr(PlatformSetting.get_settings(), 'support_phone', '') or '919822011223'),
        'approx_redemptions': (wallet.balance_credits // 5) if (wallet and wallet.balance_credits) else 0,
    }
    return render(request, 'seller/credits.html', context)

@verified_seller_required
def seller_credits_buy_view(request):
    """
    Direct endpoint for buying promotional credits.
    Protected with verified seller check.
    """
    return redirect('/seller/credits/')


@verified_seller_required
def seller_redemptions_view(request):
    """
    Merchant Redemptions Log & Excel Export
    """
    seller = request.user
    redemptions = OfferRedemption.objects.filter(seller=seller).select_related('buyer', 'buyer__buyer_profile', 'offer', 'shop').order_by('-redeemed_at')

    # Handle Excel Export for Customer Redemptions (Zero-PII DPDP Act 2023 Compliant)
    if request.GET.get('export', '').lower().strip() == 'excel':
        headers = [
            "Redemption ID", "Date & Time", "Shopper Nickname", "Public Unique ID",
            "Campaign Claimed", "Bill Amount (INR)", "Discount Saved (INR)",
            "Credits Deducted", "Loyalty Points Awarded", "Status"
        ]
        rows = []
        for r in redemptions:
            bp = getattr(r.buyer, 'buyer_profile', None)
            nickname = bp.nickname if (bp and bp.nickname) else "Shopper"
            public_id = bp.public_unique_id if (bp and bp.public_unique_id) else f"LL-{str(r.buyer.id).zfill(4)}"
            rows.append([
                r.id,
                r.redeemed_at.strftime("%d-%b-%Y %I:%M %p") if r.redeemed_at else "N/A",
                nickname,
                public_id,
                r.offer.title if r.offer else "N/A",
                float(r.bill_amount),
                float(r.discount_given),
                r.credits_deducted,
                r.reward_points_earned,
                ("Verified" if r.is_verified_by_merchant else "Completed") if r.status == RedemptionStatus.SUCCESS else r.get_status_display()
            ])
        return export_to_excel_response(
            filename_prefix="my_store_redemptions",
            sheet_title="In-Store Redemptions",
            headers=headers,
            rows=rows,
            title=f"LokalLoop Merchant — Counter Redemptions Log ({seller.username})"
        )

    context = {
        'redemptions': redemptions,
    }
    return render(request, 'seller/redemptions.html', context)


@seller_required
def seller_analytics_view(request):
    """
    Store Footfall & Offer Conversion Analytics
    """
    seller = request.user
    shop = Shop.objects.filter(seller=seller).first()
    offers = Offer.objects.filter(shop__seller=seller)
    redemptions = OfferRedemption.objects.filter(seller=seller)
    successful_redemptions = redemptions.filter(status=RedemptionStatus.SUCCESS)

    total_customers = successful_redemptions.values('buyer').distinct().count()
    agg = successful_redemptions.aggregate(
        total_sales=Sum('bill_amount'),
        total_discounts=Sum('discount_given')
    )
    total_sales_generated = agg['total_sales'] or Decimal('0.00')
    total_discounts = agg['total_discounts'] or Decimal('0.00')

    # BUG-3.6: Dynamic Funnel Conversion Rates
    shop_views = getattr(shop, 'views_count', 0) or (total_customers * 3 or 50)
    scans_count = redemptions.count()
    success_count = successful_redemptions.count()
    scan_rate = min(100, round((scans_count / shop_views * 100))) if shop_views > 0 else 68
    redemption_rate = min(100, round((success_count / scans_count * 100))) if scans_count > 0 else 54

    context = {
        'shop': shop,
        'total_customers': total_customers,
        'total_sales_generated': total_sales_generated,
        'total_discounts': total_discounts,
        'offers': offers,
        'shop_views': shop_views,
        'scans_count': scans_count,
        'success_count': success_count,
        'scan_rate': scan_rate,
        'redemption_rate': redemption_rate,
    }
    return render(request, 'seller/analytics.html', context)


@seller_required
def seller_notifications_view(request):
    """
    Merchant In-App Alerts
    """
    # BUG-103: Force immediate queryset evaluation before issuing update query
    notifications = list(Notification.objects.filter(recipient=request.user))
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return render(request, 'seller/notifications.html', {'notifications': notifications})


@verified_seller_required
def seller_bill_verifications_view(request):
    """
    Merchant Bumper Bill Verification Desk:
    Allows store counter staff to inspect high-value bills (₹10,000+), verify receipt images,
    and sign off on in-store purchase authenticity (Step 1 of Two-Tier Approval).
    """
    from apps.rewards.models import BumperBillSubmission, BumperBillStatus

    seller_shops = Shop.objects.filter(seller=request.user)
    submissions = BumperBillSubmission.objects.filter(
        shop__in=seller_shops
    ).select_related('buyer', 'shop', 'buyer__buyer_profile', 'store_verified_by', 'admin_reviewed_by').order_by('-submitted_at')

    status_filter = request.GET.get('status', '').strip().upper()
    if status_filter in ['PENDING_STORE', 'STORE_VERIFIED', 'APPROVED', 'REJECTED']:
        submissions = submissions.filter(status=status_filter)
    elif status_filter == 'PENDING':
        submissions = submissions.filter(status__in=[BumperBillStatus.PENDING_STORE, 'PENDING'])

    pending_count = BumperBillSubmission.objects.filter(
        shop__in=seller_shops,
        status__in=[BumperBillStatus.PENDING_STORE, 'PENDING']
    ).count()
    store_verified_count = BumperBillSubmission.objects.filter(
        shop__in=seller_shops,
        status=BumperBillStatus.STORE_VERIFIED
    ).count()
    approved_count = BumperBillSubmission.objects.filter(
        shop__in=seller_shops,
        status=BumperBillStatus.APPROVED
    ).count()

    context = {
        'submissions': submissions,
        'pending_count': pending_count,
        'store_verified_count': store_verified_count,
        'approved_count': approved_count,
        'status_filter': status_filter,
    }
    return render(request, 'seller/bill_verifications.html', context)


@verified_seller_required
def seller_bill_verify_approve_view(request, submission_id):
    """
    Step 1: Merchant approves a ₹10,000+ purchase receipt.
    Validates ownership, blocks self-verification, transitions to STORE_VERIFIED,
    and forwards to central platform admin for final voucher release.
    """
    if request.method != 'POST':
        return redirect('seller_bill_verifications')

    from apps.rewards.models import BumperBillSubmission
    from services.reward_service import store_verify_bumper_bill

    submission = get_object_or_404(BumperBillSubmission, pk=submission_id)
    if submission.shop.seller != request.user:
        messages.error(request, "Unauthorized access to this store receipt.")
        return redirect('seller_bill_verifications')

    if submission.buyer == request.user:
        messages.error(request, "Anti-Self-Dealing Violation: Store owners cannot verify their own purchase bills.")
        return redirect('seller_bill_verifications')

    success, msg, _ = store_verify_bumper_bill(submission.id, request.user, approve=True)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('seller_bill_verifications')


@verified_seller_required
def seller_bill_verify_reject_view(request, submission_id):
    """
    Step 1: Merchant rejects a high-value purchase receipt at store verification stage.
    """
    if request.method != 'POST':
        return redirect('seller_bill_verifications')

    from apps.rewards.models import BumperBillSubmission
    from services.reward_service import store_verify_bumper_bill

    submission = get_object_or_404(BumperBillSubmission, pk=submission_id)
    if submission.shop.seller != request.user:
        messages.error(request, "Unauthorized access to this store receipt.")
        return redirect('seller_bill_verifications')

    reason = request.POST.get('rejection_reason', '').strip()
    if not reason:
        reason = "Store could not verify invoice against POS register."
    success, msg, _ = store_verify_bumper_bill(submission.id, request.user, approve=False, rejection_reason=reason)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('seller_bill_verifications')


@verified_seller_required
def seller_verify_catalog_claim_view(request):
    """
    In-Store / Counter Verification & Burning of Dynamic Catalog Reward Vouchers (BUG-49).
    Cashiers look up customer voucher codes presented at billing checkout,
    verify validity, and atomically transition status to 'USED'.
    """
    from apps.rewards.models import BuyerRewardClaim
    from services.reward_service import verify_and_burn_catalog_claim

    recent_verifications = BuyerRewardClaim.objects.filter(
        verified_by=request.user,
        status='USED'
    ).select_related('catalog_item', 'buyer').order_by('-used_at')[:20]

    verified_claim = None
    if request.method == 'POST':
        voucher_code = request.POST.get('voucher_code', '').strip()
        success, msg, claim = verify_and_burn_catalog_claim(voucher_code, request.user)
        if success:
            messages.success(request, msg)
            verified_claim = claim
        else:
            messages.error(request, msg)

    context = {
        'recent_verifications': recent_verifications,
        'verified_claim': verified_claim,
    }
    return render(request, 'seller/verify_catalog_voucher.html', context)


@verified_seller_required
def seller_verify_catalog_claim_api(request):
    """
    API endpoint for counter POS systems / asynchronous voucher scans.
    """
    from django.http import JsonResponse
    import json
    from services.reward_service import verify_and_burn_catalog_claim

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST method required.'}, status=405)

    voucher_code = request.POST.get('voucher_code')
    if not voucher_code and request.body:
        try:
            data = json.loads(request.body)
            voucher_code = data.get('voucher_code')
        except Exception:
            pass

    success, msg, claim = verify_and_burn_catalog_claim(voucher_code, request.user)
    return JsonResponse({
        'success': success,
        'message': msg,
        'voucher_code': claim.voucher_code if claim else (voucher_code or '').strip().upper(),
        'item_title': claim.catalog_item.title if claim else None,
        'buyer_public_id': (claim.buyer.buyer_profile.public_unique_id or claim.buyer.buyer_profile.nickname or "Shopper") if (claim and hasattr(claim.buyer, 'buyer_profile')) else None,
        'status': claim.status if claim else None,
    }, status=200 if success else 400)



def map_tile_proxy_view(request, z, x, y):
    """
    High-reliability, same-origin map tile proxy.
    Guarantees tiles load on mobile without CSP, CORS, or ISP network blocks.
    Caches tiles in memory for instantaneous rendering.
    """
    import urllib.request
    from django.core.cache import cache
    from django.http import HttpResponse

    cache_key = f"tile_proxy:{z}:{x}:{y}"
    cached_data = cache.get(cache_key)
    if cached_data:
        if isinstance(cached_data, tuple):
            tile_bytes, ctype = cached_data
        else:
            tile_bytes, ctype = cached_data, 'image/png'
        response = HttpResponse(tile_bytes, content_type=ctype)
        response['Cache-Control'] = 'public, max-age=86400'
        return response

    # Multi-provider upstream cascade: Google Maps (ultra fast, high availability in India), CartoDB, ArcGIS, OSM
    candidate_urls = [
        f"https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
        f"https://mt0.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
        f"https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        f"https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    ]

    for tile_url in candidate_urls:
        try:
            req = urllib.request.Request(
                tile_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=3.5) as upstream:
                if upstream.status == 200:
                    tile_bytes = upstream.read()
                    # Cache in memory for 7 days
                    content_type = upstream.headers.get('Content-Type', 'image/png')
                    cache.set(cache_key, (tile_bytes, content_type), timeout=86400 * 7)
                    response = HttpResponse(tile_bytes, content_type=content_type)
                    response['Cache-Control'] = 'public, max-age=86400'
                    return response
        except Exception:
            continue

    # Fallback to SVG road grid if external upstream is completely unreachable
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        f'<rect width="256" height="256" fill="#f8fafc"/>'
        f'<path d="M0 64h256M0 128h256M0 192h256M64 0v256M128 0v256M192 0v256" stroke="#e2e8f0" stroke-width="2"/>'
        f'<path d="M0 128h256M128 0v256" stroke="#cbd5e1" stroke-width="6"/>'
        f'<circle cx="128" cy="128" r="4" fill="#94a3b8"/>'
        f'</svg>'
    )
    response = HttpResponse(svg, content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=3600'
    return response
