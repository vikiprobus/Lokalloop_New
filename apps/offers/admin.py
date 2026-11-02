from django.contrib import admin
from .models import Offer

@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ('title', 'shop', 'offer_type', 'discount_display', 'status', 'current_redemptions', 'max_redemptions', 'end_date')
    list_filter = ('status', 'offer_type', 'is_featured')
    search_fields = ('title', 'shop__name', 'description')
    prepopulated_fields = {'slug': ('title',)}
