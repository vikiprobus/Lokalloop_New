import logging
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Count
from apps.administration.models import ErrorLog, AdminAction
from apps.redemptions.models import QRCode
from apps.analytics.models import ShopMetric
from apps.shops.models import Shop
from apps.redemptions.models import OfferRedemption, RedemptionStatus

logger = logging.getLogger('background_tasks')


def purge_old_error_logs_job(days=15, user=None):
    """
    Purges error log entries older than the specified retention days.
    Records the outcome in AdminAction audit log.
    """
    cutoff = timezone.now() - timedelta(days=days)
    deleted_count, _ = ErrorLog.objects.filter(created_at__lt=cutoff).delete()
    
    details = f"Purged {deleted_count} error log records older than {days} days (cutoff: {cutoff.strftime('%Y-%m-%d %H:%M')})."
    
    AdminAction.objects.create(
        admin=user,
        action_type="CRON_PURGE_ERROR_LOGS",
        target_model="ErrorLog",
        target_id=str(deleted_count),
        details=details
    )
    
    return {
        'status': 'success',
        'task': 'purge_old_error_logs',
        'deleted_count': deleted_count,
        'details': details
    }


def expire_stale_qrcodes_job(user=None):
    """
    Finds and deactivates expired un-used QR codes, stale pending counter redemptions,
    and bulk expires outdated or quota-exhausted promotional campaigns.
    Marks them as used/cancelled/expired so they cannot be redeemed after validity expiration.
    """
    from apps.redemptions.models import RedemptionStatus
    from apps.offers.models import Offer, OfferStatus
    from django.db.models import Q, F
    now = timezone.now()
    # 1. Deactivate QR codes that are not used but their expires_at has passed
    stale_qrs = QRCode.objects.filter(is_used=False, expires_at__lt=now)
    expired_qr_count = stale_qrs.count()
    if expired_qr_count > 0:
        stale_qrs.update(is_used=True)

    # 2. Deactivate pending redemptions whose OTP expiration has passed
    stale_redemptions = OfferRedemption.objects.filter(
        status=RedemptionStatus.PENDING_APPROVAL,
        otp_expires_at__lt=now
    )
    expired_redemptions_count = stale_redemptions.count()
    if expired_redemptions_count > 0:
        stale_redemptions.update(status=RedemptionStatus.CANCELLED)

    # 3. Bulk update active or paused campaigns that have expired or reached max redemptions (BUG-95)
    expired_offers_qs = Offer.objects.filter(
        status__in=[OfferStatus.ACTIVE, OfferStatus.PAUSED]
    ).filter(
        Q(end_date__lt=now) | Q(current_redemptions__gte=F('max_redemptions'))
    )
    expired_offers_count = expired_offers_qs.count()
    if expired_offers_count > 0:
        expired_offers_qs.update(status=OfferStatus.EXPIRED)

    # 3b. Auto-activate scheduled upcoming campaigns whose start_date has arrived (BUG-77)
    activated_offers_qs = Offer.objects.filter(
        status=OfferStatus.UPCOMING,
        start_date__lte=now,
        end_date__gt=now,
        current_redemptions__lt=F('max_redemptions')
    )
    activated_offers_count = activated_offers_qs.count()
    if activated_offers_count > 0:
        activated_offers_qs.update(status=OfferStatus.ACTIVE)

    # 4. Sweep expired merchant loyalty perks (Featured & Spotlight stores)
    from apps.shops.models import Shop
    expired_featured_qs = Shop.objects.filter(is_featured=True, featured_until__lt=now)
    expired_featured_count = expired_featured_qs.count()
    if expired_featured_count > 0:
        expired_featured_qs.update(is_featured=False)

    expired_spotlight_qs = Shop.objects.filter(is_spotlight=True, spotlight_until__lt=now)
    expired_spotlight_count = expired_spotlight_qs.count()
    if expired_spotlight_count > 0:
        expired_spotlight_qs.update(is_spotlight=False)

    # 5. Sweep expired active reward catalog claims (BUG-123)
    from apps.rewards.models import BuyerRewardClaim
    expired_claims_qs = BuyerRewardClaim.objects.filter(status='ACTIVE', expires_at__lt=now)
    expired_claims_count = expired_claims_qs.count()
    if expired_claims_count > 0:
        expired_claims_qs.update(status='EXPIRED')

    total_swept = (
        expired_qr_count + expired_redemptions_count +
        expired_offers_count + expired_featured_count + expired_spotlight_count +
        expired_claims_count
    )

    details = (
        f"Scanned and deactivated {expired_qr_count} expired counter QR codes, "
        f"cancelled {expired_redemptions_count} stale pending redemptions, "
        f"expired {expired_offers_count} outdated/exhausted campaigns, "
        f"reset {expired_featured_count} featured / {expired_spotlight_count} spotlight expired store perks, and "
        f"expired {expired_claims_count} stale reward catalog claims "
        f"(timestamp: {now.strftime('%Y-%m-%d %H:%M')})."
    )

    AdminAction.objects.create(
        admin=user,
        action_type="CRON_EXPIRE_STALE_QRCODES",
        target_model="QRCode",
        target_id=str(total_swept),
        details=details
    )

    return {
        'status': 'success',
        'task': 'expire_stale_qrcodes',
        'expired_count': expired_qr_count,
        'expired_redemptions_count': expired_redemptions_count,
        'expired_offers_count': expired_offers_count,
        'activated_offers_count': activated_offers_count,
        'expired_featured_count': expired_featured_count,
        'expired_spotlight_count': expired_spotlight_count,
        'expired_claims_count': expired_claims_count,
        'details': details
    }


