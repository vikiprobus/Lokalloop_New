from django.db import models
from django.contrib.auth.models import User


class TransactionType(models.TextChoices):
    PURCHASE = 'PURCHASE', 'Credit Purchase'
    BONUS = 'BONUS', 'Welcome / Promotional Bonus'
    DEDUCTION = 'DEDUCTION', 'Offer Redemption Deduction'
    REFUND = 'REFUND', 'Adjustment / Refund'


class SellerWallet(models.Model):
    seller = models.OneToOneField(User, on_delete=models.PROTECT, related_name='wallet')
    balance_credits = models.IntegerField(default=50, help_text='Current available promotional credits')
    lifetime_purchased = models.IntegerField(default=50)
    lifetime_consumed = models.IntegerField(default=0)
    loyalty_points = models.IntegerField(default=0, help_text='Merchant Loyalty Points earned from approving redemptions')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(balance_credits__gte=0),
                name='seller_wallet_balance_credits_non_negative'
            )
        ]

    def __str__(self):
        return f"Wallet ({self.seller.username}): {self.balance_credits} Credits"

    @property
    def has_sufficient_credits(self):
        return self.balance_credits >= 5

    @property
    def loyalty_tier_name(self):
        if self.loyalty_points >= 100:
            return "Top Ranked Outlet Active"
        elif self.loyalty_points >= 50:
            return "Featured Store Active"
        return "Standard Store Tier"

    @property
    def is_top_ranked_outlet(self):
        return self.loyalty_points >= 100

    @property
    def is_featured_store(self):
        return self.loyalty_points >= 50

    @property
    def next_milestone_points(self):
        if self.loyalty_points < 50:
            return 50
        elif self.loyalty_points < 100:
            return 100
        return 100

    @property
    def points_to_next_perk(self):
        if self.loyalty_points < 50:
            return 50 - self.loyalty_points
        elif self.loyalty_points < 100:
            return 100 - self.loyalty_points
        return 0

    @property
    def next_perk_title(self):
        if self.loyalty_points < 50:
            return "1-Week Homepage Featured Store Badge"
        elif self.loyalty_points < 100:
            return "2-Week Top Ranked Outlet Spotlight"
        return "All Tier Perks Unlocked"

    @property
    def loyalty_progress_percent(self):
        """
        Overall progress percentage toward 100-point master milestone.
        """
        if self.loyalty_points <= 0:
            return 0
        if self.loyalty_points >= 100:
            return 100
        return min(100, int(round((self.loyalty_points / 100.0) * 100)))

    @property
    def progress_to_50_percent(self):
        """
        Progress percentage toward the first milestone of 50 points.
        """
        if self.loyalty_points <= 0:
            return 0
        if self.loyalty_points >= 50:
            return 100
        return min(100, int(round((self.loyalty_points / 50.0) * 100)))


class CreditPackage(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    price_inr = models.DecimalField(max_digits=10, decimal_places=2, help_text='Price in Indian Rupees')
    credits = models.PositiveIntegerField()
    bonus_credits = models.PositiveIntegerField(default=0)
    description = models.TextField()
    is_popular = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'price_inr']

    def __str__(self):
        return f"{self.name} - ₹{int(self.price_inr)} ({self.total_credits} Credits)"

    @property
    def total_credits(self):
        return self.credits + self.bonus_credits


class CreditPurchase(models.Model):
    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Completed'
        PENDING = 'PENDING', 'Pending'
        FAILED = 'FAILED', 'Failed'

    seller = models.ForeignKey(User, on_delete=models.PROTECT, related_name='credit_purchases')
    package = models.ForeignKey(CreditPackage, on_delete=models.PROTECT, related_name='purchases')
    amount_inr = models.DecimalField(max_digits=10, decimal_places=2)
    credits_added = models.PositiveIntegerField()
    payment_reference = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUCCESS)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Purchase {self.payment_reference}: ₹{self.amount_inr} by {self.seller.username}"


class WalletTransaction(models.Model):
    wallet = models.ForeignKey(SellerWallet, on_delete=models.PROTECT, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    credits = models.IntegerField(help_text='Positive for credit, negative for debit')
    balance_after = models.IntegerField()
    reference_id = models.CharField(max_length=100, blank=True, default='')
    notes = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()}: {self.credits:+d} Credits (Bal: {self.balance_after})"
