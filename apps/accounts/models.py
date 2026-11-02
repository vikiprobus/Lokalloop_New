from django.contrib.auth.hashers import make_password, check_password
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class Role(models.TextChoices):
    BUYER = 'BUYER', 'Buyer'
    SELLER = 'SELLER', 'Seller'
    ADMIN = 'ADMIN', 'Platform Admin'


class Gender(models.TextChoices):
    MALE = 'MALE', 'Male'
    FEMALE = 'FEMALE', 'Female'
    OTHER = 'OTHER', 'Other'


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.BUYER)
    phone = models.CharField(max_length=15, blank=True, default='')
    city = models.CharField(max_length=100, default='Bengaluru')
    avatar_url = models.CharField(
        max_length=500,
        blank=True,
        default='https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_buyer(self):
        return self.role == Role.BUYER

    @property
    def is_seller(self):
        return self.role == Role.SELLER

    @property
    def is_admin(self):
        return self.role == Role.ADMIN or self.user.is_superuser


class BuyerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='buyer_profile')
    shopper_id = models.CharField(max_length=30, blank=True, default='', db_index=True)
    district = models.CharField(max_length=100, default='Pune')
    state = models.CharField(max_length=100, default='Maharashtra')
    
    # Smart Onboarding & Algorithmic Demographics
    birth_month = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Birth month (1-12)")
    birth_year = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Birth year (e.g. 1998)")
    age = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Calculated shopper age")
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, default='', help_text="Shopper gender: Male, Female, Other")
    internal_composite_id = models.CharField(max_length=64, blank=True, default='', help_text="Algorithmic composite identity e.g. A8208-O56-U4127-S2905")
    public_unique_id = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True, help_text="3-block public ID e.g. LQ7M-8K2R-5D9X")
    
    # "Gennie" Personalization
    nickname = models.CharField(max_length=50, blank=True, default='', help_text="Preferred nickname for Gennie greeting")
    is_nickname_set = models.BooleanField(default=False, help_text="True if user has provided or confirmed nickname")
    
    # Handset Persistence & Auto-Login
    handset_device_token = models.CharField(max_length=128, blank=True, default='', db_index=True, help_text="Unique device token for seamless auto-login")

    security_question = models.CharField(max_length=255, blank=True, default='')
    security_answer_hash = models.CharField(max_length=255, blank=True, default='')

    def set_security_answer(self, raw_answer):
        if raw_answer:
            normalized = raw_answer.strip().lower()
            self.security_answer_hash = make_password(normalized)
        else:
            self.security_answer_hash = ''

    def check_security_answer(self, raw_answer):
        if not self.security_answer_hash or not raw_answer:
            return False
        normalized = raw_answer.strip().lower()
        return check_password(normalized, self.security_answer_hash)
    preferred_city = models.CharField(max_length=100, default='Pune')
    default_latitude = models.DecimalField(max_digits=9, decimal_places=6, default=18.5204)  # Pune approx
    default_longitude = models.DecimalField(max_digits=9, decimal_places=6, default=73.8567)
    total_redemptions = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        display_id = self.public_unique_id or self.shopper_id or f"Shopper-{self.user_id}"
        if self.nickname:
            return f"BuyerProfile: {self.nickname} ({display_id})"
        return f"BuyerProfile: {display_id}"


class SellerProfile(models.Model):
    DESIGNATION_CHOICES = (
        ('OWNER', 'Store Owner'),
        ('MANAGER', 'Authorized Manager / Partner'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='seller_profile')
    merchant_id = models.CharField(max_length=30, unique=True, null=True, blank=True, db_index=True, help_text="City-Code Merchant ID e.g. PUN-273849")
    full_name = models.CharField(max_length=100, blank=True, default='', help_text="Full legal name of owner / authorized signatory")
    designation = models.CharField(max_length=20, choices=DESIGNATION_CHOICES, default='OWNER')
    email = models.EmailField(blank=True, default='', help_text="Official correspondence email")
    phone = models.CharField(max_length=15, blank=True, default='', help_text="10-digit verified mobile number")
    business_name = models.CharField(max_length=200)
    gstin = models.CharField(max_length=20, blank=True, default='')
    pan_number = models.CharField(max_length=15, blank=True, default='')
    business_type = models.CharField(max_length=100, default='Retail')
    agreement_accepted = models.BooleanField(default=False, help_text="True once merchant accepts Digital Vendor Agreement")
    agreement_accepted_at = models.DateTimeField(null=True, blank=True)
    is_verified = models.BooleanField(default=True)  # True for demo ease
    verified_at = models.DateTimeField(null=True, blank=True)
    security_question = models.CharField(max_length=255, blank=True, default='')
    security_answer_hash = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def set_security_answer(self, raw_answer):
        if raw_answer:
            normalized = raw_answer.strip().lower()
            self.security_answer_hash = make_password(normalized)
        else:
            self.security_answer_hash = ''

    def check_security_answer(self, raw_answer):
        if not self.security_answer_hash or not raw_answer:
            return False
        normalized = raw_answer.strip().lower()
        return check_password(normalized, self.security_answer_hash)

    def __str__(self):
        return f"SellerProfile: {self.business_name} ({self.user.username})"


class DeletionRole(models.TextChoices):
    SELLER = 'SELLER', 'Merchant / Seller'
    BUYER = 'BUYER', 'Shopper / Buyer'


class DeletionReasonCategory(models.TextChoices):
    CLOSING_BUSINESS = 'CLOSING_BUSINESS', 'Closing business'
    PRIVACY_CONCERNS = 'PRIVACY_CONCERNS', 'Privacy concerns'
    SWITCHING_PLATFORMS = 'SWITCHING_PLATFORMS', 'Switching platforms'
    NO_LONGER_NEEDED = 'NO_LONGER_NEEDED', 'No longer needed'
    OTHER = 'OTHER', 'Other'


class AccountDeletionLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deletion_logs')
    user_identifier = models.CharField(max_length=150, help_text="Preserved username, email, shopper_id, or merchant_id")
    role = models.CharField(max_length=20, choices=DeletionRole.choices)
    reason_category = models.CharField(max_length=50, choices=DeletionReasonCategory.choices, default=DeletionReasonCategory.OTHER)
    reason_notes = models.TextField(blank=True, default='', help_text="Optional user feedback or closure context")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    stores_delisted_count = models.IntegerField(default=0)
    financial_records_retained = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-deleted_at']
        verbose_name = 'Account Deletion Audit Log'
        verbose_name_plural = 'Account Deletion Audit Logs'

    def __str__(self):
        return f"DeletionLog: {self.user_identifier} ({self.role}) at {self.deleted_at.strftime('%Y-%m-%d %H:%M')}"
