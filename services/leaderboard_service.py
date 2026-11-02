"""
LokalLoop Gamification & Anti-Abuse Safeguards:
- Weekly Shopper Leaderboard with 7-day cycle ranking.
- 3-Cycle Anti-Monopoly Cooldown: Grand Prize winners cannot repeat-win for 3 cycles.
- Anomaly & IP Fraud Guardrails: Auto-flag and rate-limit multi-account clusters from same IP.
- Seller Loyalty Perks: Automated store prominence unlocks at 50 & 100 points.
"""
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum
from django.contrib.auth.models import User

from apps.rewards.models import RewardTransaction, LeaderboardHistory
from apps.wallets.models import SellerWallet
from apps.shops.models import Shop


def get_current_cycle_number() -> int:
    """
    Returns the current active weekly cycle number.
    """
    latest = LeaderboardHistory.objects.order_by('-cycle_number').first()
    return (latest.cycle_number + 1) if latest else 1


def is_buyer_in_cooldown(buyer_user, current_cycle: int = None) -> tuple[bool, int | None]:
    """
    3-Cycle Cooldown Rule (Anti-Monopoly & Anti-Botting):
    If a buyer wins Rank #1 in a cycle, they remain visible on the leaderboard for honor/prestige,
    but cannot claim the grand weekly prize for the next 3 cycles.
    
    Returns: (is_cooldown_active: bool, last_won_cycle: int or None)
    """
    if current_cycle is None:
        current_cycle = get_current_cycle_number()

    # Look for Rank 1 wins or Grand Prize awards in the preceding 3 cycles (BUG-79)
    from django.db.models import Q
    min_cooldown_cycle = max(1, current_cycle - 3)
    recent_win = LeaderboardHistory.objects.filter(
        winner=buyer_user,
        cycle_number__gte=min_cooldown_cycle,
        cycle_number__lt=current_cycle
    ).filter(
        Q(rank=1) | Q(notes__icontains="Grand Prize")
    ).order_by('-cycle_number').first()

    if recent_win:
        return True, recent_win.cycle_number
    return False, None


