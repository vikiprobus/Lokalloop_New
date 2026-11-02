import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone

from apps.accounts.models import Profile, Role, BuyerProfile, SellerProfile
from apps.shops.models import Shop, ShopCategory, ShopImage
from apps.offers.models import Offer, OfferType, OfferStatus
from apps.redemptions.models import QRCode, OfferRedemption, RedemptionStatus
from apps.wallets.models import SellerWallet, CreditPackage, WalletTransaction, TransactionType, CreditPurchase
from apps.rewards.models import BuyerRewardAccount, RewardTransaction, RewardType
from apps.notifications.models import Notification, NotificationType
from apps.administration.models import AdminAction, SupportTicket, TicketCategory, TicketStatus
from services.wallet_service import get_or_create_wallet
from services.reward_service import get_or_create_reward_account
from services.redemption_service import create_or_refresh_offer_qr


class Command(BaseCommand):
    help = 'Seeds realistic Indian demo data for LokalLoop: Buyers, Sellers, Shops, Offers, QR Codes, Credits & Rewards'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing LokalLoop demo data cleanup & reset..."))

        # 0. Clean old demo records safely
        OfferRedemption.objects.all().delete()
        QRCode.objects.all().delete()
        Offer.objects.all().delete()
        Shop.objects.all().delete()
        SupportTicket.objects.all().delete()
        AdminAction.objects.all().delete()
        Notification.objects.all().delete()
        RewardTransaction.objects.all().delete()
        WalletTransaction.objects.all().delete()
        CreditPurchase.objects.all().delete()
        
        # Clean demo users
        demo_emails = [
            'admin@example.com', 'buyer@example.com', 'seller@example.com',
            'urbanthreads@example.com', 'apextech@example.com', 'naturefresh@example.com', 'bandrabites@example.com',
            'urbantrends@example.com', 'saielectronics@example.com', 'nashikfresh@example.com', 'punechaat@example.com'
        ]
        User.objects.filter(username__in=demo_emails).delete()
        User.objects.filter(email__in=demo_emails).delete()
        self.stdout.write(self.style.SUCCESS("[OK] Previous demo datasets purged cleanly"))

        # 1. Credit Packages in INR
        pkg_data = [
            {
                'name': 'Starter Pack',
                'code': 'STARTER',
                'price_inr': Decimal('499.00'),
                'credits': 100,
                'bonus_credits': 0,
                'description': 'Ideal for neighborhood cafes and boutiques testing counter promotions.',
                'is_popular': False,
                'sort_order': 1,
            },
            {
                'name': 'Growth Pack',
                'code': 'GROWTH',
                'price_inr': Decimal('1499.00'),
                'credits': 350,
                'bonus_credits': 50,
                'description': 'Most popular for busy local stores aiming to boost repeat footfalls.',
                'is_popular': True,
                'sort_order': 2,
            },
            {
                'name': 'Business Pro',
                'code': 'BUSINESS',
                'price_inr': Decimal('3999.00'),
                'credits': 1000,
                'bonus_credits': 200,
                'description': 'High volume retail package with maximum credit savings.',
                'is_popular': False,
                'sort_order': 3,
            },
        ]
        for p in pkg_data:
            CreditPackage.objects.update_or_create(code=p['code'], defaults=p)
        self.stdout.write(self.style.SUCCESS("[OK] Seeded Credit Packages (INR)"))

        # 2. Categories
        categories_data = [
            {'name': 'Food & Cafes', 'slug': 'food', 'icon': 'bi-cup-hot', 'sort_order': 1},
            {'name': 'Fashion & Trends', 'slug': 'fashion', 'icon': 'bi-bag-check', 'sort_order': 2},
            {'name': 'Electronics & Mobile', 'slug': 'electronics', 'icon': 'bi-phone', 'sort_order': 3},
            {'name': 'Groceries & Mart', 'slug': 'grocery', 'icon': 'bi-cart4', 'sort_order': 4},
            {'name': 'Salons & Wellness', 'slug': 'beauty', 'icon': 'bi-scissors', 'sort_order': 5},
            {'name': 'Fitness & Gyms', 'slug': 'fitness', 'icon': 'bi-heart-pulse', 'sort_order': 6},
        ]
        created_cats = {}
        for c in categories_data:
            cat_obj, _ = ShopCategory.objects.update_or_create(slug=c['slug'], defaults=c)
            created_cats[c['slug']] = cat_obj
        self.stdout.write(self.style.SUCCESS("[OK] Seeded Retail Categories"))

        # 3. Demo Admin Account (Platform Governance)
        admin_email = 'admin@example.com'
        admin_user, _ = User.objects.get_or_create(
            username=admin_email,
            defaults={
                'email': admin_email,
                'first_name': 'Platform',
                'last_name': 'Admin',
                'is_staff': True,
                'is_superuser': True,
            }
        )
        admin_user.set_password('demo123')
        admin_user.save()
        Profile.objects.update_or_create(
            user=admin_user,
            defaults={'role': Role.ADMIN, 'phone': '+91 98000 00000', 'city': 'Bengaluru'}
        )
        self.stdout.write(self.style.SUCCESS(f"[OK] Admin User: {admin_email} (Pass: demo123)"))

        # 4. Demo Buyer Account (Generic Shopper)
        buyer_email = 'buyer@example.com'
        buyer_user, _ = User.objects.get_or_create(
            username=buyer_email,
            defaults={
                'email': buyer_email,
                'first_name': 'Demo',
                'last_name': 'Customer',
            }
        )
        buyer_user.set_password('demo123')
        buyer_user.save()
        Profile.objects.update_or_create(
            user=buyer_user,
            defaults={'role': Role.BUYER, 'phone': '+91 98000 00001', 'city': 'Bengaluru'}
        )
        BuyerProfile.objects.update_or_create(
            user=buyer_user,
            defaults={'preferred_city': 'Bengaluru', 'total_redemptions': 3}
        )
        buyer_wallet = get_or_create_reward_account(buyer_user)
        buyer_wallet.current_points = 240
        buyer_wallet.lifetime_earned = 240
        buyer_wallet.save()
        self.stdout.write(self.style.SUCCESS(f"[OK] Buyer User: {buyer_email} (Pass: demo123)"))

        # 5. Demo Seller Account (Merchant Partner)
        seller_email = 'seller@example.com'
        seller_user, _ = User.objects.get_or_create(
            username=seller_email,
            defaults={
                'email': seller_email,
                'first_name': 'Merchant',
                'last_name': 'Partner',
            }
        )
        seller_user.set_password('demo123')
        seller_user.save()
        Profile.objects.update_or_create(
            user=seller_user,
            defaults={'role': Role.SELLER, 'phone': '+91 98000 00002', 'city': 'Bengaluru'}
        )
        SellerProfile.objects.update_or_create(
            user=seller_user,
            defaults={
                'business_name': 'Artisan Brew Coffee & Roasters',
                'gstin': '29ABCDE1234F1Z5',
                'is_verified': True,
                'verified_at': timezone.now()
            }
        )
        seller_wallet = get_or_create_wallet(seller_user)
        seller_wallet.balance_credits = 185
        seller_wallet.lifetime_purchased = 200
        seller_wallet.lifetime_consumed = 15
        seller_wallet.save()
        self.stdout.write(self.style.SUCCESS(f"[OK] Seller User: {seller_email} (Pass: demo123)"))

        # 6. Additional Storefronts Across Key Commercial Hubs
        merchants_seed = [
            {
                'email': 'urbanthreads@example.com',
                'first_name': 'Store',
                'last_name': 'Manager',
                'business_name': 'Urban Threads Studio',
                'category': 'fashion',
                'locality': 'Koramangala',
                'city': 'Bengaluru',
                'address': 'Shop 12, 80 Feet Road, 4th Block, Koramangala, Bengaluru - 560034',
                'tagline': 'Contemporary Designer Wear & Sustainable Fashion',
                'description': 'Curated contemporary apparel, linen essentials, and handloom garments in Koramangala.',
                'phone': '+91 98000 11002',
                'rating': Decimal('4.8'),
                'banner': '/media/shops/banners/banner_fashion.jpg',
                'logo': '/media/shops/logos/logo_fashion.jpg',
            },
            {
                'email': 'apextech@example.com',
                'first_name': 'Store',
                'last_name': 'Manager',
                'business_name': 'Apex Tech & Sound Gallery',
                'category': 'electronics',
                'locality': 'MG Road',
                'city': 'Bengaluru',
                'address': 'Ground Floor, Prestige Meridian, MG Road, Bengaluru - 560001',
                'tagline': 'Premium Audio, Smart Wearables & Mobile Gear',
                'description': 'Authorized retail experience for flagship earbuds, headphones, smart devices, and accessories.',
                'phone': '+91 98000 11003',
                'rating': Decimal('4.7'),
                'banner': '/media/shops/banners/banner_tech.jpg',
                'logo': '/media/shops/logos/logo_tech.jpg',
            },
            {
                'email': 'naturefresh@example.com',
                'first_name': 'Store',
                'last_name': 'Manager',
                'business_name': 'NatureFresh Organic Supermart',
                'category': 'grocery',
                'locality': 'HSR Layout',
                'city': 'Bengaluru',
                'address': 'Plot 45, 27th Main Road, Sector 1, HSR Layout, Bengaluru - 560102',
                'tagline': 'Farm-To-Table Organic Produce & Clean Groceries',
                'description': 'Certified organic farm harvest, cold-pressed oils, stone-ground flours, and pantry essentials.',
                'phone': '+91 98000 11004',
                'rating': Decimal('4.8'),
                'banner': '/media/shops/banners/banner_grocery.jpg',
                'logo': '/media/shops/logos/logo_grocery.jpg',
            },
            {
                'email': 'bandrabites@example.com',
                'first_name': 'Store',
                'last_name': 'Manager',
                'business_name': 'Bandra Street Deli & Cafe',
                'category': 'food',
                'locality': 'Bandra West',
                'city': 'Mumbai',
                'address': 'Lane 4, Hill Road, Bandra West, Mumbai - 400050',
                'tagline': 'Gourmet Sourdough Melts & Artisanal Shakes',
                'description': 'Vibrant neighborhood cafe serving specialty grilled sandwiches, refreshing cold brews, and desserts.',
                'phone': '+91 98000 11005',
                'rating': Decimal('4.9'),
                'banner': '/media/shops/banners/banner_deli.jpg',
                'logo': '/media/shops/logos/logo_deli.jpg',
            },
        ]

        # 7. Seed Primary Demo Shop for seller@example.com
        primary_shop, _ = Shop.objects.update_or_create(
            slug='artisan-brew-coffee-roasters',
            defaults={
                'seller': seller_user,
                'category': created_cats['food'],
                'name': 'Artisan Brew Coffee & Roasters',
                'tagline': 'Specialty Single-Origin Coffee, Artisanal Bakery & Breakfast',
                'description': 'Premier artisanal coffee roastery located on 100 Feet Road, Indiranagar. Renowned for handcrafted pour-overs, sourdough pizza, and viennoiserie.',
                'address': 'Shop 18 & 19, 100 Feet Road, HAL 2nd Stage, Indiranagar',
                'locality': 'Indiranagar',
                'city': 'Bengaluru',
                'state': 'Karnataka',
                'pincode': '560038',
                'latitude': Decimal('12.978400'),
                'longitude': Decimal('77.640800'),
                'phone': '+91 98000 11001',
                'email': 'artisanbrew@example.com',
                'opening_time': datetime.time(9, 0),
                'closing_time': datetime.time(22, 30),
                'rating': Decimal('4.9'),
                'views_count': 180,
                'banner_image_url': '/media/shops/banners/banner_cafe.jpg',
                'logo_image_url': '/media/shops/logos/logo_cafe.jpg',
                'is_approved': True,
                'is_active': True,
            }
        )

        all_shops = [primary_shop]

        for m in merchants_seed:
            u, _ = User.objects.get_or_create(
                username=m['email'],
                defaults={'email': m['email'], 'first_name': m['first_name'], 'last_name': m['last_name']}
            )
            u.set_password('demo123')
            u.save()
            Profile.objects.update_or_create(user=u, defaults={'role': Role.SELLER, 'phone': m['phone'], 'city': m['city']})
            SellerProfile.objects.update_or_create(user=u, defaults={'business_name': m['business_name'], 'is_verified': True})
            get_or_create_wallet(u)

            sh, _ = Shop.objects.update_or_create(
                seller=u,
                name=m['business_name'],
                defaults={
                    'category': created_cats[m['category']],
                    'tagline': m['tagline'],
                    'description': m['description'],
                    'address': m['address'],
                    'locality': m['locality'],
                    'city': m['city'],
                    'phone': m['phone'],
                    'email': m['email'],
                    'rating': m['rating'],
                    'views_count': 110,
                    'banner_image_url': m['banner'],
                    'logo_image_url': m['logo'],
                    'is_approved': True,
                    'is_active': True,
                }
            )
            all_shops.append(sh)

        self.stdout.write(self.style.SUCCESS(f"[OK] Seeded {len(all_shops)} Realistic Local Outlets across Metro Hubs"))

        # 8. Seed Offers
        now = timezone.now()
        offers_data = [
            {
                'shop': primary_shop,
                'title': 'Flat 25% Off on Bakery & Beverages',
                'offer_type': OfferType.PERCENTAGE,
                'discount_value': Decimal('25.00'),
                'min_purchase': Decimal('400.00'),
                'max_discount': Decimal('200.00'),
                'credits_per_redemption': 5,
                'reward_points_per_redemption': 50,
                'max_redemptions': 100,
                'current_redemptions': 14,
                'status': OfferStatus.ACTIVE,
                'is_featured': True,
                'description': 'Enjoy a flat 25% discount across our entire espresso, frappe and fresh pastry selection at Indiranagar.',
                'terms': 'Valid on bills above Rs 400. Applicable for dine-in and takeaway. Cannot be combined with other offers.',
                'image_url': '/media/offers/offer_bakery.jpg',
            },
            {
                'shop': primary_shop,
                'title': 'Buy 1 Specialty Pizza, Get Garlic Bread Free',
                'offer_type': OfferType.BOGO,
                'discount_value': Decimal('150.00'),
                'min_purchase': Decimal('450.00'),
                'credits_per_redemption': 5,
                'reward_points_per_redemption': 60,
                'max_redemptions': 50,
                'current_redemptions': 8,
                'status': OfferStatus.ACTIVE,
                'is_featured': True,
                'description': 'Order any 11-inch artisanal sourdough pizza and receive our freshly baked herb garlic bread free.',
                'terms': 'Available Monday through Friday between 12:00 PM and 7:00 PM.',
                'image_url': '/media/offers/offer_pizza.jpg',
            },
            {
                'shop': all_shops[1],  # Urban Threads
                'title': 'Flat Rs 300 Off on Ethnic & Designer Sets',
                'offer_type': OfferType.FLAT,
                'discount_value': Decimal('300.00'),
                'min_purchase': Decimal('1499.00'),
                'credits_per_redemption': 5,
                'reward_points_per_redemption': 75,
                'max_redemptions': 80,
                'current_redemptions': 22,
                'status': OfferStatus.ACTIVE,
                'is_featured': True,
                'description': 'Instant Rs 300 cashback reduction at checkout on all designer kurtas, dresses and festive sets.',
                'terms': 'Minimum billing Rs 1,499. One redemption per customer per day.',
                'image_url': '/media/offers/offer_fashion.jpg',
            },
            {
                'shop': all_shops[2],  # Apex Tech
                'title': 'Flat 20% Off on Wireless Earbuds & Audio Gear',
                'offer_type': OfferType.PERCENTAGE,
                'discount_value': Decimal('20.00'),
                'min_purchase': Decimal('999.00'),
                'max_discount': Decimal('500.00'),
                'credits_per_redemption': 5,
                'reward_points_per_redemption': 50,
                'max_redemptions': 50,
                'current_redemptions': 11,
                'status': OfferStatus.ACTIVE,
                'is_featured': True,
                'description': 'Upgrade your sound. Save 20% on top branded neckbands, TWS earbuds and Bluetooth speakers.',
                'terms': 'Valid on branded audio gear. Manufacturer warranty applies.',
                'image_url': '/media/offers/offer_audio.jpg',
            },
            {
                'shop': all_shops[3],  # NatureFresh
                'title': 'Flat Rs 100 Off on Organic Groceries & Staples',
                'offer_type': OfferType.FLAT,
                'discount_value': Decimal('100.00'),
                'min_purchase': Decimal('800.00'),
                'credits_per_redemption': 5,
                'reward_points_per_redemption': 40,
                'max_redemptions': 120,
                'current_redemptions': 45,
                'status': OfferStatus.ACTIVE,
                'is_featured': False,
                'description': 'Farm-fresh staples and cold-pressed oils. Save Rs 100 on every weekly family basket.',
                'terms': 'Valid on billing above Rs 800 at HSR Layout store.',
                'image_url': '/media/offers/offer_festive.jpg',
            },
            {
                'shop': all_shops[4],  # Bandra Bites
                'title': 'Flat 20% Off on All Sandwiches & Shakes',
                'offer_type': OfferType.PERCENTAGE,
                'discount_value': Decimal('20.00'),
                'min_purchase': Decimal('250.00'),
                'credits_per_redemption': 5,
                'reward_points_per_redemption': 35,
                'max_redemptions': 150,
                'current_redemptions': 38,
                'status': OfferStatus.ACTIVE,
                'is_featured': True,
                'description': 'Famous Bandra specialty bites. Scan counter QR and get 20% off your entire snack order.',
                'terms': 'Valid all days at Bandra West outlet.',
                'image_url': 'https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop&q=80',
            },
        ]

        created_offers = []
        for od in offers_data:
            od['start_date'] = now - datetime.timedelta(days=5)
            od['end_date'] = now + datetime.timedelta(days=45)
            off, _ = Offer.objects.update_or_create(
                shop=od['shop'],
                title=od['title'],
                defaults=od
            )
            created_offers.append(off)
            create_or_refresh_offer_qr(off)

        self.stdout.write(self.style.SUCCESS(f"[OK] Seeded {len(created_offers)} Active Promotional Campaigns and QR Codes"))

        # 9. Seed Sample Redemptions for Buyer
        first_offer = created_offers[0]
        active_qr = QRCode.objects.filter(offer=first_offer).first()

        OfferRedemption.objects.get_or_create(
            idempotency_key='seed_redemption_001',
            defaults={
                'qr_code': active_qr,
                'offer': first_offer,
                'shop': primary_shop,
                'buyer': buyer_user,
                'seller': seller_user,
                'bill_amount': Decimal('680.00'),
                'discount_given': Decimal('170.00'),
                'credits_deducted': 5,
                'reward_points_earned': 50,
                'status': RedemptionStatus.SUCCESS,
            }
        )

        second_offer = created_offers[2]
        OfferRedemption.objects.get_or_create(
            idempotency_key='seed_redemption_002',
            defaults={
                'offer': second_offer,
                'shop': all_shops[1],
                'buyer': buyer_user,
                'seller': all_shops[1].seller,
                'bill_amount': Decimal('1850.00'),
                'discount_given': Decimal('300.00'),
                'credits_deducted': 5,
                'reward_points_earned': 75,
                'status': RedemptionStatus.SUCCESS,
            }
        )
        self.stdout.write(self.style.SUCCESS("[OK] Seeded Sample Verified Redemptions"))

        # 10. Seed Sample Support Ticket & Admin Action
        SupportTicket.objects.get_or_create(
            subject='Redemption Inquiry at Artisan Brew Coffee',
            defaults={
                'user': buyer_user,
                'category': TicketCategory.REDEMPTION_ISSUE,
                'message': 'Hi team, I redeemed an offer at Artisan Brew Coffee yesterday and received my discount immediately! Wanted to check if points can be used at other shops too.',
                'status': TicketStatus.RESOLVED,
                'admin_notes': 'Explained that LokalLoop reward points are universal and redeemable across all network shops.'
            }
        )

        AdminAction.objects.get_or_create(
            action_type='MERCHANT_APPROVED',
            target_model='SellerProfile',
            target_id=str(seller_user.id),
            defaults={
                'admin': admin_user,
                'details': f'Approved verified merchant: {seller_user.seller_profile.business_name} in Bengaluru.',
                'ip_address': '127.0.0.1'
            }
        )

        # 11. Seed Sample In-App Notifications
        Notification.objects.get_or_create(
            recipient=buyer_user,
            title='Points Credited! (50 Pts)',
            defaults={
                'message': 'You received +50 points on your redemption at Artisan Brew Coffee & Roasters.',
                'notification_type': NotificationType.POINTS_EARNED,
                'link_url': '/buyer/rewards/',
                'is_read': False
            }
        )
        Notification.objects.get_or_create(
            recipient=seller_user,
            title='New Customer Visit!',
            defaults={
                'message': 'A customer redeemed "Flat 25% Off on Bakery & Beverages" at your billing counter.',
                'notification_type': NotificationType.OFFER_REDEEMED,
                'link_url': '/seller/redemptions/',
                'is_read': False
            }
        )

        self.stdout.write(self.style.SUCCESS("""
========================================================================
LOKALLOOP DEMO SEEDING COMPLETED SUCCESSFULLY!

DEMO ACCOUNTS READY TO TEST:
  BUYER:  buyer@example.com   / demo123  -> http://127.0.0.1:8000/buyer/login/
  SELLER: seller@example.com  / demo123  -> http://127.0.0.1:8000/seller/login/
  ADMIN:  admin@example.com   / demo123  -> http://127.0.0.1:8000/admin-login/

PUBLIC WEBSITE: http://127.0.0.1:8000/
========================================================================
"""))
