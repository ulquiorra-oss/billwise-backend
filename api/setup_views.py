"""
POST /api/setup/submit/

Receives everything the user entered in Setup 1-4 and saves it in ONE transaction:
household profile, earners, incomes, budget items and bill allocations.
If anything is invalid nothing is saved, so the user can fix it and resubmit.
"""
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BudgetAllocation, BudgetItem, Earner, Income, IncomeFrequency, ItemCategory
from .serializers import HouseholdSerializer

HOUSING_TYPES = {'Own House', 'Renting', 'With Relatives'}
MAX_AMOUNT = Decimal('100000000')  # DecimalField(max_digits=10, decimal_places=2)


class SetupError(Exception):
    """Raised for invalid input; the message is returned to the app."""


# ---------------------------------------------------------------- validation helpers

def _num(value, field, minimum=0):
    try:
        n = Decimal(str(value).replace(',', ''))
    except (InvalidOperation, TypeError, ValueError):
        raise SetupError(f'{field} must be a number')
    if not n.is_finite() or n < minimum or n >= MAX_AMOUNT:
        raise SetupError(f'{field} is out of range')
    return n.quantize(Decimal('0.01'))


def _int(value, field, minimum=0, maximum=None):
    try:
        n = int(str(value))
    except (TypeError, ValueError):
        raise SetupError(f'{field} must be a whole number')
    if n < minimum or (maximum is not None and n > maximum):
        raise SetupError(f'{field} is out of range')
    return n


def _date(value, field):
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        raise SetupError(f'{field} must be a date in YYYY-MM-DD format')


def _due_in_month(year, month, day):
    return date(year, month, min(day, monthrange(year, month)[1]))


def _next_due_date(day, today):
    """Next occurrence of `day` (today counts)."""
    this_month = _due_in_month(today.year, today.month, day)
    if this_month >= today:
        return this_month
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return _due_in_month(year, month, day)


def _period_for(due):
    if due.day <= 15:
        return '1st Half', date(due.year, due.month, 1), date(due.year, due.month, 15)
    return '2nd Half', date(due.year, due.month, 16), _due_in_month(due.year, due.month, 31)


# ---------------------------------------------------------------- parsing

def _parse_earners(items):
    if not items:
        raise SetupError('Add at least one earner')
    earners = []
    for i, e in enumerate(items, 1):
        first = str(e.get('first_name') or '').strip()
        last = str(e.get('last_name') or '').strip()
        if not first or not last:
            raise SetupError(f'Earner {i}: first and last name are required')

        parts = str(e.get('range_amount') or '').split('-')
        if len(parts) != 2:
            raise SetupError(f'Earner {i}: choose an income range')
        lo, hi = _num(parts[0], f'Earner {i} income'), _num(parts[1], f'Earner {i} income')
        if lo > hi:
            raise SetupError(f'Earner {i}: income range is invalid')

        frequency = str(e.get('frequency') or '').strip()
        if not frequency:
            raise SetupError(f'Earner {i}: choose an income frequency')

        earners.append({
            'first': first,
            'last': last,
            'range': f'{lo}-{hi}',
            'frequency': frequency,
            'payday': _date(e.get('next_payday'), f'Earner {i} next payday'),
        })
    return earners


def _parse_bills(items):
    if not items:
        raise SetupError('Add at least one bill')
    bills = []
    for i, b in enumerate(items, 1):
        name = str(b.get('item_desc') or '').strip()
        if not name:
            raise SetupError(f'Bill {i}: name is required')

        lo = _num(b.get('min_amount'), f'{name} minimum')
        hi = _num(b.get('max_amount'), f'{name} maximum')
        if lo > hi:
            raise SetupError(f"{name}: the minimum can't be higher than the maximum")

        due_date = _date(b['due_date'], f'{name} due date') if b.get('due_date') else None
        amount = _num(b['amount'], f'{name} amount') if b.get('amount') not in (None, '') else None

        bills.append({
            'name': name[:255],
            'category': str(b.get('category') or 'Other').strip() or 'Other',
            'due_day': due_date.day if due_date else _int(b.get('due_day'), f'{name} due day', 1, 31),
            'due_date': due_date,
            'grace': _int(b.get('grace_period_days', 0), f'{name} grace period', 0, 365),
            'penalty': bool(b.get('penalty_classification')),
            'amount': amount,
            'min': lo,
            'max': hi,
        })
    return bills


# ---------------------------------------------------------------- endpoint

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_setup(request):
    household = request.user

    if household.setup_completed:
        return Response({'error': 'Setup has already been completed.'}, status=status.HTTP_409_CONFLICT)

    data = request.data
    try:
        hh = data.get('household') or {}
        total_members = _int(hh.get('total_members'), 'Total family members', 1)
        dependents = _int(hh.get('no_of_dependents'), 'Number of dependents', 0)
        housing = hh.get('housing_type')
        if housing not in HOUSING_TYPES:
            raise SetupError('Choose a housing type')

        earners = _parse_earners(data.get('earners'))
        bills = _parse_bills(data.get('bills'))
        daily_food = _num(data.get('daily_food'), 'Daily food expenses')
        daily_transport = _num(data.get('daily_transport'), 'Daily transportation')
    except SetupError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    today = date.today()

    with transaction.atomic():
        # setup_completed is still False, so any earners/incomes/bills that exist
        # are leftovers from testing. Start clean (cascades to income + bills).
        Earner.objects.filter(household=household).delete()

        household.total_members = total_members
        household.no_of_earners = len(earners)
        household.no_of_dependents = dependents
        household.housing_type = housing
        household.daily_food_expense_min = daily_food
        household.daily_food_expense_max = daily_food
        household.daily_transport_expense_min = daily_transport
        household.daily_transport_expense_max = daily_transport
        household.setup_completed = True
        household.save()

        incomes = []
        for e in earners:
            earner = Earner.objects.create(
                household=household, earner_fname=e['first'], earner_lname=e['last']
            )
            frequency = (
                IncomeFrequency.objects.filter(frequency_desc__iexact=e['frequency']).first()
                or IncomeFrequency.objects.create(frequency_desc=e['frequency'])
            )
            incomes.append(Income.objects.create(
                earner=earner,
                income_frequency=frequency,
                range_amount=e['range'],
                income_startdate=today,
                next_payday=e['payday'],
            ))

        # Every allocation needs an Income; bills are attached to the first earner's income.
        # Risk assessment sums all incomes for the household, so this doesn't change the result.
        income = incomes[0]

        for b in bills:
            category = (
                ItemCategory.objects.filter(category_desc__iexact=b['category']).first()
                or ItemCategory.objects.create(category_desc=b['category'])
            )
            item = BudgetItem.objects.create(
                category=category,
                item_desc=b['name'],
                due_day=b['due_day'],
                grace_period_days=b['grace'],
                penalty_classification=b['penalty'],
            )
            due = b['due_date'] or _next_due_date(b['due_day'], today)
            period_half, start, end = _period_for(due)

            BudgetAllocation.objects.create(
                income=income,
                item=item,
                amount=b['amount'],
                actual_due_date=due,
                budget_amount_range=f"{b['min']}-{b['max']}",
                budget_start_date=start,
                budget_end_date=end,
                period_half=period_half,
                is_confirmed=True,
            )

    return Response(
        {'message': 'Setup saved.', 'household': HouseholdSerializer(household).data},
        status=status.HTTP_201_CREATED,
    )