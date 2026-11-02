from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.rewards.models import BuyerRewardAccount, RewardTransaction, RewardType


def calculate_offer_reward_points(min_purchase) -> int:
    """
    Dynamic 1% Point Calculation:
    Calculate points as flat 1% of the offer's min_purchase (minimum bill amount set by seller).
    - ₹200 min_purchase -> 2 points
    - ₹300 min_purchase -> 3 points
    - ₹1,000 min_purchase -> 10 points
    - ₹3,000+ min_purchase -> Capped at maximum 25 points.
    """
    if not min_purchase:
        return 0
    try:
        val = Decimal(str(min_purchase))
    except Exception:
        return 0
    if val <= Decimal('0.00'):
        return 0
    points = int(val * Decimal('0.01'))
    return max(0, min(points, 25))



def get_or_create_reward_account(buyer_user) -> BuyerRewardAccount:
    from apps.administration.models import PlatformSetting
    settings_obj = PlatformSetting.get_settings()
    bonus_enabled = getattr(settings_obj, 'signup_bonus_enabled', True)
    starter_points = 25 if bonus_enabled else 0

    account, created = BuyerRewardAccount.objects.get_or_create(
        buyer=buyer_user,
        defaults={'current_points': starter_points, 'lifetime_earned': starter_points, 'lifetime_spent': 0}
    )
    if created and starter_points > 0:
        RewardTransaction.objects.create(
            account=account,
            transaction_type=RewardType.WELCOME_BONUS,
            points=starter_points,
            balance_after=starter_points,
            notes='Welcome sign-up reward bonus'
        )
    return account


@transaction.atomic
def award_points(buyer_user, points: int, transaction_type: str, redemption=None, notes: str = "") -> BuyerRewardAccount:
    """
    Safely awards loyalty reward points to buyer and logs ledger transaction.
    """
    if points <= 0:
        raise ValueError("Points to award must be positive.")

    get_or_create_reward_account(buyer_user)
    account = BuyerRewardAccount.objects.select_for_update().get(buyer=buyer_user)

    from django.db.models import F
    BuyerRewardAccount.objects.filter(pk=account.pk).update(
        current_points=F('current_points') + points,
        lifetime_earned=F('lifetime_earned') + points
    )
    account.refresh_from_db()

    RewardTransaction.objects.create(
        account=account,
        transaction_type=transaction_type,
        points=points,
        balance_after=account.current_points,
        redemption=redemption,
        notes=notes or f"Earned {points} reward points"
    )

    return account


@transaction.atomic
def spend_points(buyer_user, points: int, notes: str = "", transaction_type: str = RewardType.REDEEMED) -> BuyerRewardAccount:
    """
    Safely deducts points for discount voucher redemptions.
    """
    if points <= 0:
        raise ValueError("Points to spend must be positive.")

    get_or_create_reward_account(buyer_user)
    account = BuyerRewardAccount.objects.select_for_update().get(buyer=buyer_user)

    if account.current_points < points:
        raise ValidationError(f"Insufficient reward points. Required: {points}, Available: {account.current_points}")

    from django.db.models import F
    BuyerRewardAccount.objects.filter(pk=account.pk).update(
        current_points=F('current_points') - points,
        lifetime_spent=F('lifetime_spent') + points
    )
    account.refresh_from_db()

    RewardTransaction.objects.create(
        account=account,
        transaction_type=transaction_type,
        points=-points,
        balance_after=account.current_points,
        notes=notes or f"Redeemed {points} reward points"
    )

    return account


