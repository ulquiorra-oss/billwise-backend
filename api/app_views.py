"""
Endpoints added for the mobile app.

  POST /api/auth/refresh/          -> rotate JWT pair (custom: SimpleJWT's built-in
                                      refresh view looks users up in auth.User, which
                                      doesn't work with the Household model)
  POST /api/bills/quick-add/       -> create a bill from the Add Bill / Scanner screens
  POST /api/bills/<id>/pay/        -> mark a bill paid (or undo with {"is_paid": false})
  GET/PUT /api/income/monthly/     -> read / set the household's monthly income
"""
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .auth_views import get_tokens_for_user
from .models import (
    Biller,
    BudgetAllocation,
    BudgetItem,
    Earner,
    Household,
    Income,
    IncomeFrequency,
    ItemCategory,
)
from .payday import income_next_payday
from .risk import _parse_range
from .serializers import BudgetAllocationSerializer
from .setup_views import _period_for
from .views import classify_bill


# ============================================================
# TOKEN REFRESH
# ============================================================

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def refresh_token(request):
    raw = request.data.get('refresh')
    if not raw:
        return Response({'error': 'refresh is required'}, status=status.HTTP_400_BAD_REQUEST)

    claim = settings.SIMPLE_JWT.get('USER_ID_CLAIM', 'user_id')
    try:
        old = RefreshToken(raw)  # verifies signature, expiry and blacklist
        household = Household.objects.get(household_id=old[claim])
    except (TokenError, Household.DoesNotExist, KeyError):
        return Response(
            {'error': 'Refresh token is invalid or expired'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Returns {'access': ..., 'refresh': ...} (rotated pair)
    return Response(get_tokens_for_user(household), status=status.HTTP_200_OK)


# ============================================================
# HELPERS
# ============================================================

# key sent by the app -> ItemCategory.category_desc
CATEGORY_LABELS = {
    'electricity': 'Electricity',
    'water': 'Water',
    'internet': 'Internet',
    'rent': 'Rent',
    'insurance': 'Insurance',
    'loan': 'Loan',
    'phone': 'Phone',
    'other': 'Other',
}

# key -> (penalty_classification, grace_period_days)
# These feed classify_bill(): essentials with a penalty and no grace period
# come out High / Non-deferrable, internet comes out Medium / Deferrable.
CATEGORY_DEFAULTS = {
    'electricity': (True, 0),
    'water': (True, 0),
    'rent': (True, 0),
    'loan': (True, 0),
    'internet': (True, 3),
    'insurance': (True, 3),
    'phone': (True, 3),
    'other': (False, 0),
}


def _parse_money(value, allow_zero=False):
    try:
        amount = Decimal(str(value).replace(',', ''))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        return None
    if amount >= Decimal('100000000'):  # DecimalField(max_digits=10, decimal_places=2)
        return None
    return amount.quantize(Decimal('0.01'))


def _end_of_month(d):
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def get_default_income(household):
    """
    A new household has no earner/income rows, but every bill needs an Income FK.
    Return the household's latest income, creating a default earner + income if none.
    """
    income = (
        Income.objects.filter(earner__household=household)
        .order_by('-income_startdate', '-income_id')
        .first()
    )
    if income:
        return income

    with transaction.atomic():
        earner = Earner.objects.filter(household=household).first()
        if earner is None:
            earner = Earner.objects.create(
                household=household,
                earner_fname=household.first_name,
                earner_lname=household.last_name,
            )

        frequency = (
            IncomeFrequency.objects.filter(frequency_desc__icontains='month').first()
            or IncomeFrequency.objects.order_by('frequency_id').first()
            or IncomeFrequency.objects.create(frequency_desc='Monthly')
        )

        today = date.today()
        return Income.objects.create(
            earner=earner,
            income_frequency=frequency,
            range_amount='0',
            income_startdate=today,
            next_payday=_end_of_month(today),
            payday_day_1=31,
        )


# ============================================================
# QUICK-ADD BILL
# ============================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quick_add_bill(request):
    """
    Body: { name, amount, category, due_date (YYYY-MM-DD), income? }
    Creates a BudgetItem + a confirmed BudgetAllocation in one call and runs the
    rule engine so priority_level / budget_classification are filled in.
    """
    household = request.user
    data = request.data

    name = str(data.get('name') or '').strip()
    if not name:
        return Response({'error': 'name is required'}, status=status.HTTP_400_BAD_REQUEST)

    amount = _parse_money(data.get('amount'))
    if amount is None:
        return Response(
            {'error': 'amount must be a positive number'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        due = datetime.strptime(str(data.get('due_date')), '%Y-%m-%d').date()
    except ValueError:
        return Response(
            {'error': 'due_date must be in YYYY-MM-DD format'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    category_key = str(data.get('category') or 'other').lower()
    if category_key not in CATEGORY_LABELS:
        category_key = 'other'

    income_id = data.get('income')
    if income_id:
        try:
            income = Income.objects.get(income_id=income_id, earner__household=household)
        except (Income.DoesNotExist, ValueError):
            return Response(
                {'error': 'Income not found for this household'},
                status=status.HTTP_404_NOT_FOUND,
            )
    else:
        income = get_default_income(household)

    penalty, grace = CATEGORY_DEFAULTS[category_key]

    if due.day <= 15:
        period_half = '1st Half'
        start, end = date(due.year, due.month, 1), date(due.year, due.month, 15)
    else:
        period_half = '2nd Half'
        start, end = date(due.year, due.month, 16), _end_of_month(due)

    label = CATEGORY_LABELS[category_key]

    with transaction.atomic():
        category = (
            ItemCategory.objects.filter(category_desc__iexact=label).first()
            or ItemCategory.objects.create(category_desc=label)
        )
        item = BudgetItem.objects.create(
            category=category,
            item_desc=name[:255],
            due_day=due.day,
            grace_period_days=grace,
            penalty_classification=penalty,
        )
        bill = BudgetAllocation.objects.create(
            income=income,
            item=item,
            amount=amount,
            actual_due_date=due,
            budget_amount_range=str(amount),
            budget_start_date=start,
            budget_end_date=end,
            period_half=period_half,
            is_confirmed=True,
        )

        priority, classification = classify_bill(bill)
        bill.priority_level = priority
        bill.budget_classification = classification
        bill.save(update_fields=['priority_level', 'budget_classification'])

    return Response(BudgetAllocationSerializer(bill).data, status=status.HTTP_201_CREATED)


# ============================================================
# PAY BILL
# ============================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pay_bill(request, allocation_id):
    try:
        bill = BudgetAllocation.objects.select_related('item', 'item__category').get(
            budget_allocation_id=allocation_id,
            income__earner__household=request.user,
        )
    except BudgetAllocation.DoesNotExist:
        return Response({'error': 'Bill not found.'}, status=status.HTTP_404_NOT_FOUND)

    is_paid = request.data.get('is_paid', True)
    if isinstance(is_paid, str):
        is_paid = is_paid.strip().lower() not in ('false', '0', 'no', '')

    bill.is_paid = bool(is_paid)
    bill.paid_date = date.today() if bill.is_paid else None
    bill.save(update_fields=['is_paid', 'paid_date'])

    return Response(BudgetAllocationSerializer(bill).data, status=status.HTTP_200_OK)


# ============================================================
# MOVE TO NEXT PERIOD
# ============================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def defer_bill(request, allocation_id):
    """
    Plan to pay a deferrable bill after the next payday, or undo that plan ({"deferred": false}).
    Only bills the rules marked Deferrable can be moved. The bill's real due date does not change.
    """
    try:
        bill = BudgetAllocation.objects.select_related('item', 'item__category').get(
            budget_allocation_id=allocation_id,
            income__earner__household=request.user,
        )
    except BudgetAllocation.DoesNotExist:
        return Response({'error': 'Bill not found.'}, status=status.HTTP_404_NOT_FOUND)

    deferred = request.data.get('deferred', True)
    if isinstance(deferred, str):
        deferred = deferred.strip().lower() not in ('false', '0', 'no', '')

    if not deferred:
        bill.deferred_until = None
    else:
        if bill.is_paid:
            return Response({'error': 'This bill is already paid.'}, status=status.HTTP_400_BAD_REQUEST)
        if bill.budget_classification != 'Deferrable':
            return Response({'error': "This bill can't wait, so it can't be moved."}, status=status.HTTP_400_BAD_REQUEST)
        paydays = [
            p for p in (
                income_next_payday(i)
                for i in Income.objects.filter(earner__household=request.user).select_related('income_frequency')
            ) if p
        ]
        if not paydays:
            return Response({'error': 'Set your payday first.'}, status=status.HTTP_400_BAD_REQUEST)
        bill.deferred_until = min(paydays)

    bill.save(update_fields=['deferred_until'])
    return Response(BudgetAllocationSerializer(bill).data, status=status.HTTP_200_OK)


# ============================================================
# PREVIEW PRIORITY (scan review)
# ============================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def preview_priority(request):
    """
    The priority a bill would get if it were saved now. Runs the same rules as a real bill (classify_bill)
    on a stand-in object, so nothing is written. Used by the scan review screen.
    """
    d = request.data
    try:
        due = datetime.strptime(str(d.get('due_date') or ''), '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'A valid due date is required.'}, status=status.HTTP_400_BAD_REQUEST)

    name = str(d.get('item_desc') or '').strip()
    category = str(d.get('category') or 'Other').strip() or 'Other'

    biller = None
    try:
        if d.get('biller_id') not in (None, ''):
            biller = Biller.objects.filter(biller_id=int(d.get('biller_id'))).first()
    except (TypeError, ValueError):
        biller = None

    # A recognised biller supplies the rules; otherwise the bill's own values (same defaults as a scan).
    if biller is not None:
        grace, penalty = biller.grace_period_days, biller.has_penalty
    else:
        try:
            grace = max(0, int(d.get('grace_period_days') or 0))
        except (TypeError, ValueError):
            grace = 0
        penalty = bool(d.get('penalty_classification', True))

    _, start, end = _period_for(due)
    stand_in = SimpleNamespace(
        item=SimpleNamespace(
            item_desc=name,
            category=SimpleNamespace(category_desc=category),
            penalty_classification=penalty,
            grace_period_days=grace,
        ),
        actual_due_date=due,
        budget_start_date=start,
        budget_end_date=end,
    )
    priority, classification, rule, reason = classify_bill(stand_in)

    return Response({
        'priority_level': priority,
        'budget_classification': classification,
        'rule_applied': rule,
        'reason': reason,
        'grace_period_days': grace,
        'penalty_classification': penalty,
    }, status=status.HTTP_200_OK)


# ============================================================
# MONTHLY INCOME (Profile -> Monthly Budget)
# ============================================================

@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def monthly_income(request):
    income = get_default_income(request.user)

    if request.method == 'PUT':
        amount = _parse_money(request.data.get('amount'), allow_zero=True)
        if amount is None:
            return Response(
                {'error': 'amount must be a number'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        income.range_amount = str(amount)
        income.save(update_fields=['range_amount'])

    lo, hi = _parse_range(income.range_amount)
    return Response({
        'income_id': income.income_id,
        'range_amount': income.range_amount,
        'min': str(lo),
        'max': str(hi),
        'next_payday': income_next_payday(income),
    }, status=status.HTTP_200_OK)