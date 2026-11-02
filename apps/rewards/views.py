from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import buyer_required
from services.leaderboard_service import get_weekly_leaderboard


@buyer_required
def buyer_leaderboard_view(request):
    """
    Weekly Shopper Leaderboard:
    - 7-day cycle ranking based on points earned.
    - Displays Gennie Nicknames, Public Unique IDs, and Gold/Silver/Bronze badges.
    - Zero personal info shown on frontend (strictly privacy-preserving).
    - Highlights 3-Cycle Anti-Monopoly Cooldown for past Grand Prize winners.
    """
    data = get_weekly_leaderboard(cycle_days=7)

    # Check if the logged-in buyer is in the top leaderboard
    current_user_entry = None
    for item in data['leaderboard']:
        if item['buyer_id'] == request.user.id:
            current_user_entry = item
            break

    # BUG-74: If buyer is outside top 100, calculate their exact cycle rank
    if not current_user_entry and request.user.is_authenticated:
        from services.leaderboard_service import get_buyer_current_cycle_rank
        current_user_entry = get_buyer_current_cycle_rank(request.user, cycle_days=7)

    context = {
        'cycle_number': data['cycle_number'],
        'cycle_start': data['cycle_start'],
        'cycle_end': data['cycle_end'],
        'leaderboard': data['leaderboard'],
        'total_participants': data['total_participants'],
        'history_records': data['history_records'],
        'current_user_entry': current_user_entry,
    }
    return render(request, 'buyer/leaderboard.html', context)