@transaction.atomic
def redeem_catalog_reward(buyer_user, catalog_item_id: int):
    """
    Burns points from buyer's reward ledger to claim a dynamic catalog voucher.
    Returns: (success: bool, message: str, claim: BuyerRewardClaim or None)
    """
    from apps.rewards.models import RewardCatalogItem, BuyerRewardClaim
    import uuid
    from datetime import timedelta
    from django.utils import timezone

    from django.db.models import F

    if not buyer_user or not getattr(buyer_user, 'is_authenticated', False):
        return False, "Authentication required to redeem reward.", None

    try:
        catalog_item_id = int(catalog_item_id)
    except (ValueError, TypeError):
        return False, "Invalid reward item requested.", None

    item = RewardCatalogItem.objects.select_for_update().filter(pk=catalog_item_id, is_active=True).first()
    if not item:
        return False, "This reward is no longer available in the catalog.", None

    if not item.is_in_stock or item.stock == 0 or (item.stock != -1 and item.stock <= 0):
        return False, "This reward is temporarily out of stock.", None

    account = get_or_create_reward_account(buyer_user)
    if account.current_points < item.points_required:
        return False, f"Insufficient points. You need {item.points_required} Pts (available: {account.current_points} Pts).", None

    try:
        spend_points(
            buyer_user=buyer_user,
            points=item.points_required,
            notes=f"Catalog Burn: {item.title}",
            transaction_type=RewardType.CATALOG_REDEMPTION
        )
    except ValidationError as e:
        return False, str(e), None

    if item.stock > 0:
        from django.db.models import F
        updated_rows = RewardCatalogItem.objects.filter(pk=item.pk, stock__gt=0).update(stock=F('stock') - 1)
        if updated_rows == 0:
            return False, "This reward has just gone out of stock.", None
        item.refresh_from_db(fields=['stock'])

    prefix = (item.voucher_code_prefix or "VOUCHER").strip().upper()
    claim = None
    from django.db import IntegrityError
    for attempt in range(5):
        # Generate voucher code with increased entropy (8-char hex) and collision retry (BUG-107)
        entropy = uuid.uuid4().hex[:8].upper()
        candidate_code = f"{prefix}-{entropy}"
        if not BuyerRewardClaim.objects.filter(voucher_code=candidate_code).exists():
            try:
                claim = BuyerRewardClaim.objects.create(
                    buyer=buyer_user,
                    catalog_item=item,
                    voucher_code=candidate_code,
                    points_spent=item.points_required,
                    status='ACTIVE',
                    expires_at=timezone.now() + timedelta(days=30)
                )
                break
            except IntegrityError:
                continue

    if not claim:
        unique_suffix = f"{uuid.uuid4().hex[:12].upper()}"
        claim = BuyerRewardClaim.objects.create(
            buyer=buyer_user,
            catalog_item=item,
            voucher_code=f"{prefix}-{unique_suffix}",
            points_spent=item.points_required,
            status='ACTIVE',
            expires_at=timezone.now() + timedelta(days=30)
        )

    return True, f"Successfully redeemed '{item.title}'! Voucher code: {claim.voucher_code}", claim


