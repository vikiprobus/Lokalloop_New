from django.db import models
from django.contrib.auth.models import User


class NotificationType(models.TextChoices):
    OFFER_REDEEMED = 'OFFER_REDEEMED', 'Offer Redeemed'
    POINTS_EARNED = 'POINTS_EARNED', 'Reward Points Earned'
    LOW_CREDITS = 'LOW_CREDITS', 'Low Credits Warning'
    CREDIT_PURCHASE = 'CREDIT_PURCHASE', 'Credit Purchase Successful'
    NEW_OFFER = 'NEW_OFFER', 'New Nearby Offer'
    SYSTEM = 'SYSTEM', 'System Alert'


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices, default=NotificationType.SYSTEM)
    is_read = models.BooleanField(default=False)
    link_url = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification to {self.recipient.username}: {self.title}"
