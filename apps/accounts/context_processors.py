def role_context(request):
    """
    Exposes role information and common platform branding to templates.
    """
    from django.conf import settings
    default_city = getattr(settings, 'DEFAULT_CITY', 'Bengaluru')
    context = {
        'BRAND_NAME': 'LokalLoop',
        'BRAND_TAGLINE': 'Discover Local. Save More. Shop Nearby.',
        'SUPPORT_CITY': default_city,
        'DJANGO_ADMIN_URL': getattr(settings, 'DJANGO_ADMIN_URL', 'portal-admin/').strip('/') + '/',
        'is_buyer': False,
        'is_seller': False,
        'is_admin': False,
        'user_profile': None,
        'unread_notifications_count': 0,
    }

    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        context['user_profile'] = profile
        if profile:
            context['is_buyer'] = profile.is_buyer
            context['is_seller'] = profile.is_seller
            context['is_admin'] = profile.is_admin
        elif request.user.is_superuser:
            context['is_admin'] = True

        # Notification count if notifications app is available
        try:
            from apps.notifications.models import Notification
            context['unread_notifications_count'] = Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count()
        except Exception:
            pass

    return context
