from django.contrib import admin
from .models import Profile, BuyerProfile, SellerProfile, AccountDeletionLog

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'city', 'phone', 'is_active', 'created_at')
    list_filter = ('role', 'city', 'is_active')
    search_fields = ('user__username', 'user__email', 'phone')

@admin.register(BuyerProfile)
class BuyerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'preferred_city', 'total_redemptions', 'created_at')
    search_fields = ('user__username', 'preferred_city')

@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'user', 'is_verified', 'created_at')
    list_filter = ('is_verified',)
    search_fields = ('business_name', 'user__username', 'gstin')

@admin.register(AccountDeletionLog)
class AccountDeletionLogAdmin(admin.ModelAdmin):
    list_display = ('user_identifier', 'role', 'reason_category', 'stores_delisted_count', 'deleted_at', 'ip_address')
    list_filter = ('role', 'reason_category', 'deleted_at')
    search_fields = ('user_identifier', 'reason_notes', 'ip_address')
    readonly_fields = (
        'user', 'user_identifier', 'role', 'reason_category', 'reason_notes',
        'ip_address', 'user_agent', 'stores_delisted_count', 'financial_records_retained', 'deleted_at'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