@transaction.atomic
def submit_bumper_bill(buyer_user, shop_id: int, bill_number: str, bill_amount, bill_image=None, bill_date=None, require_image: bool = False):
    """
    Submits a high-value purchase bill (₹10,000 to ₹1,00,000) for bumper verification.
    Enforces anti-self-dealing guardrail preventing merchants from submitting
    bills for their own store outlets.
    Enforces receipt image requirements, purchase date validity (past dates only),
    and maximum cap of ₹1,00,000.
    Atomic transaction and database unique constraint prevent concurrent double-submissions (BUG-56).
    """
    from datetime import date, datetime
    from decimal import Decimal
    from django.db import IntegrityError
    from django.utils import timezone
    from apps.shops.models import Shop
    from apps.rewards.models import BumperBillSubmission, BumperBillStatus

    try:
        amount = Decimal(str(bill_amount))
    except Exception:
        return False, "Invalid bill amount specified.", None

    if amount < Decimal('10000.00'):
        return False, "Bumper reward submissions require a minimum bill amount of ₹10,000.", None

    if amount > Decimal('100000.00'):
        return False, "Bumper reward submissions allow a maximum bill amount of ₹1,00,000 (₹1 Lakh).", None

    bill_number = (bill_number or '').strip()
    if not bill_number:
        return False, "Bill / Invoice number is required.", None

    if require_image and not bill_image:
        return False, "Receipt image / snapshot is required for verification.", None

    # Parse and validate purchase date (Bug B5: past dates allowed, future dates blocked)
    parsed_bill_date = None
    if bill_date:
        if isinstance(bill_date, str):
            bill_date_str = bill_date.strip()
            if bill_date_str:
                try:
                    parsed_bill_date = datetime.strptime(bill_date_str, '%Y-%m-%d').date()
                except ValueError:
                    try:
                        parsed_bill_date = datetime.strptime(bill_date_str, '%d-%m-%Y').date()
                    except ValueError:
                        return False, "Invalid purchase date format. Please use YYYY-MM-DD.", None
        elif isinstance(bill_date, (date, datetime)):
            parsed_bill_date = bill_date if isinstance(bill_date, date) else bill_date.date()

    if parsed_bill_date:
        today = timezone.localdate()
        if parsed_bill_date > today:
            return False, "Bill purchase date cannot be in the future.", None

    shop = Shop.objects.filter(pk=shop_id, is_approved=True).first()
    if not shop:
        return False, "Selected partner outlet is not recognized or not verified.", None

    # Strict Anti-Self-Dealing Guardrail: merchants cannot submit bills for their own outlet
    if shop.seller == buyer_user:
        return False, "Merchants cannot submit purchase bills for their own store.", None

    if BumperBillSubmission.objects.filter(
        shop=shop,
        bill_number=bill_number,
        status__in=[BumperBillStatus.PENDING_STORE, BumperBillStatus.STORE_VERIFIED, BumperBillStatus.APPROVED, 'PENDING']
    ).exists():
        return False, f"Invoice #{bill_number} has already been submitted for counter verification.", None

    # Deep image sanitization & security validation
    saved_bill_image = None
    if bill_image:
        from services.image_security_service import process_and_sanitize_image, ImageSecurityException
        try:
            saved_bill_image = process_and_sanitize_image(bill_image, subfolder='bumper_bills', return_relative_path=True)
        except ImageSecurityException as se:
            return False, f"Receipt image rejected: {str(se)}", None
        except Exception as e:
            return False, f"Receipt image validation failed: {str(e)}", None

    try:
        submission = BumperBillSubmission.objects.create(
            buyer=buyer_user,
            shop=shop,
            bill_number=bill_number,
            bill_date=parsed_bill_date,
            bill_amount=amount,
            bill_image=saved_bill_image,
            status=BumperBillStatus.PENDING_STORE,
            points_awarded=100
        )
    except IntegrityError:
        return False, f"Invoice #{bill_number} has already been submitted for counter verification.", None

    return True, "Your ₹10,000+ purchase receipt has been submitted for counter verification! 100 Bonus Points + VIP perks will be credited upon approval.", submission


@transaction.atomic
def store_verify_bumper_bill(submission_id: int, merchant_user, approve: bool, rejection_reason: str = ""):
    """
    Step 1 of Two-Tier Approval: Store Merchant in-store POS verification.
    Transitions status from PENDING_STORE -> STORE_VERIFIED (or REJECTED with rejection_stage='STORE').
    CRITICAL: Does NOT award points or mint VIP vouchers at this stage to protect platform treasury.
    """
    from apps.rewards.models import BumperBillSubmission, BumperBillStatus
    from django.utils import timezone
    from services.notification_service import create_notification
    from apps.notifications.models import NotificationType

    submission = BumperBillSubmission.objects.select_for_update().filter(pk=submission_id).first()
    if not submission:
        return False, "Submission record not found.", None

    # Ensure current user is the owner of the shop
    if submission.shop.seller != merchant_user:
        return False, "Unauthorized access: You can only verify purchase bills for your own store.", submission

    # Anti-Self-Dealing Guardrail: merchant cannot verify their own bill
    if submission.buyer == merchant_user:
        return False, "Anti-Self-Dealing Violation: Store owners cannot verify their own purchase bills.", submission

    if submission.status not in [BumperBillStatus.PENDING_STORE, 'PENDING']:
        return False, f"This submission is not awaiting merchant verification (current status: {submission.get_status_display()}).", submission

    now = timezone.now()
    if approve:
        submission.status = BumperBillStatus.STORE_VERIFIED
        submission.store_verified_by = merchant_user
        submission.store_verified_at = now
        submission.save()

        # Notify buyer
        create_notification(
            recipient=submission.buyer,
            title="Receipt Verified by Store! 🏬",
            message="Store verified your purchase! Forwarded to LokalLoop Compliance for final voucher release.",
            notification_type=NotificationType.SYSTEM,
            link_url="/buyer/rewards/"
        )

        return True, "Store verified your purchase! Forwarded to LokalLoop Compliance for final voucher release.", submission
    else:
        submission.status = BumperBillStatus.REJECTED
        submission.rejection_stage = 'STORE'
        submission.store_verified_by = merchant_user
        submission.store_verified_at = now
        submission.rejection_reason = rejection_reason or "Store could not verify invoice against POS register."
        submission.save()

        create_notification(
            recipient=submission.buyer,
            title="Bumper Bill Declined by Store ❌",
            message=f"Store declined receipt #{submission.bill_number}: {submission.rejection_reason}",
            notification_type=NotificationType.SYSTEM,
            link_url="/buyer/rewards/"
        )

        return True, "Bumper bill submission declined at store verification stage.", submission


