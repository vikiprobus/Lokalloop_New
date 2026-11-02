from django.contrib import admin
from .models import SellerWallet, CreditPackage, CreditPurchase, WalletTransaction

@admin.register(SellerWallet)
class SellerWalletAdmin(admin.ModelAdmin):
    list_display = ('seller', 'balance_credits', 'lifetime_purchased', 'lifetime_consumed', 'updated_at')
    search_fields = ('seller__username', 'seller__email')

@admin.register(CreditPackage)
class CreditPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'price_inr', 'credits', 'bonus_credits', 'is_popular', 'is_active')
    list_filter = ('is_active', 'is_popular')

@admin.register(CreditPurchase)
class CreditPurchaseAdmin(admin.ModelAdmin):
    list_display = ('payment_reference', 'seller', 'package', 'amount_inr', 'credits_added', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('payment_reference', 'seller__username')

@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'transaction_type', 'credits', 'balance_after', 'reference_id', 'created_at')
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('wallet__seller__username', 'reference_id', 'notes')
