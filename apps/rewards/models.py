import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User


class RewardType(models.TextChoices):
    REDEMPTION_REWARD = 'REDEMPTION_REWARD', 'Offer Redemption Reward'
    WELCOME_BONUS = 'WELCOME_BONUS', 'Welcome Sign-up Bonus'
    ADJUSTMENT = 'ADJUSTMENT', 'Platform Adjustment'
    REDEEMED = 'REDEEMED', 'Points Spent'
    CATALOG_REDEMPTION = 'CATALOG_REDEMPTION', 'Reward Catalog Burn'
    BUMPER_BONUS = 'BUMPER_BONUS', 'Bumper Purchase Reward (₹10,000+)'


class CatalogRewardType(models.TextChoices):
    DISCOUNT_VOUCHER = 'DISCOUNT_VOUCHER', 'In-Store Discount Voucher'
    FOOD_BEVERAGE = 'FOOD_BEVERAGE', 'Free Food & Beverage'
    MERCHANDISE = 'MERCHANDISE', 'Free Merchandise / Gift'
    EXPERIENCE = 'EXPERIENCE', 'Special VIP Experience'


class BuyerRewardAccount(models.Model):
    buyer = models.OneToOneField(User, on_delete=models.PROTECT, related_name='reward_account')
    current_points = models.IntegerField(default=25, help_text='Available reward points')
    lifetime_earned = models.IntegerField(default=25)
    lifetime_spent = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_points__gte=0),
                name='buyer_reward_account_points_non_negative'
            )
        ]

    def __str__(self):
        return f"RewardAccount ({self.buyer.username}): {self.current_points} Pts"

    @property
    def tier_name(self):
        if self.lifetime_earned >= 1000:
            return "Gold Explorer"
        elif self.lifetime_earned >= 300:
            return "Silver Patron"
        return "Bronze Member"


class RewardTransaction(models.Model):
    account = models.ForeignKey(BuyerRewardAccount, on_delete=models.PROTECT, related_name='transactions')
    transaction_type = models.CharField(max_length=30, choices=RewardType.choices)
    points = models.IntegerField(help_text='Positive for credit, negative for debit')
    balance_after = models.IntegerField()
    redemption = models.ForeignKey(
        'redemptions.OfferRedemption',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reward_transactions'
    )
    notes = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()}: {self.points:+d} Pts (Bal: {self.balance_after})"


class RewardCatalogItem(models.Model):
    """
    Dynamic Admin Reward Burn Catalog Item:
    Allows administrators to configure points-burning rewards without code modifications.
    """
    title = models.CharField(max_length=150, help_text='e.g., ₹10 Off, ₹50 Off, Free Coffee Voucher')
    points_required = models.PositiveIntegerField(help_text='Points required to burn/redeem (e.g., 50, 75, 100, 150)')
    description = models.TextField(blank=True, help_text='Reward description and redemption instructions')
    reward_type = models.CharField(
        max_length=40,
        choices=CatalogRewardType.choices,
        default=CatalogRewardType.DISCOUNT_VOUCHER
    )
    voucher_code_prefix = models.CharField(max_length=20, default='VOUCHER', help_text='Prefix for generated claim code')
    discount_inr = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), help_text='Monetary discount value if applicable')
    is_active = models.BooleanField(default=True, help_text='Admin toggle to show or hide in catalog without deleting')
    stock = models.IntegerField(default=-1, help_text='Available units. -1 indicates unlimited stock')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['points_required', 'title']

    def __str__(self):
        return f"{self.title} ({self.points_required} Pts)"

    @property
    def is_in_stock(self):
        return self.stock == -1 or self.stock > 0


class BuyerRewardClaim(models.Model):
    """
    Vouchers or rewards claimed by a buyer from the Reward Burn Catalog.
    """
    CLAIM_STATUS = [
        ('ACTIVE', 'Active / Ready to Use'),
        ('USED', 'Redeemed In-Store'),
        ('EXPIRED', 'Expired'),
    ]

    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='claimed_rewards')
    catalog_item = models.ForeignKey(RewardCatalogItem, on_delete=models.PROTECT, related_name='claims')
    voucher_code = models.CharField(max_length=64, unique=True)
    points_spent = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=CLAIM_STATUS, default='ACTIVE')
    claimed_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='verified_catalog_claims')

    class Meta:
        ordering = ['-claimed_at']

    def __str__(self):
        return f"{self.voucher_code} - {self.catalog_item.title} ({self.buyer.username})"


