import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from apps.shops.models import Shop
from apps.offers.models import Offer


class QRCode(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name='qr_codes')
    code = models.CharField(max_length=64, unique=True, db_index=True, default=uuid.uuid4)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"QR: {self.code[:8]}... ({self.offer.title})"

    @property
    def is_expired(self):
        now = timezone.now()
        if now > self.expires_at:
            return True
        if self.offer and self.offer.end_date and now > self.offer.end_date:
            return True
        return False

    @property
    def is_valid(self):
        return not self.is_used and not self.is_expired and self.offer.is_currently_valid


class RedemptionStatus(models.TextChoices):
    PENDING_APPROVAL = 'PENDING_APPROVAL', 'Pending Counter Approval'
    SUCCESS = 'SUCCESS', 'Successful'
    CANCELLED = 'CANCELLED', 'Cancelled'


class OfferRedemption(models.Model):
    qr_code = models.ForeignKey(QRCode, on_delete=models.SET_NULL, null=True, blank=True, related_name='redemptions')
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT, related_name='redemptions')
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name='redemptions')
    buyer = models.ForeignKey(User, on_delete=models.PROTECT, related_name='buyer_redemptions')
    seller = models.ForeignKey(User, on_delete=models.PROTECT, related_name='seller_redemptions')
    
    bill_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text='Original store purchase amount in INR')
    discount_given = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text='Actual INR saved by buyer')
    
    credits_deducted = models.PositiveIntegerField(default=5)
    reward_points_earned = models.PositiveIntegerField(default=50)
    
    idempotency_key = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=RedemptionStatus.choices, default=RedemptionStatus.SUCCESS)
    rejection_reason = models.TextField(blank=True, default='', help_text='Merchant reason for rejecting redemption at counter')
    is_verified_by_merchant = models.BooleanField(default=True)
    redemption_distance_meters = models.FloatField(null=True, blank=True, help_text='Calculated distance between shopper and store at checkout in meters')
    is_geofence_verified = models.BooleanField(default=False, help_text='True if verified within configured store proximity radius')
    verification_otp = models.CharField(max_length=6, blank=True, default='', db_index=True, help_text='4-digit counter code shown on buyer screen')
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    # Anomaly & Fraud Guardrails
    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True, help_text='Client IP address during redemption initiation')
    device_token = models.CharField(max_length=128, blank=True, default='', db_index=True, help_text='Handset device token or fingerprint')

    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-redeemed_at']

    @property
    def is_expired(self):
        if self.status == RedemptionStatus.PENDING_APPROVAL and self.otp_expires_at:
            return timezone.now() > self.otp_expires_at
        return False

    def __str__(self):
        return f"Redemption #{self.id} - {self.offer.title} by {self.buyer.username}"
