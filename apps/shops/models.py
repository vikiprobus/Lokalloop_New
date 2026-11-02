import uuid
import datetime
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class ShopCategory(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    icon = models.CharField(max_length=50, default='bi-tag', help_text='Icon class or identifier')
    description = models.TextField(blank=True, default='')
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subcategories')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    requires_fssai = models.BooleanField(default=False, help_text="True if businesses in this category require FSSAI Food License")
    requires_fda = models.BooleanField(default=False, help_text="True if businesses in this category require FDA Pharmacy License")

    class Meta:
        verbose_name_plural = 'Shop Categories'
        ordering = ['sort_order', 'name']

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} → {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or f"subcat-{uuid.uuid4().hex[:8]}" or f"cat-{uuid.uuid4().hex[:8]}"
            candidate = base_slug
            idx = 1
            while ShopCategory.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{idx}"
                idx += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def active_subcategories(self):
        return self.shop_subcategories.filter(is_active=True)


class ShopSubcategory(models.Model):
    category = models.ForeignKey(ShopCategory, on_delete=models.CASCADE, related_name='shop_subcategories')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Shop Subcategories'
        ordering = ['sort_order', 'name']
        unique_together = ('category', 'slug')

    def __str__(self):
        return f"{self.category.name} → {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or f"cat-{uuid.uuid4().hex[:8]}"
            candidate = base_slug
            idx = 1
            while ShopSubcategory.objects.filter(category=self.category, slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{idx}"
                idx += 1
            self.slug = candidate
        super().save(*args, **kwargs)


class Shop(models.Model):
    seller = models.ForeignKey(User, on_delete=models.PROTECT, related_name='shops')
    category = models.ForeignKey(ShopCategory, on_delete=models.PROTECT, related_name='shops')
    subcategory = models.ForeignKey(ShopSubcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='shops')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    tagline = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField()

    # Location & Address (Indian Context)
    address = models.CharField(max_length=255, help_text='Shop No., Building, Street')
    locality = models.CharField(max_length=150, help_text='e.g., Indiranagar, Koramangala, Bandra West')
    city = models.CharField(max_length=100, default='Bengaluru')
    state = models.CharField(max_length=100, default='Karnataka')
    pincode = models.CharField(max_length=10, default='560038')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, default=12.9716)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, default=77.5946)

    # Contact & Timings
    phone = models.CharField(max_length=20, default='+91 98000 11223')
    display_phone = models.CharField(max_length=20, blank=True, default='', help_text='Public customer-facing support number')
    email = models.EmailField(blank=True, default='')
    opening_time = models.TimeField(default=datetime.time(10, 0))
    closing_time = models.TimeField(default=datetime.time(22, 0))
    operating_hours = models.CharField(max_length=150, blank=True, default='09:00 AM - 10:00 PM (All Days)', help_text='Structured schedule and open days')

    # Media
    logo_image_url = models.CharField(
        max_length=500,
        blank=True,
        default='https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=200&auto=format&fit=crop&q=80'
    )
    banner_image_url = models.CharField(
        max_length=500,
        blank=True,
        default='https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=1200&auto=format&fit=crop&q=80'
    )

    # Status & Moderation
    is_approved = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=4.8)
    views_count = models.PositiveIntegerField(default=0)

    # Merchant Loyalty Perks Unlocks (50 & 100 Points)
    is_featured = models.BooleanField(default=False, help_text='Featured / Priority Store on Homepage (Unlocked at 50 loyalty points)')
    featured_until = models.DateTimeField(null=True, blank=True)
    is_spotlight = models.BooleanField(default=False, help_text='Top Ranked Outlet Spotlight (Unlocked at 100 loyalty points)')
    spotlight_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.locality}, {self.city}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or f"shop-{uuid.uuid4().hex[:8]}"
            candidate = base_slug
            idx = 1
            while Shop.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{idx}"
                idx += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def business_name(self):
        return self.name

    @property
    def full_address(self):
        return f"{self.address}, {self.locality}, {self.city}, {self.state} - {self.pincode}"

    @property
    def timing_display(self):
        return f"{self.opening_time.strftime('%I:%M %p')} - {self.closing_time.strftime('%I:%M %p')}"

    @property
    def active_offers_count(self):
        from django.utils import timezone
        from django.db.models import F
        now = timezone.now()
        return self.offers.filter(
            status='ACTIVE',
            start_date__lte=now,
            end_date__gte=now,
            current_redemptions__lt=F('max_redemptions')
        ).count()

    @property
    def is_featured_active(self) -> bool:
        from django.utils import timezone
        if self.is_featured:
            if not self.featured_until or self.featured_until > timezone.now():
                return True
        return False

    @property
    def is_spotlight_active(self) -> bool:
        from django.utils import timezone
        if self.is_spotlight:
            if not self.spotlight_until or self.spotlight_until > timezone.now():
                return True
        return False

    @property
    def master_qr_url(self) -> str:
        """
        Master Counter QR code URL opens /shop/<slug>/offers/ listing all active deals.
        """
        return f"/shop/{self.slug}/offers/"

    def get_master_qr_data_uri(self, base_url: str = "") -> str:
        """
        Returns high-contrast base64 Data URI PNG for physical standee or digital counter display.
        Supports absolute URI resolution so mobile cameras/Google Lens can open links directly (BUG-84).
        """
        from services.redemption_service import generate_store_qr
        if base_url:
            clean_base = base_url.rstrip('/')
            url = f"{clean_base}/shop/{self.slug}/offers/"
        else:
            url = self.master_qr_url
        return generate_store_qr(url)



class ShopImage(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='gallery_images')
    image_url = models.CharField(max_length=500)
    caption = models.CharField(max_length=200, blank=True, default='')
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.shop.name}"


class ShopVerification(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending Verification'),
        ('APPROVED', 'Verified & Compliant'),
        ('REJECTED', 'Action Required'),
    )

    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name='verification')
    shop_act_license_number = models.CharField(max_length=50, blank=True, default='', help_text="Compulsory Municipal / State Gumasta / Shop Act License No.")
    shop_act_valid_till = models.DateField(null=True, blank=True, help_text="Shop Act License Valid Till")
    fssai_license_number = models.CharField(max_length=30, blank=True, default='', help_text="FSSAI License (Mandatory for Grocery / Food)")
    fssai_valid_till = models.DateField(null=True, blank=True, help_text="FSSAI License Valid Till")
    fda_license_number = models.CharField(max_length=40, blank=True, default='', help_text="FDA License (Mandatory for Pharmacy / Healthcare)")
    fda_valid_till = models.DateField(null=True, blank=True, help_text="FDA License Valid Till")
    gst_number = models.CharField(max_length=20, blank=True, default='', help_text="15-character GSTIN")
    business_pan = models.CharField(max_length=15, blank=True, default='', help_text="10-character Business PAN")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_shops')
    verified_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Verification: {self.shop.name} ({self.status})"