class BumperBillStatus(models.TextChoices):
    PENDING_STORE = 'PENDING_STORE', 'Pending Store Verification'
    STORE_VERIFIED = 'STORE_VERIFIED', 'Store Verified (Awaiting Admin)'
    APPROVED = 'APPROVED', 'Approved & Rewarded'
    REJECTED = 'REJECTED', 'Rejected'
    PENDING = 'PENDING', 'Pending Verification'


class BumperBillSubmission(models.Model):
    """
    Bumper Rewards for High-Value Purchases (₹10,000+):
    Buyers submit bills to receive 100 bonus points and complimentary food/voucher perks.
    Upgraded to Enterprise Two-Tier (Dual Approval / Maker-Checker):
      1. Store Merchant confirms POS register purchase (PENDING_STORE -> STORE_VERIFIED).
      2. Central Platform Admin clears anti-collusion and issues points & vouchers (STORE_VERIFIED -> APPROVED).
    """
    STATUS_CHOICES = BumperBillStatus.choices

    buyer = models.ForeignKey(User, on_delete=models.PROTECT, related_name='bumper_submissions')
    shop = models.ForeignKey('shops.Shop', on_delete=models.PROTECT, related_name='bumper_submissions')
    bill_number = models.CharField(max_length=100, help_text='Invoice/Bill number from the store receipt')
    bill_date = models.DateField(null=True, blank=True, help_text='Date of purchase from invoice receipt')
    bill_amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Total bill amount (must be >= ₹10,000)')
    bill_image = models.ImageField(upload_to='bumper_bills/', null=True, blank=True, help_text='Receipt snapshot/upload')
    status = models.CharField(max_length=20, choices=BumperBillStatus.choices, default=BumperBillStatus.PENDING_STORE)
    rejection_reason = models.TextField(blank=True, default='')
    rejection_stage = models.CharField(max_length=20, blank=True, default='')  # 'STORE' or 'ADMIN'
    points_awarded = models.PositiveIntegerField(default=100)
    voucher_code = models.CharField(max_length=60, blank=True, default='', help_text='Complimentary bumper voucher code')
    voucher_details = models.CharField(
        max_length=255,
        blank=True,
        default='Complimentary Breakfast / Gourmet Coffee / Lunch Voucher'
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_bumper_bills'
    )
    store_verified_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='store_verified_bills'
    )
    store_verified_at = models.DateTimeField(null=True, blank=True)
    admin_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='admin_reviewed_bills'
    )
    admin_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['shop', 'bill_number'],
                condition=~models.Q(status=BumperBillStatus.REJECTED),
                name='unique_active_bumper_bill_submission'
            )
        ]

    def __str__(self):
        return f"Bumper Bill #{self.bill_number} - ₹{self.bill_amount} at {self.shop.name} ({self.status})"


class LeaderboardHistory(models.Model):
    """
    Weekly Shopper Leaderboard Cycle History & Anti-Monopoly Winners Record:
    Records winners of completed 7-day leaderboard cycles.
    Enforces 3-Cycle Cooldown for Rank #1 Grand Prize winners.
    """
    cycle_number = models.PositiveIntegerField(db_index=True)
    cycle_start = models.DateTimeField()
    cycle_end = models.DateTimeField()
    winner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='leaderboard_wins')
    points_scored = models.PositiveIntegerField()
    rank = models.PositiveIntegerField(default=1, help_text='Rank position achieved in cycle')
    notes = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-cycle_number', 'rank']
        verbose_name_plural = 'Leaderboard Histories'

    def __str__(self):
        return f"Cycle #{self.cycle_number} Rank #{self.rank}: {self.winner.username} ({self.points_scored} Pts)"