@transaction.atomic
def admin_clear_bumper_bill(submission_id: int, admin_user, approve: bool, rejection_reason: str = ""):
    """
    Step 2 of Two-Tier Approval: Platform Central Admin final clearance (Four-Eyes Principle).
    Requires submission to be in STORE_VERIFIED state.
    Atomically transitions status to APPROVED, records admin review timestamps,
    credits 100 reward points, generates VIP voucher, and dispatches notifications.
    """
    from apps.rewards.models import BumperBillSubmission, BumperBillStatus, RewardType
    from django.utils import timezone
    from services.notification_service import create_notification
    from apps.notifications.models import NotificationType
    import uuid

    submission = BumperBillSubmission.objects.select_for_update().filter(pk=submission_id).first()
    if not submission:
        return False, "Submission record not found.", None

    if submission.status not in [BumperBillStatus.STORE_VERIFIED, BumperBillStatus.PENDING_STORE, 'PENDING']:
        return False, f"This submission is already {submission.get_status_display().lower()}.", submission

    if approve and submission.status != BumperBillStatus.STORE_VERIFIED:
        return False, "Submissions must undergo in-store merchant verification before platform clearance.", submission

    now = timezone.now()
    if approve:
        submission.status = BumperBillStatus.APPROVED
        submission.admin_reviewed_by = admin_user
        submission.admin_reviewed_at = now
        submission.reviewed_by = admin_user
        submission.reviewed_at = now
        submission.voucher_code = f"BUMPER-VIP-{uuid.uuid4().hex[:6].upper()}"
        submission.voucher_details = "Complimentary Breakfast / Gourmet Coffee / Lunch Voucher"
        submission.save()

        # Award 100 bonus points
        award_points(
            buyer_user=submission.buyer,
            points=submission.points_awarded,
            transaction_type=RewardType.BUMPER_BONUS,
            notes=f"Bumper Bill #{submission.bill_number} verification at {submission.shop.name}"
        )

        # Notify buyer
        create_notification(
            recipient=submission.buyer,
            title="Bumper Bill Approved & VIP Voucher Issued! 🎉",
            message=f"Your ₹{submission.bill_amount} purchase at {submission.shop.name} is approved! 100 bonus points credited and VIP Voucher {submission.voucher_code} unlocked.",
            notification_type=NotificationType.SYSTEM,
            link_url="/buyer/rewards/"
        )

        # Notify store merchant
        create_notification(
            recipient=submission.shop.seller,
            title="Bumper Bill Central Clearance Complete ✅",
            message=f"Platform compliance cleared receipt #{submission.bill_number} for customer {submission.buyer.username}.",
            notification_type=NotificationType.SYSTEM,
            link_url="/seller/bill-verifications/"
        )

        return True, f"Bumper bill approved! 100 points credited and VIP Voucher {submission.voucher_code} issued.", submission
    else:
        submission.status = BumperBillStatus.REJECTED
        submission.rejection_stage = 'ADMIN'
        submission.admin_reviewed_by = admin_user
        submission.admin_reviewed_at = now
        submission.reviewed_by = admin_user
        submission.reviewed_at = now
        submission.rejection_reason = rejection_reason or "Central platform compliance check declined this receipt."
        submission.save()

        # Notify buyer
        create_notification(
            recipient=submission.buyer,
            title="Bumper Bill Declined by Platform Compliance ❌",
            message=f"Platform compliance declined receipt #{submission.bill_number}: {submission.rejection_reason}",
            notification_type=NotificationType.SYSTEM,
            link_url="/buyer/rewards/"
        )

        return True, "Bumper bill submission declined by platform administration.", submission


