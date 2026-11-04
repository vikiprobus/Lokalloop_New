"""
URL configuration for LokalLoop project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from apps.public import views as public_views
from apps.accounts import views as auth_views
from apps.accounts import buyer_views
from apps.shops import seller_views
from apps.administration import views as admin_views
from apps.redemptions import views as redemption_views
from apps.rewards import views as rewards_views

urlpatterns = [
    # Built-in developer admin (configurable secret path via DJANGO_ADMIN_URL)
    path(settings.DJANGO_ADMIN_URL, admin.site.urls),

    # 1. Public Website & SEO
    path('', public_views.landing_page_view, name='landing_page'),
    path('service-worker.js', public_views.service_worker_view, name='service_worker'),
    path('robots.txt', public_views.robots_txt_view, name='robots_txt'),
    path('sitemap.xml', public_views.sitemap_xml_view, name='sitemap_xml'),
    path('terms/', public_views.terms_view, name='terms'),
    path('terms-and-conditions/', public_views.terms_view, name='terms_and_conditions'),
    path('privacy-policy/', public_views.privacy_policy_view, name='privacy_policy'),
    path('refund-policy/', public_views.refund_policy_view, name='refund_policy'),
    path('cookie-policy/', public_views.cookie_policy_view, name='cookie_policy'),
    path('thank-you/', public_views.thank_you_view, name='thank_you'),
    path('offline/', public_views.offline_view, name='offline'),
    path('explore/', public_views.explore_view, name='explore'),
    path('shops/<slug:slug>/', public_views.shop_detail_view, name='shop_detail'),
    path('store/<slug:slug>/qr/', public_views.shop_qr_landing_view, name='shop_qr_landing'),
    path('store/<slug:slug>/deals/', public_views.shop_qr_landing_view, name='store_deals'),
    path('shop/<slug:slug>/offers/', public_views.shop_qr_landing_view, name='shop_offers'),
    path('shops/<slug:slug>/offers/', public_views.shop_qr_landing_view, name='shops_offers'),
    path('qr/<str:token>/', public_views.qr_token_landing_view, name='qr_token_landing'),
    path('offers/<slug:slug>/', public_views.offer_detail_view, name='offer_detail'),
    path('for-sellers/', public_views.for_sellers_view, name='for_sellers'),
    path('contact/', public_views.contact_us_view, name='contact'),
    path('contact-us/', public_views.contact_us_view, name='contact_us'),

    # 2. Authentication & Roles
    path('login/', auth_views.login_chooser_view, name='login'),
    path('buyer/login/', auth_views.buyer_login_view, name='buyer_login'),
    path('buyer/register/', auth_views.buyer_register_view, name='buyer_register'),
    path('buyer/recover/', auth_views.buyer_recover_view, name='buyer_recover'),
    path('buyer/forgot-password/', auth_views.buyer_recover_view, name='buyer_forgot_password'),
    path('api/shopper-id/', auth_views.generate_shopper_id_api, name='api_shopper_id'),
    path('api/captcha/refresh/', auth_views.refresh_captcha_api, name='api_captcha_refresh'),
    path('api/captcha/verify-slider/', auth_views.verify_slider_captcha_api, name='api_captcha_verify_slider'),
    path('api/subcategories/', auth_views.get_subcategories_api, name='api_subcategories'),
    path('buyer/onboarding/', auth_views.buyer_onboarding_view, name='buyer_onboarding'),
    path('buyer/set-nickname/', auth_views.buyer_set_nickname_view, name='buyer_set_nickname'),
    path('buyer/logout/', auth_views.logout_view, name='buyer_logout'),

    path('seller/login/', auth_views.seller_login_view, name='seller_login'),
    path('seller/register/', auth_views.seller_register_view, name='seller_register'),
    path('seller/recover/', auth_views.seller_recover_view, name='seller_recover'),
    path('seller/forgot-password/', auth_views.seller_recover_view, name='seller_forgot_password'),
    path('seller/logout/', auth_views.logout_view, name='seller_logout'),

    path('admin-login/', auth_views.admin_login_view, name='admin_login'),
    path('admin-logout/', auth_views.logout_view, name='admin_logout'),
    path('logout/', auth_views.logout_view, name='logout'),

    # 3. Buyer Portal
    path('buyer/dashboard/', buyer_views.buyer_dashboard_view, name='buyer_dashboard'),
    path('buyer/explore/', buyer_views.buyer_explore_view, name='buyer_explore'),
    path('buyer/shops/<slug:slug>/', public_views.shop_detail_view, name='buyer_shop_detail'),
    path('buyer/offers/<slug:slug>/', public_views.offer_detail_view, name='buyer_offer_detail'),
    path('buyer/scan/', buyer_views.buyer_scan_view, name='buyer_scan'),
    path('buyer/rewards/', buyer_views.buyer_rewards_view, name='buyer_rewards'),
    path('buyer/rewards/redeem/<int:item_id>/', buyer_views.buyer_redeem_catalog_reward_view, name='buyer_redeem_catalog_reward'),
    path('buyer/rewards/bumper-bill/', buyer_views.buyer_submit_bumper_bill_view, name='buyer_submit_bumper_bill'),
    path('buyer/leaderboard/', rewards_views.buyer_leaderboard_view, name='buyer_leaderboard'),
    path('buyer/history/', buyer_views.buyer_history_view, name='buyer_history'),
    path('buyer/notifications/', buyer_views.buyer_notifications_view, name='buyer_notifications'),
    path('buyer/account/', buyer_views.buyer_account_view, name='buyer_account'),
    path('buyer/change-password/', auth_views.buyer_change_password_view, name='buyer_change_password'),
    path('buyer/delete-account/', auth_views.buyer_delete_account_view, name='buyer_delete_account'),

    # 4. Seller Portal
    path('seller/dashboard/', seller_views.seller_dashboard_view, name='seller_dashboard'),
    path('seller/shop/', seller_views.seller_shop_view, name='seller_shop'),
    path('seller/profile/', seller_views.seller_shop_view, name='seller_profile'),
    path('seller/offers/', seller_views.seller_offers_list_view, name='seller_offers'),
    path('seller/offers/create/', seller_views.seller_offer_create_view, name='seller_offer_create'),
    path('seller/offers/new/', seller_views.seller_offer_create_view, name='seller_offer_new'),
    path('seller/offers/<int:offer_id>/toggle/', seller_views.seller_offer_toggle_status_view, name='seller_offer_toggle'),
    path('seller/qr/', seller_views.seller_qr_desk_view, name='seller_qr_desk'),
    path('seller/credits/', seller_views.seller_credits_view, name='seller_credits'),
    path('seller/credits/buy/', seller_views.seller_credits_buy_view, name='seller_credits_buy'),
    path('seller/redemptions/', seller_views.seller_redemptions_view, name='seller_redemptions'),
    path('seller/bill-verifications/', seller_views.seller_bill_verifications_view, name='seller_bill_verifications'),
    path('seller/bill-verifications/<int:submission_id>/approve/', seller_views.seller_bill_verify_approve_view, name='seller_bill_verify_approve'),
    path('seller/bill-verifications/<int:submission_id>/reject/', seller_views.seller_bill_verify_reject_view, name='seller_bill_verify_reject'),
    path('seller/vouchers/verify/', seller_views.seller_verify_catalog_claim_view, name='seller_verify_catalog_claim'),
    path('api/seller/vouchers/verify/', seller_views.seller_verify_catalog_claim_api, name='api_seller_verify_catalog_claim'),
    path('seller/analytics/', seller_views.seller_analytics_view, name='seller_analytics'),
    path('seller/notifications/', seller_views.seller_notifications_view, name='seller_notifications'),
    path('seller/change-password/', auth_views.seller_change_password_view, name='seller_change_password'),
    path('seller/delete-account/', auth_views.seller_delete_account_view, name='seller_delete_account'),
    path('api/seller/check-deletion-eligibility/', auth_views.seller_check_deletion_eligibility_api, name='api_seller_check_deletion_eligibility'),

    # Real-Time Live Counter Redemption & Seller Approval Workflow
    path('api/seller/pending-redemptions/', redemption_views.seller_pending_redemptions_api, name='api_seller_pending_redemptions'),
    path('api/seller/redemptions/<int:redemption_id>/approve/', redemption_views.seller_approve_redemption_view, name='api_seller_approve_redemption'),
    path('api/seller/redemptions/<int:redemption_id>/reject/', redemption_views.seller_reject_redemption_view, name='api_seller_reject_redemption'),
    path('api/redemptions/<int:redemption_id>/status/', redemption_views.redemption_status_api, name='api_redemption_status'),

    # 5. Admin Portal
    path('admin-portal/dashboard/', admin_views.admin_dashboard_view, name='admin_dashboard'),
    path('admin-portal/buyers/', admin_views.admin_buyers_view, name='admin_buyers'),
    path('admin-portal/sellers/', admin_views.admin_sellers_view, name='admin_sellers'),
    path('admin-portal/sellers/<int:seller_id>/toggle/', admin_views.admin_seller_toggle_approval_view, name='admin_seller_toggle'),
    path('admin-portal/merchants/', admin_views.admin_sellers_view, name='admin_merchants'),
    path('admin-portal/merchants/<int:seller_id>/toggle/', admin_views.admin_seller_toggle_approval_view, name='admin_merchant_toggle'),
    path('admin-portal/shops/', admin_views.admin_shops_view, name='admin_shops'),
    path('admin-portal/categories/', admin_views.admin_categories_view, name='admin_categories'),
    path('admin-portal/categories/<int:category_id>/toggle/', admin_views.admin_category_toggle_view, name='admin_category_toggle'),
    path('admin-portal/subcategories/<int:subcategory_id>/toggle/', admin_views.admin_subcategory_toggle_view, name='admin_subcategory_toggle'),
    path('admin-portal/verifications/', admin_views.admin_verifications_view, name='admin_verifications'),
    path('admin-portal/verifications/<int:verification_id>/update/', admin_views.admin_verification_update_view, name='admin_verification_update'),
    path('admin-portal/offers/', admin_views.admin_offers_view, name='admin_offers'),
    path('admin-portal/rewards/', admin_views.admin_rewards_catalog_view, name='admin_rewards_catalog'),
    path('admin-portal/rewards/create/', admin_views.admin_rewards_catalog_create_view, name='admin_rewards_catalog_create'),
    path('admin-portal/rewards/<int:item_id>/toggle/', admin_views.admin_rewards_catalog_toggle_view, name='admin_rewards_catalog_toggle'),
    path('admin-portal/bumper-bills/', admin_views.admin_bumper_bills_view, name='admin_bumper_bills'),
    path('admin-portal/bumper-bills/<int:submission_id>/approve/', admin_views.admin_bumper_bill_approve_view, name='admin_bumper_bill_approve'),
    path('admin-portal/bumper-bills/<int:submission_id>/reject/', admin_views.admin_bumper_bill_reject_view, name='admin_bumper_bill_reject'),
    path('admin-portal/leaderboard/', admin_views.admin_leaderboard_view, name='admin_leaderboard'),
    path('admin-portal/leaderboard/rollover/', admin_views.admin_leaderboard_rollover_view, name='admin_leaderboard_rollover'),
    path('admin-portal/redemptions/', admin_views.admin_redemptions_view, name='admin_redemptions'),
    path('admin-portal/credits/', admin_views.admin_credits_view, name='admin_credits'),
    path('admin-portal/audit/', admin_views.admin_audit_log_view, name='admin_audit'),
    path('admin-portal/agreement-logs/', admin_views.admin_agreement_logs_view, name='admin_agreement_logs'),
    path('admin-portal/error-logs/', admin_views.admin_error_logs_view, name='admin_error_logs'),
    path('admin-portal/error-logs/<int:log_id>/resolve/', admin_views.admin_error_log_resolve_view, name='admin_error_log_resolve'),
    path('admin-portal/error-logs/clear/', admin_views.admin_error_log_clear_view, name='admin_error_log_clear'),
    path('admin-portal/tickets/', admin_views.admin_tickets_view, name='admin_tickets'),
    path('admin-portal/tickets/<int:ticket_id>/update/', admin_views.admin_ticket_update_view, name='admin_ticket_update'),
    path('admin-portal/inquiries/<int:inquiry_id>/update/', admin_views.admin_inquiry_update_view, name='admin_inquiry_update'),
    path('admin-portal/settings/', admin_views.admin_settings_view, name='admin_settings'),
]

from django.urls import re_path
from django.views.static import serve

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

handler404 = 'apps.administration.views.custom_404_view'
handler500 = 'apps.administration.views.custom_500_view'
