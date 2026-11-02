import uuid
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from apps.shops.models import Shop


class OfferType(models.TextChoices):
    PERCENTAGE = 'PERCENTAGE', 'Percentage Discount'
    FLAT = 'FLAT', 'Flat Cash Discount (₹)'
    BOGO = 'BOGO', 'Buy 1 Get 1 Free'
    SPECIAL = 'SPECIAL', 'Special Combo / Exclusive'


class OfferStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    UPCOMING = 'UPCOMING', 'Upcoming'
    ACTIVE = 'ACTIVE', 'Active'
    PAUSED = 'PAUSED', 'Paused'
    EXPIRED = 'EXPIRED', 'Expired'


class Offer(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='offers')
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField()
    terms = models.TextField(help_text='Terms and conditions for redemption')
    
    offer_type = models.CharField(max_length=20, choices=OfferType.choices, default=OfferType.PERCENTAGE)
    discount_value = models.DecimalField(max_digits=8, decimal_places=2, default=20.00, help_text='Percentage or Flat INR amount')
    min_purchase = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, help_text='Minimum bill amount in INR')
    max_discount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='Cap on discount for percentage offers')
    
    # Tokenomics: Merchant Credits deducted vs Buyer Reward Points granted
    credits_per_redemption = models.PositiveIntegerField(default=5, help_text='Seller credits deducted on each redemption')
    reward_points_per_redemption = models.PositiveIntegerField(default=50, help_text='Loyalty points awarded to buyer')
    
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()
    
    max_redemptions = models.PositiveIntegerField(default=100)
    current_redemptions = models.PositiveIntegerField(default=0)
    
    status = models.CharField(max_length=20, choices=OfferStatus.choices, default=OfferStatus.ACTIVE)
    image_url = models.CharField(
        max_length=500,
        blank=True,
        default='https://images.unsplash.com/photo-1607082348824-0a96f2a4b9da?w=800&auto=format&fit=crop&q=80'
    )
    is_featured = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_featured', '-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_redemptions__lte=models.F('max_redemptions')),
                name='offer_current_redemptions_lte_max'
            )
        ]

    def __str__(self):
        return f"{self.title} @ {self.shop.name}"

    def calculate_reward_points(self) -> int:
        """
        Calculates flat 1% of min_purchase, capped at 25 points.
        """
        from services.reward_service import calculate_offer_reward_points
        return calculate_offer_reward_points(self.min_purchase)

    @property
    def dynamic_reward_points(self) -> int:
        return self.calculate_reward_points()

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(f"{self.shop.name}-{self.title}")[:200]
            if not base_slug:
                base_slug = f"offer-{uuid.uuid4().hex[:8]}"
            candidate = base_slug
            idx = 1
            while Offer.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug[:190]}-{idx}"
                idx += 1
            self.slug = candidate
        
        # Dynamic 1% Point Calculation on min_purchase (capped at 25 points)
        self.reward_points_per_redemption = self.calculate_reward_points()

        # Auto update status based on date/quota if active
        now = timezone.now()
        if self.status == OfferStatus.ACTIVE:
            if now > self.end_date or self.current_redemptions >= self.max_redemptions:
                self.status = OfferStatus.EXPIRED
            elif now < self.start_date:
                self.status = OfferStatus.UPCOMING
        elif self.status == OfferStatus.UPCOMING:
            if self.start_date <= now <= self.end_date and self.current_redemptions < self.max_redemptions:
                self.status = OfferStatus.ACTIVE
            elif now > self.end_date:
                self.status = OfferStatus.EXPIRED
                
        super().save(*args, **kwargs)

    @property
    def discount_display(self):
        if self.offer_type == OfferType.PERCENTAGE:
            return f"{int(self.discount_value)}% OFF"
        elif self.offer_type == OfferType.FLAT:
            return f"₹{int(self.discount_value)} OFF"
        elif self.offer_type == OfferType.BOGO:
            return "BUY 1 GET 1"
        return "EXCLUSIVE DEAL"

    @property
    def is_currently_valid(self):
        now = timezone.now()
        return (
            self.status == OfferStatus.ACTIVE and
            self.start_date <= now <= self.end_date and
            self.current_redemptions < self.max_redemptions
        )

    @property
    def is_expired(self):
        return timezone.now() > self.end_date or self.status == OfferStatus.EXPIRED

    @property
    def remaining_redemptions(self):
        return max(0, self.max_redemptions - self.current_redemptions)

    @property
    def percentage_claimed(self):
        if self.max_redemptions <= 0:
            return 0
        return min(100, int((self.current_redemptions / self.max_redemptions) * 100))
