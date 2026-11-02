from django.contrib import admin
from .models import QRCode, OfferRedemption

@admin.register(QRCode)
class QRCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'offer', 'expires_at', 'is_used', 'created_at')
    list_filter = ('is_used',)
    search_fields = ('code', 'offer__title')

@admin.register(OfferRedemption)
class OfferRedemptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'offer', 'shop', 'buyer', 'seller', 'discount_given', 'credits_deducted', 'reward_points_earned', 'redeemed_at')
    list_filter = ('status', 'redeemed_at')
    search_fields = ('buyer__username', 'seller__username', 'offer__title', 'shop__name', 'idempotency_key')