def get_weekly_leaderboard(cycle_days: int = 7, limit: int = 100) -> dict:
    """
    Calculates weekly rank of buyers based on points earned within the current cycle.
    Privacy Guarantee: Never leaks personal names, emails, or phone numbers. Only Gennie Nicknames
    and Public Unique IDs are returned.
    """
    from apps.rewards.models import RewardType
    from django.db.models import Min
    now = timezone.now()
    rolling_start = now - timedelta(days=cycle_days)
    latest_history = LeaderboardHistory.objects.order_by('-cycle_number').first()
    if latest_history and latest_history.cycle_end:
        cycle_start = latest_history.cycle_end
    else:
        cycle_start = rolling_start

    current_cycle = get_current_cycle_number()
    cycle_scheduled_end = cycle_start + timedelta(days=cycle_days)

    # Aggregate genuine shopping reward points earned within current cycle (exclude welcome bonus & adjustments)
    # Strictly filter active buyers to exclude deleted accounts
    # Tie-breaker (BUG-70): Earliest transaction timestamp within current cycle
    points_aggregation_qs = (
        RewardTransaction.objects.filter(
            created_at__gte=cycle_start,
            points__gt=0,
            transaction_type__in=[RewardType.REDEMPTION_REWARD, RewardType.BUMPER_BONUS],
            account__buyer__is_active=True
        )
        .values('account__buyer')
        .annotate(
            total_points=Sum('points'),
            earliest_tx=Min('created_at')
        )
        .order_by('-total_points', 'earliest_tx', 'account__buyer')
    )
    # BUG-120: Calculate unsliced total participant count
    total_participants = points_aggregation_qs.count()
    points_aggregation = list(points_aggregation_qs[:limit])

    leaderboard = []
    grand_prize_assigned = False

    # Batch load all candidate active buyers and cooldown history to prevent N+1 queries (BUG-10)
    buyer_ids = [entry['account__buyer'] for entry in points_aggregation if entry.get('account__buyer')]
    users_by_id = User.objects.filter(id__in=buyer_ids, is_active=True).select_related('buyer_profile').in_bulk()

    from django.db.models import Q
    min_cooldown_cycle = max(1, current_cycle - 3)
    recent_wins = LeaderboardHistory.objects.filter(
        winner_id__in=buyer_ids,
        cycle_number__gte=min_cooldown_cycle,
        cycle_number__lt=current_cycle
    ).filter(
        Q(rank=1) | Q(notes__icontains="Grand Prize")
    ).order_by('-cycle_number')
    recent_wins_by_buyer = {}
    for rw in recent_wins:
        if rw.winner_id not in recent_wins_by_buyer:
            recent_wins_by_buyer[rw.winner_id] = rw.cycle_number

    for rank_idx, entry in enumerate(points_aggregation, start=1):
        buyer_id = entry['account__buyer']
        total_points = entry['total_points']

        buyer = users_by_id.get(buyer_id)
        if not buyer or not buyer.is_active:
            continue

        bp = getattr(buyer, 'buyer_profile', None)
        # Privacy-Preserving Display Identity
        nickname = (bp.nickname if bp and bp.nickname else f"Shopper-{str(buyer.id).zfill(4)}")
        public_id = (bp.public_unique_id if bp and bp.public_unique_id else f"LL-{str(buyer.id).zfill(4)}")

        # 3-Cycle Cooldown Check
        last_won_cycle = recent_wins_by_buyer.get(buyer.id)
        in_cooldown = last_won_cycle is not None

        # Determine Grand Prize Eligibility
        is_grand_prize_winner = False
        if not grand_prize_assigned and not in_cooldown:
            is_grand_prize_winner = True
            grand_prize_assigned = True

        # Assign Badge
        if rank_idx == 1:
            badge_icon = "🥇"
            badge_tier = "Gold Champion"
        elif rank_idx == 2:
            badge_icon = "🥈"
            badge_tier = "Silver Runner-Up"
        elif rank_idx == 3:
            badge_icon = "🥉"
            badge_tier = "Bronze Contender"
        elif rank_idx <= 10:
            badge_icon = "⭐"
            badge_tier = "Top 10 Star"
        else:
            badge_icon = "✨"
            badge_tier = "Rising Shopper"

        leaderboard.append({
            'rank': rank_idx,
            'buyer_id': buyer.id,
            'gennie_nickname': nickname,
            'public_unique_id': public_id,
            'points_earned': total_points,
            'badge_icon': badge_icon,
            'badge_tier': badge_tier,
            'in_cooldown': in_cooldown,
            'last_won_cycle': last_won_cycle,
            'is_grand_prize_winner': is_grand_prize_winner,
        })

    # Historical Champions
    recent_history = LeaderboardHistory.objects.select_related(
        'winner', 'winner__buyer_profile'
    ).order_by('-cycle_number', 'rank')[:12]

    history_records = []
    for h in recent_history:
        h_bp = getattr(h.winner, 'buyer_profile', None)
        h_nick = h_bp.nickname if h_bp and h_bp.nickname else f"Shopper-{str(h.winner.id).zfill(4)}"
        h_pub_id = h_bp.public_unique_id if h_bp and h_bp.public_unique_id else f"LL-{str(h.winner.id).zfill(4)}"
        history_records.append({
            'cycle_number': h.cycle_number,
            'rank': h.rank,
            'gennie_nickname': h_nick,
            'public_unique_id': h_pub_id,
            'points_scored': h.points_scored,
            'cycle_end': h.cycle_end,
        })

    return {
        'cycle_number': current_cycle,
        'cycle_start': cycle_start,
        'cycle_end': cycle_scheduled_end,
        'leaderboard': leaderboard,
        'total_participants': total_participants,
        'history_records': history_records,
    }


def record_cycle_winner(cycle_number: int, winner_user, points_scored: int, rank: int = 1, notes: str = "", cycle_start=None, cycle_end=None) -> LeaderboardHistory:
    """
    Records a cycle podium finisher in LeaderboardHistory to enforce future 3-cycle cooldowns and Hall of Fame.
    """
    now = timezone.now()
    if cycle_end is None:
        cycle_end = now
    if cycle_start is None:
        latest = LeaderboardHistory.objects.filter(cycle_number__lt=cycle_number).order_by('-cycle_number').first()
        cycle_start = latest.cycle_end if latest else (cycle_end - timedelta(days=7))

    return LeaderboardHistory.objects.create(
        cycle_number=cycle_number,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        winner=winner_user,
        points_scored=points_scored,
        rank=rank,
        notes=notes or f"Rank #{rank} for Weekly Cycle #{cycle_number}"
    )


