from django.test import TestCase, Client
import json
import os


class PwaIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_manifest_file_exists_and_is_valid_json(self):
        manifest_path = os.path.join('static', 'manifest.json')
        self.assertTrue(os.path.exists(manifest_path))

        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(data.get('short_name'), 'LokalLoop')
        self.assertEqual(data.get('start_url'), '/')
        self.assertEqual(data.get('display'), 'standalone')
        self.assertEqual(data.get('theme_color'), '#F97316')
        self.assertEqual(data.get('background_color'), '#0F172A')
        self.assertTrue(len(data.get('icons', [])) >= 2)

    def test_service_worker_and_pwa_js_files_exist(self):
        sw_path = os.path.join('static', 'js', 'service-worker.js')
        pwa_path = os.path.join('static', 'js', 'pwa.js')
        icon192 = os.path.join('static', 'images', 'icon-192.svg')
        icon512 = os.path.join('static', 'images', 'icon-512.svg')

        self.assertTrue(os.path.exists(sw_path))
        self.assertTrue(os.path.exists(pwa_path))
        self.assertTrue(os.path.exists(icon192))
        self.assertTrue(os.path.exists(icon512))

    def test_offline_page_renders_with_200(self):
        response = self.client.get('/offline/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're Currently Offline")
        self.assertContains(response, "Try Reconnecting")

    def test_pwa_meta_tags_in_public_base(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'name="theme-color" content="#F97316"')
        self.assertContains(response, 'name="apple-mobile-web-app-capable" content="yes"')
        self.assertContains(response, 'static/js/pwa.js')
        self.assertContains(response, 'id="pwaInstallBanner"')

    def test_counter_qr_landing_page_renders_with_offers(self):
        from apps.shops.models import Shop, ShopCategory
        from apps.offers.models import Offer, OfferStatus
        from django.contrib.auth.models import User
        from decimal import Decimal
        from datetime import date, timedelta

        from apps.offers.models import Offer, OfferStatus, OfferType
        from django.utils import timezone

        seller = User.objects.create_user(username='test_seller_qr', email='seller_qr@example.com', password='pass')
        cat = ShopCategory.objects.create(name='Test Category', slug='test-cat-qr')
        shop = Shop.objects.create(
            seller=seller,
            category=cat,
            name='Blue Tokai Roasters',
            slug='blue-tokai-roasters',
            locality='Koramangala',
            city='Bengaluru',
            is_approved=True
        )
        offer = Offer.objects.create(
            shop=shop,
            title='Flat 20% Off Pour Over',
            slug='flat-20-off-pour-over',
            offer_type=OfferType.PERCENTAGE,
            discount_value=Decimal('20.00'),
            min_purchase=Decimal('250.00'),
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=10),
            status=OfferStatus.ACTIVE
        )

        # 1. Test public counter QR landing page
        response = self.client.get(f'/store/{shop.slug}/qr/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Blue Tokai Roasters')
        self.assertContains(response, 'Flat 20% Off Pour Over')
        self.assertContains(response, 'IN-STORE COUNTER STANDEE')

        # 2. Test QR code token resolution
        from apps.redemptions.models import QRCode
        from django.utils import timezone
        qr_obj = QRCode.objects.create(
            offer=offer,
            code='tok-test-12345',
            expires_at=timezone.now() + timedelta(days=1)
        )
        token_res = self.client.get('/qr/tok-test-12345/')
        self.assertEqual(token_res.status_code, 302)
        self.assertTrue(f'/store/{shop.slug}/qr/?claim_offer={offer.id}' in token_res.url)