@transaction.atomic
def verify_bumper_bill(submission_id: int, reviewer_user, approve: bool, rejection_reason: str = ""):
    """
    Unified entry point for backward compatibility.
    Routes to admin_clear_bumper_bill if reviewer is staff/admin,
    or store_verify_bumper_bill if reviewer is store seller.
    """
    from apps.rewards.models import BumperBillSubmission
    from apps.accounts.models import Role

    submission = BumperBillSubmission.objects.filter(pk=submission_id).first()
    if not submission:
        return False, "Submission record not found.", None

    is_admin = (
        reviewer_user.is_superuser
        or reviewer_user.is_staff
        or (hasattr(reviewer_user, 'profile') and reviewer_user.profile.role == Role.ADMIN)
    )

    if is_admin:
        return admin_clear_bumper_bill(submission_id, reviewer_user, approve, rejection_reason)
    else:
        return store_verify_bumper_bill(submission_id, reviewer_user, approve, rejection_reason)


@transaction.atomic
def verify_and_burn_catalog_claim(voucher_code: str, seller_user):
    """
    Store counter / cashier verification and burning of a dynamic catalog voucher.
    Transitions BuyerRewardClaim from ACTIVE to USED, records audit timestamps,
    enforces anti-self-dealing guardrails, and prevents double-spending.
    """
    from apps.rewards.models import BuyerRewardClaim
    from apps.notifications.models import NotificationType
    from services.notification_service import create_notification
    from django.utils import timezone

    voucher_code = (voucher_code or '').strip().upper()
    if not voucher_code:
        return False, "Please enter a valid voucher code.", None

    claim = BuyerRewardClaim.objects.select_for_update().filter(voucher_code=voucher_code).first()
    if not claim:
        return False, f"Voucher code '{voucher_code}' not found in catalog claims.", None

    # Anti-Self-Dealing Guardrail: Merchants cannot redeem their own catalog vouchers
    if claim.buyer == seller_user:
        return False, "Anti-Self-Dealing Violation: Store merchants cannot redeem their own catalog reward vouchers.", claim

    # Check already used
    if claim.status == 'USED':
        used_time_str = claim.used_at.strftime('%d %b %Y, %I:%M %p') if claim.used_at else "previously"
        return False, f"This voucher code '{voucher_code}' has already been redeemed and marked USED ({used_time_str}).", claim

    # Check expiry
    now = timezone.now()
    if claim.status == 'EXPIRED' or (claim.expires_at and now > claim.expires_at):
        if claim.status != 'EXPIRED':
            claim.status = 'EXPIRED'
            claim.save(update_fields=['status'])
        return False, f"This voucher code '{voucher_code}' has expired and can no longer be redeemed.", claim

    if claim.status != 'ACTIVE':
        return False, f"Voucher '{voucher_code}' is not valid for redemption (Current status: {claim.get_status_display()}).", claim

    # Atomically mark as USED
    claim.status = 'USED'
    claim.used_at = now
    claim.verified_by = seller_user
    claim.save(update_fields=['status', 'used_at', 'verified_by'])

    # Dispatch notification to buyer
    create_notification(
        recipient=claim.buyer,
        title="Reward Voucher Redeemed In-Store! 🎁",
        message=f"Your catalog voucher {claim.voucher_code} for '{claim.catalog_item.title}' was verified and redeemed at checkout.",
        notification_type=NotificationType.OFFER_REDEEMED,
        link_url="/buyer/rewards/"
    )

    return True, f"Voucher '{claim.voucher_code}' ({claim.catalog_item.title}) successfully verified and redeemed!", claim

