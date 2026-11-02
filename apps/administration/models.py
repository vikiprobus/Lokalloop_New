from django.db import models
from django.contrib.auth.models import User


class AdminAction(models.Model):
    admin = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='admin_actions')
    action_type = models.CharField(max_length=100)
    target_model = models.CharField(max_length=50)
    target_id = models.CharField(max_length=50)
    details = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        admin_name = self.admin.username if self.admin else 'System'
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {admin_name}: {self.action_type} on {self.target_model}#{self.target_id}"


class TicketCategory(models.TextChoices):
    REDEMPTION_ISSUE = 'REDEMPTION_ISSUE', 'Redemption Issue'
    BILLING = 'BILLING', 'Billing & Credits'
    SHOP_INQUIRY = 'SHOP_INQUIRY', 'Shop Inquiry'
    GENERAL = 'GENERAL', 'General Feedback / Support'


class TicketStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    RESOLVED = 'RESOLVED', 'Resolved'
    CLOSED = 'CLOSED', 'Closed'


class SupportTicket(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='support_tickets')
    subject = models.CharField(max_length=200)
    category = models.CharField(max_length=30, choices=TicketCategory.choices, default=TicketCategory.GENERAL)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN)
    admin_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Ticket #{self.id} [{self.get_status_display()}]: {self.subject}"


class ErrorLogLevel(models.TextChoices):
    ERROR = 'ERROR', 'Error'
    CRITICAL = 'CRITICAL', 'Critical'
    WARNING = 'WARNING', 'Warning'
    INFO = 'INFO', 'Info'


class ErrorLog(models.Model):
    incident_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    level = models.CharField(max_length=20, choices=ErrorLogLevel.choices, default=ErrorLogLevel.ERROR)
    error_type = models.CharField(max_length=150, db_index=True)
    message = models.TextField()
    traceback = models.TextField(blank=True, default='')
    path = models.CharField(max_length=500, blank=True, default='')
    method = models.CharField(max_length=10, blank=True, default='GET')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='error_logs')
    user_agent = models.CharField(max_length=500, blank=True, default='')
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.level}] {self.error_type}: {self.message[:60]} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"

    @classmethod
    def purge_old_errors(cls, days=15):
        """
        Automatically clears error logs older than the specified retention window (default: 15 days).
        Returns the number of deleted records.
        """
        from django.utils import timezone
        from datetime import timedelta
        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = cls.objects.filter(created_at__lt=cutoff_date).delete()
        return deleted_count


class PlatformSetting(models.Model):
    """
    Platform-wide governance settings configurable by Superusers.
    """
    geofence_enabled = models.BooleanField(
        default=True,
        help_text="Enforce in-store GPS proximity validation for counter QR redemptions"
    )
    max_redemption_distance_meters = models.PositiveIntegerField(
        default=50,
        help_text="Configured proximity radius in meters (e.g. 50m, 100m, 200m)"
    )
    gps_accuracy_tolerance_meters = models.PositiveIntegerField(
        default=25,
        help_text="GPS accuracy tolerance buffer for indoor drift in meters"
    )
    signup_bonus_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable initial signup welcome bonus (25 pts for buyers, 50 credits for sellers)"
    )
    support_phone = models.CharField(
        max_length=30,
        default='+91 98220 11223',
        blank=True,
        help_text="Official Merchant & Field Support Phone"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Setting"
        verbose_name_plural = "Platform Settings"

    def __str__(self):
        status = "Enabled" if self.geofence_enabled else "Disabled"
        return f"Platform Settings (Geo-Fence: {status}, Radius: {self.max_redemption_distance_meters}m + {self.gps_accuracy_tolerance_meters}m buffer)"

    @classmethod
    def get_settings(cls):
        """
        Singleton pattern accessor for platform settings.
        """
        setting, _ = cls.objects.get_or_create(id=1)
        return setting


class MerchantAgreementLog(models.Model):
    """
    Audit log of merchant digital agreements signed during onboarding or portal updates.
    Captures legal acceptance, timestamp, client IP, user agent, and agreement URL.
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='agreement_logs')
    merchant_id = models.CharField(max_length=30, blank=True, default='', db_index=True)
    business_name = models.CharField(max_length=200, blank=True, default='')
    agreement_title = models.CharField(max_length=200, default='Merchant Digital Agreement')
    agreement_url = models.CharField(max_length=255, default='http://127.0.0.1:8000/terms/')
    agreement_version = models.CharField(max_length=50, default='v1.0')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default='')
    is_accepted = models.BooleanField(default=True)
    accepted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-accepted_at']
        verbose_name = 'Merchant Digital Agreement Log'
        verbose_name_plural = 'Merchant Digital Agreement Logs'

    def __str__(self):
        return f"Agreement Log: {self.business_name} ({self.merchant_id}) - Accepted at {self.accepted_at.strftime('%Y-%m-%d %H:%M')}"


class InquiryRole(models.TextChoices):
    BUYER = 'BUYER', 'Shopper / Buyer'
    MERCHANT = 'MERCHANT', 'Merchant / Store Partner'
    BILLING = 'BILLING', 'Billing & Payment Inquiry'
    GRIEVANCE = 'GRIEVANCE', 'Statutory Grievance / Legal'
    GENERAL = 'GENERAL', 'General Inquiry'


class ContactInquiry(models.Model):
    ticket_number = models.CharField(max_length=32, unique=True, db_index=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=25, blank=True, default='')
    user_role = models.CharField(max_length=30, choices=InquiryRole.choices, default=InquiryRole.BUYER)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default='')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='contact_inquiries')
    admin_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Contact Inquiry'
        verbose_name_plural = 'Contact Inquiries'

    def __str__(self):
        return f"[{self.ticket_number}] {self.full_name} ({self.get_user_role_display()}): {self.subject}"