def rollover_weekly_leaderboard_cycle(force: bool = False) -> tuple[bool, str, LeaderboardHistory | None]:
    """
    Automated Weekly Leaderboard Cycle Rollover:
    Evaluates top shoppers for the ending cycle, determines the Grand Prize
    winner (enforcing 3-Cycle Anti-Monopoly Cooldown), commits LeaderboardHistory for podium finishers (Rank 1, 2, 3),
    dispatches in-app winner notifications, and automatically advances cycle_number.
    """
    from django.db import transaction
    from services.notification_service import create_notification
    from apps.notifications.models import NotificationType

    current_cycle = get_current_cycle_number()
    latest_history = LeaderboardHistory.objects.order_by('-cycle_number').first()

    # BUG-73: Prevent premature cycle rollover if less than 6 days have elapsed (unless force=True)
    if latest_history and latest_history.cycle_end and not force:
        elapsed = timezone.now() - latest_history.cycle_end
        if elapsed < timedelta(days=6):
            hours_left = max(1, int((timedelta(days=6) - elapsed).total_seconds() / 3600))
            return False, f"Weekly Cycle #{current_cycle} is still active ({hours_left}h remaining). Rollover locked to prevent premature cycle termination.", None

    with transaction.atomic():
        data = get_weekly_leaderboard(cycle_days=7)
        leaderboard = data.get('leaderboard', [])

        if not leaderboard:
            return False, f"No participant transactions in Weekly Cycle #{current_cycle}.", None

        # Find the eligible Grand Prize Winner (highest points not in 3-cycle cooldown)
        grand_prize_entry = None
        for entry in leaderboard:
            if entry.get('is_grand_prize_winner'):
                grand_prize_entry = entry
                break

        # Strictly enforce 3-cycle cooldown: If all participants are in cooldown, do NOT bypass cooldown
        if not grand_prize_entry:
            eligible_entries = [e for e in leaderboard if not e.get('in_cooldown')]
            if eligible_entries:
                grand_prize_entry = eligible_entries[0]
            else:
                return False, f"All active participants in Cycle #{current_cycle} are currently in 3-cycle cooldown. No grand prize awarded.", None

        winner_id = grand_prize_entry['buyer_id']
        winner_user = User.objects.filter(pk=winner_id).first()
        if not winner_user:
            return False, "Winner user record could not be resolved.", None

        cycle_now = timezone.now()
        history_record = None
        recorded_user_ids = set()

        # BUG-69: Record podium positions (Ranks 1, 2, 3) without duplicate records
        for pos_idx, entry in enumerate(leaderboard[:3], start=1):
            user_rec = User.objects.filter(pk=entry['buyer_id']).first()
            if not user_rec or user_rec.id in recorded_user_ids:
                continue
            recorded_user_ids.add(user_rec.id)

            pts = entry.get('points_earned', 0)
            is_gp_winner = (user_rec.id == winner_user.id)
            if pos_idx == 1:
                label = "Gold Champion" + (" & Grand Prize Winner" if is_gp_winner else " (In Cooldown - Prestige)")
            elif pos_idx == 2:
                label = "Silver Runner-Up" + (" & Awarded Grand Prize" if is_gp_winner else "")
            else:
                label = "Bronze Contender" + (" & Awarded Grand Prize" if is_gp_winner else "")

            h = record_cycle_winner(
                cycle_number=current_cycle,
                winner_user=user_rec,
                points_scored=pts,
                rank=pos_idx,
                notes=f"{label} for Weekly Cycle #{current_cycle} ({pts} Pts)",
                cycle_start=data.get('cycle_start'),
                cycle_end=cycle_now
            )
            if is_gp_winner and not history_record:
                history_record = h
            elif pos_idx == 1 and not history_record:
                history_record = h

            badge_icon = "🥇" if pos_idx == 1 else ("🥈" if pos_idx == 2 else "🥉")
            if is_gp_winner:
                create_notification(
                    recipient=user_rec,
                    title="🏆 You Won the Weekly Shopper Grand Prize!",
                    message=f"Congratulations! You scored {pts} points and won Weekly Leaderboard Cycle #{current_cycle}!",
                    notification_type=NotificationType.SYSTEM,
                    link_url="/buyer/leaderboard/"
                )
            else:
                create_notification(
                    recipient=user_rec,
                    title=f"{badge_icon} Weekly Leaderboard Podium Finish!",
                    message=f"Congratulations! You scored {pts} points and finished {label} in Weekly Cycle #{current_cycle}!",
                    notification_type=NotificationType.SYSTEM,
                    link_url="/buyer/leaderboard/"
                )

        # BUG-102: If the eligible Grand Prize winner is outside top 3 podium (e.g., Rank 4+ due to cooldown on ranks 1-3),
        # ensure they are explicitly recorded in LeaderboardHistory so cooldown and rewards persist.
        if winner_user.id not in recorded_user_ids:
            pts = grand_prize_entry.get('points_earned', 0)
            winner_rank = grand_prize_entry.get('rank', 4)
            h = record_cycle_winner(
                cycle_number=current_cycle,
                winner_user=winner_user,
                points_scored=pts,
                rank=winner_rank,
                notes=f"Grand Prize Winner (Rank {winner_rank}) for Weekly Cycle #{current_cycle} ({pts} Pts)",
                cycle_start=data.get('cycle_start'),
                cycle_end=cycle_now
            )
            recorded_user_ids.add(winner_user.id)
            history_record = h

            create_notification(
                recipient=winner_user,
                title="🏆 You Won the Weekly Shopper Grand Prize!",
                message=f"Congratulations! You scored {pts} points and won Weekly Leaderboard Cycle #{current_cycle}!",
                notification_type=NotificationType.SYSTEM,
                link_url="/buyer/leaderboard/"
            )

        points_scored = grand_prize_entry.get('points_earned', 0)
        return True, f"Successfully rolled over Weekly Cycle #{current_cycle}. Winner: {winner_user.username} ({points_scored} pts).", history_record


