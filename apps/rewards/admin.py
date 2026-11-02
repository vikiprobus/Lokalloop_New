from django.contrib import admin
from .models import (
    BuyerRewardAccount, RewardTransaction,
    RewardCatalogItem, BuyerRewardClaim, BumperBillSubmission,
    LeaderboardHistory
)

@admin.register(BuyerRewardAccount)
class BuyerRewardAccountAdmin(admin.ModelAdmin):
    list_display = ('buyer', 'current_points', 'lifetime_earned', 'tier_name', 'updated_at')
    search_fields = ('buyer__username', 'buyer__email')

@admin.register(RewardTransaction)
class RewardTransactionAdmin(admin.ModelAdmin):
    list_display = ('account', 'transaction_type', 'points', 'balance_after', 'redemption', 'created_at')
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('account__buyer__username', 'notes')

@admin.register(RewardCatalogItem)
class RewardCatalogItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'points_required', 'reward_type', 'discount_inr', 'is_active', 'stock', 'created_at')
    list_filter = ('is_active', 'reward_type', 'created_at')
    search_fields = ('title', 'description', 'voucher_code_prefix')
    list_editable = ('is_active', 'points_required')

@admin.register(BuyerRewardClaim)
class BuyerRewardClaimAdmin(admin.ModelAdmin):
    list_display = ('voucher_code', 'buyer', 'catalog_item', 'points_spent', 'status', 'claimed_at', 'expires_at')
    list_filter = ('status', 'claimed_at')
    search_fields = ('voucher_code', 'buyer__username', 'catalog_item__title')

@admin.register(BumperBillSubmission)
class BumperBillSubmissionAdmin(admin.ModelAdmin):
    list_display = ('bill_number', 'buyer', 'shop', 'bill_amount', 'status', 'points_awarded', 'voucher_code', 'submitted_at')
    list_filter = ('status', 'submitted_at')
    search_fields = ('bill_number', 'buyer__username', 'shop__name', 'voucher_code')

@admin.register(LeaderboardHistory)
class LeaderboardHistoryAdmin(admin.ModelAdmin):
    list_display = ('cycle_number', 'rank', 'winner', 'points_scored', 'cycle_start', 'cycle_end', 'created_at')
    list_filter = ('cycle_number', 'rank', 'created_at')
    search_fields = ('winner__username', 'notes')


