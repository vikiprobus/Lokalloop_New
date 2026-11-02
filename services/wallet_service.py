from django.db import transaction
from django.core.exceptions import ValidationError
from apps.wallets.models import SellerWallet, WalletTransaction, TransactionType, CreditPackage, CreditPurchase


def get_or_create_wallet(seller_user) -> SellerWallet:
    from apps.administration.models import PlatformSetting
    settings_obj = PlatformSetting.get_settings()
    bonus_enabled = getattr(settings_obj, 'signup_bonus_enabled', True)
    starter_credits = 50 if bonus_enabled else 0

    wallet, created = SellerWallet.objects.get_or_create(
        seller=seller_user,
        defaults={'balance_credits': starter_credits, 'lifetime_purchased': starter_credits, 'lifetime_consumed': 0}
    )
    if created and starter_credits > 0:
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type=TransactionType.BONUS,
            credits=starter_credits,
            balance_after=starter_credits,
            reference_id='WELCOME_BONUS',
            notes='Initial promotional credits welcome gift'
        )
    return wallet


@transaction.atomic
def credit_wallet(seller_user, amount_credits: int, transaction_type: str, reference_id: str, notes: str) -> SellerWallet:
    """
    Safely credits seller wallet and logs ledger transaction.
    """
    if amount_credits <= 0:
        raise ValueError("Credit amount must be greater than zero.")

    # Select for update to lock the wallet row in SQL transactions (BUG-50 fix: ensure welcome bonus initialized)
    get_or_create_wallet(seller_user)
    wallet = SellerWallet.objects.select_for_update().get(seller=seller_user)

    from django.db.models import F
    SellerWallet.objects.filter(pk=wallet.pk).update(
        balance_credits=F('balance_credits') + amount_credits,
        lifetime_purchased=F('lifetime_purchased') + amount_credits
    )
    wallet.refresh_from_db()

    WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=transaction_type,
        credits=amount_credits,
        balance_after=wallet.balance_credits,
        reference_id=reference_id,
        notes=notes
    )

    return wallet


@transaction.atomic
def deduct_credits(seller_user, amount_credits: int, reference_id: str, notes: str) -> SellerWallet:
    """
    Safely deducts credits from seller wallet and logs ledger transaction.
    Raises ValidationError if insufficient balance.
    """
    if amount_credits <= 0:
        raise ValueError("Deduction amount must be greater than zero.")

    # Ensure welcome bonus initialized (BUG-50 fix)
    get_or_create_wallet(seller_user)
    wallet = SellerWallet.objects.select_for_update().get(seller=seller_user)

    if wallet.balance_credits < amount_credits:
        raise ValidationError(f"Insufficient credits. Required: {amount_credits}, Available: {wallet.balance_credits}")

    from django.db.models import F
    SellerWallet.objects.filter(pk=wallet.pk).update(
        balance_credits=F('balance_credits') - amount_credits,
        lifetime_consumed=F('lifetime_consumed') + amount_credits
    )
    wallet.refresh_from_db()

    WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=TransactionType.DEDUCTION,
        credits=-amount_credits,
        balance_after=wallet.balance_credits,
        reference_id=reference_id,
        notes=notes
    )

    return wallet


@transaction.atomic
def process_demo_package_purchase(seller_user, package_code: str, payment_ref: str) -> CreditPurchase:
    """
    Executes payment checkout / offline billing reconciliation and credits merchant wallet idempotently.
    Prevents duplicate credit issuance and unhandled IntegrityError on duplicate payment_ref (BUG-82).
    """
    existing_purchase = CreditPurchase.objects.filter(payment_reference=payment_ref).first()
    if existing_purchase:
        return existing_purchase

    package = CreditPackage.objects.filter(code=package_code, is_active=True).first()
    if not package:
        raise ValidationError(f"Credit package '{package_code}' is invalid or no longer active.")
    total_credits = package.total_credits

    from django.db import IntegrityError
    try:
        with transaction.atomic():
            purchase = CreditPurchase.objects.create(
                seller=seller_user,
                package=package,
                amount_inr=package.price_inr,
                credits_added=total_credits,
                payment_reference=payment_ref,
                status=CreditPurchase.Status.SUCCESS
            )
    except IntegrityError:
        return CreditPurchase.objects.get(payment_reference=payment_ref)

    credit_wallet(
        seller_user=seller_user,
        amount_credits=total_credits,
        transaction_type=TransactionType.PURCHASE,
        reference_id=payment_ref,
        notes=f"Purchased {package.name} (₹{package.price_inr})"
    )

    return purchase