def get_buyer_current_cycle_rank(buyer_user, cycle_days: int = 7) -> dict | None:
    """
    BUG-74: Calculates the exact leaderboard position of a specific buyer within the current cycle,
    even if they fall outside the top 100 limit.
    """
    if not buyer_user or not buyer_user.is_active:
        return None

    from apps.rewards.models import RewardType
    from django.db.models import Sum, Min

    now = timezone.now()
    rolling_start = now - timedelta(days=cycle_days)
    latest_history = LeaderboardHistory.objects.order_by('-cycle_number').first()
    cycle_start = latest_history.cycle_end if (latest_history and latest_history.cycle_end) else rolling_start

    user_points = (
        RewardTransaction.objects.filter(
            account__buyer=buyer_user,
            created_at__gte=cycle_start,
            points__gt=0,
            transaction_type__in=[RewardType.REDEMPTION_REWARD, RewardType.BUMPER_BONUS]
        ).aggregate(total=Sum('points'))['total'] or 0
    )

    if user_points <= 0:
        return None

    # First transaction timestamp for tie-breaker
    user_first_tx = (
        RewardTransaction.objects.filter(
            account__buyer=buyer_user,
            created_at__gte=cycle_start,
            points__gt=0,
            transaction_type__in=[RewardType.REDEMPTION_REWARD, RewardType.BUMPER_BONUS]
        ).aggregate(earliest=Min('created_at'))['earliest']
    )

    # Count how many active buyers have higher points (or equal points with earlier first_tx)
    higher_aggregates = (
        RewardTransaction.objects.filter(
            created_at__gte=cycle_start,
            points__gt=0,
            transaction_type__in=[RewardType.REDEMPTION_REWARD, RewardType.BUMPER_BONUS],
            account__buyer__is_active=True
        )
        .exclude(account__buyer=buyer_user)
        .values('account__buyer')
        .annotate(
            total=Sum('points'),
            earliest=Min('created_at')
        )
    )

    higher_count = sum(
        1 for agg in higher_aggregates
        if (agg['total'] > user_points) or (
            agg['total'] == user_points and agg['earliest'] and user_first_tx and agg['earliest'] < user_first_tx
        )
    )
    calculated_rank = higher_count + 1

    bp = getattr(buyer_user, 'buyer_profile', None)
    nickname = bp.nickname if bp and bp.nickname else f"Shopper-{str(buyer_user.id).zfill(4)}"
    public_id = bp.public_unique_id if bp and bp.public_unique_id else f"LL-{str(buyer_user.id).zfill(4)}"

    is_cooldown, last_won = is_buyer_in_cooldown(buyer_user)

    if calculated_rank == 1:
        badge_icon = "🥇"
        badge_tier = "Gold Champion"
    elif calculated_rank == 2:
        badge_icon = "🥈"
        badge_tier = "Silver Runner-Up"
    elif calculated_rank == 3:
        badge_icon = "🥉"
        badge_tier = "Bronze Contender"
    elif calculated_rank <= 10:
        badge_icon = "⭐"
        badge_tier = "Top 10 Star"
    else:
        badge_icon = "✨"
        badge_tier = "Rising Shopper"

    return {
        'rank': calculated_rank,
        'buyer_id': buyer_user.id,
        'gennie_nickname': nickname,
        'public_unique_id': public_id,
        'points_earned': user_points,
        'badge_icon': badge_icon,
        'badge_tier': badge_tier,
        'in_cooldown': is_cooldown,
        'last_won_cycle': last_won,
        'is_grand_prize_winner': False,
    }


