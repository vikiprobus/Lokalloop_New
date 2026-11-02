from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied


def buyer_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            messages.info(request, "Please log in to continue as a Buyer.")
            return redirect(f"/buyer/login/?next={request.get_full_path()}")
        
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_buyer or not profile.is_active:
            messages.error(request, "Access restricted: Buyer account required.")
            return redirect('/')
        
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def seller_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            messages.info(request, "Please log in to access your Seller Portal.")
            return redirect(f"/seller/login/?next={request.get_full_path()}")
        
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_seller or not profile.is_active:
            messages.error(request, "Access restricted: Seller account required.")
            return redirect('/')
        
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def verified_seller_required(view_func):
    """
    Decorator for views that require an approved/verified seller account.
    If the seller is unapproved or pending compliance verification, redirects
    to /seller/dashboard/ with an informative warning message:
    'This feature is locked while your account is pending verification.'
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            messages.info(request, "Please log in to access your Seller Portal.")
            return redirect(f"/seller/login/?next={request.get_full_path()}")
        
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_seller or not profile.is_active:
            messages.error(request, "Access restricted: Seller account required.")
            return redirect('/')
        
        seller_profile = getattr(request.user, 'seller_profile', None)
        is_verified = bool(seller_profile and seller_profile.is_verified)
        
        has_approved_shop = False
        if hasattr(request.user, 'shops'):
            has_approved_shop = request.user.shops.filter(is_approved=True).exists()
            
        if not (is_verified or has_approved_shop):
            messages.warning(request, "This feature is locked while your account is pending verification.")
            return redirect('/seller/dashboard/')
            
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            messages.info(request, "Please sign in with administrator credentials.")
            return redirect(f"/admin-login/?next={request.get_full_path()}")
        
        profile = getattr(request.user, 'profile', None)
        is_admin = ((profile and profile.is_admin) or request.user.is_superuser or request.user.is_staff) and request.user.is_active
        if not is_admin:
            raise PermissionDenied("Access restricted: Platform Administrator access required.")
        
        return view_func(request, *args, **kwargs)
    return _wrapped_view
