from django.db import models
from apps.shops.models import Shop


class ShopMetric(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='daily_metrics')
    date = models.DateField()
    views_count = models.PositiveIntegerField(default=0)
    redemptions_count = models.PositiveIntegerField(default=0)
    estimated_sales_inr = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    class Meta:
        unique_together = ('shop', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.shop.name} Metric ({self.date})"