def check_and_unlock_seller_loyalty_perks(seller_wallet: SellerWallet) -> list[str]:
    """
    Seller Loyalty Tier Unlocks:
    Tracks accumulated merchant loyalty points (1 point per successful counter redemption).
    - Reaching 50 Points: Automatically grants 1-week "Featured / Priority Store" badge on Homepage.
    - Reaching 100 Points: Automatically grants 2-week "Top Ranked Outlet" spotlight.
    """
    now = timezone.now()
    points = seller_wallet.loyalty_points
    unlocked_perks = []

    shops = Shop.objects.filter(seller=seller_wallet.seller)
    if not shops.exists():
        return unlocked_perks

    if points >= 100:
        spotlight_expiry = now + timedelta(days=14)
        for s in shops:
            # Grant or refresh only if currently not spotlighted or existing spotlight has expired
            if not s.is_spotlight or not s.spotlight_until or s.spotlight_until < now:
                s.is_spotlight = True
                s.spotlight_until = spotlight_expiry
            if not s.is_featured or not s.featured_until or s.featured_until < now:
                s.is_featured = True
                s.featured_until = spotlight_expiry
            s.save(update_fields=['is_spotlight', 'spotlight_until', 'is_featured', 'featured_until'])
        unlocked_perks.append("Top Ranked Outlet Spotlight (2 Weeks)")
    elif points >= 50:
        featured_expiry = now + timedelta(days=7)
        for s in shops:
            # Grant only if currently not featured or existing featured perk has expired
            if not s.is_featured or not s.featured_until or s.featured_until < now:
                s.is_featured = True
                s.featured_until = featured_expiry
                s.save(update_fields=['is_featured', 'featured_until'])
        unlocked_perks.append("Featured / Priority Store Badge (1 Week)")

    return unlocked_perks


def check_ip_fraud_guardrail(client_ip: str, current_buyer_id: int) -> tuple[bool, str]:
    """
    Anomaly & IP Fraud Guardrails:
    Auto-flags and rate-limits if more than 3 distinct accounts redeem from the same IP within 10 minutes.
    """
    if not client_ip:
        return True, ""

    # Allow local development IPs
    if client_ip in ['127.0.0.1', '::1', 'localhost', 'testclient']:
        return True, ""

    from apps.redemptions.models import OfferRedemption
    from apps.administration.models import AdminAction

    cutoff_10m = timezone.now() - timedelta(minutes=10)
    distinct_accounts_count = (
        OfferRedemption.objects.filter(
            ip_address=client_ip,
            redeemed_at__gte=cutoff_10m
        )
        .exclude(buyer_id=current_buyer_id)
        .values_list('buyer_id', flat=True)
        .distinct()
        .count()
    )

    if distinct_accounts_count >= 3:
        # Auto-flag security anomaly in audit log
        try:
            AdminAction.objects.create(
                action_type="SECURITY_ANOMALY_IP_CLUSTER",
                target_model="OfferRedemption",
                target_id=str(current_buyer_id),
                details=f"IP Cluster Guardrail: Over 3 distinct accounts redeemed from IP {client_ip} in 10 minutes. Redemption blocked.",
                ip_address=client_ip
            )
        except Exception:
            pass

        return False, "Rate limit exceeded: Multiple account redemptions detected from this network. Please try again later."

    return True, ""