def sync_daily_analytics_job(target_date=None, user=None):
    """
    Aggregates verified counter redemptions and estimated sales into ShopMetric records per merchant.
    Defaults to today's date if not specified.
    """
    if target_date is None:
        target_date = timezone.localdate()

    shops = Shop.objects.filter(is_approved=True)
    synced_shops_count = 0
    total_redemptions = 0
    total_sales = 0.0

    for shop in shops:
        # Redemptions on target_date for this shop (strictly verified successful redemptions, BUG-97)
        from django.db.models.functions import Coalesce
        redemptions_qs = OfferRedemption.objects.filter(
            shop=shop,
            status=RedemptionStatus.SUCCESS
        ).annotate(
            final_date=Coalesce('verified_at__date', 'redeemed_at__date')
        ).filter(
            final_date=target_date
        )
        
        count = redemptions_qs.count()
        sales_agg = redemptions_qs.aggregate(total_bill=Sum('bill_amount'))['total_bill'] or 0.00
        
        metric, created = ShopMetric.objects.get_or_create(
            shop=shop,
            date=target_date,
            defaults={
                'views_count': 0,
                'redemptions_count': count,
                'estimated_sales_inr': sales_agg,
            }
        )
        if not created:
            metric.redemptions_count = count
            metric.estimated_sales_inr = sales_agg
            metric.save(update_fields=['redemptions_count', 'estimated_sales_inr'])
        synced_shops_count += 1
        total_redemptions += count
        total_sales += float(sales_agg)

    details = (
        f"Synced daily analytics for date {target_date}: {synced_shops_count} stores updated, "
        f"{total_redemptions} total redemptions recorded, INR {total_sales:.2f} total estimated checkout volume."
    )

    AdminAction.objects.create(
        admin=user,
        action_type="CRON_SYNC_DAILY_ANALYTICS",
        target_model="ShopMetric",
        target_id=str(synced_shops_count),
        details=details
    )

    return {
        'status': 'success',
        'task': 'sync_daily_analytics',
        'date': str(target_date),
        'synced_shops_count': synced_shops_count,
        'total_redemptions': total_redemptions,
        'total_sales': total_sales,
        'details': details
    }


def rollover_leaderboard_cycle_job(user=None):
    """
    Automated background cron task:
    Closes the completed 7-day shopper leaderboard cycle, resolves the Grand Prize
    winner with 3-Cycle Anti-Monopoly Cooldown guardrails, records LeaderboardHistory,
    and advances the platform cycle number.
    """
    from services.leaderboard_service import rollover_weekly_leaderboard_cycle

    success, msg, history = rollover_weekly_leaderboard_cycle()
    details = f"Leaderboard Cycle Rollover Cron: {msg}"

    AdminAction.objects.create(
        admin=user,
        action_type="CRON_ROLLOVER_LEADERBOARD_CYCLE",
        target_model="LeaderboardHistory",
        target_id=str(history.id) if history else "0",
        details=details
    )

    return {
        'status': 'success' if success else 'skipped',
        'task': 'rollover_leaderboard_cycle',
        'details': details,
        'cycle_number': history.cycle_number if history else None,
        'winner': history.winner.username if history else None
    }
