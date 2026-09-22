"""
Endpoints used by Profile > Edit ... and Update This Period's Bills.

  GET    /api/setup/current/               everything needed to pre-fill the edit screens
  PUT    /api/setup/household/             Edit Household Profile (members, dependents, housing, earners)
  PUT    /api/setup/income/                Edit Income Details
  PUT    /api/setup/ranges/                Edit Budget Ranges (+ daily food / transport)
  POST   /api/setup/bills/                 Edit Budget Items: add a bill
  PUT    /api/setup/bills/<id>/            Edit Budget Items: edit a bill
  DELETE /api/setup/bills/<id>/            Edit Budget Items: delete a bill
  POST   /api/setup/bills/amounts/         Update This Period's Bills (bulk amounts)

Every query is scoped to the logged-in household. Changes are applied in one transaction.
"""
from datetime import date

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BudgetAllocation, BudgetItem, Earner, Income, IncomeFrequency, ItemCategory
from .serializers import BudgetAllocationSerializer, HouseholdSerializer
from .setup_views import (
    HOUSING_TYPES,
    SetupError,
    _date,
    _due_in_month,
    _int,
    _next_due_date,
    _num,
    _parse_bills,
    _period_for,
)
from .views import classify_bill


# ---------------------------------------------------------------- helpers

def _bad(message):
    return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)


def _not_found(message):
    return Response({'error': message}, status=status.HTTP_404_NOT_FOUND)


def _latest_income(earner):
    return (
        Income.objects.filter(earner=earner)
        .select_related('income_frequency')
        .order_by('-income_startdate', '-income_id')
        .first()
    )


def _frequency(desc):
    return (
        IncomeFrequency.objects.filter(frequency_desc__iexact=desc).first()
        or IncomeFrequency.objects.create(frequency_desc=desc)
    )


def _default_income(earner):
    """New earners start with no income; the user sets it in Edit Income Details."""
    today = date.today()
    return Income.objects.create(
        earner=earner,
        income_frequency=_frequency('Monthly'),
        range_amount='0.00-0.00',
        income_startdate=today,
        next_payday=_due_in_month(today.year, today.month, 31),
    )


def _range(value, label):
    parts = str(value or '').split('-')
    if len(parts) != 2:
        raise SetupError(f'{label}: choose an income range')
    lo, hi = _num(parts[0], label), _num(parts[1], label)
    if lo > hi:
        raise SetupError(f'{label}: the range is invalid')
    return f'{lo}-{hi}'


def _classify_and_save(bill):
    priority, classification = classify_bill(bill)
    bill.priority_level = priority
    bill.budget_classification = classification
    bill.save(update_fields=['priority_level', 'budget_classification'])


def _category(label):
    return (
        ItemCategory.objects.filter(category_desc__iexact=label).first()
        or ItemCategory.objects.create(category_desc=label)
    )


def _get_bill(household, allocation_id):
    return (
        BudgetAllocation.objects.select_related('income', 'item', 'item__category')
        .filter(budget_allocation_id=allocation_id, income__earner__household=household)
        .first()
    )


def _household_income(household):
    return (
        Income.objects.filter(earner__household=household)
        .order_by('-income_startdate', '-income_id')
        .first()
    )


# ---------------------------------------------------------------- read

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_setup(request):
    household = request.user

    earners = []
    for e in Earner.objects.filter(household=household).order_by('earner_id'):
        inc = _latest_income(e)
        earners.append({
            'earner_id': e.earner_id,
            'first_name': e.earner_fname,
            'last_name': e.earner_lname,
            'income_id': inc.income_id if inc else None,
            'frequency': inc.income_frequency.frequency_desc if inc and inc.income_frequency else 'Monthly',
            'range_amount': inc.range_amount if inc else '',
            'next_payday': inc.next_payday if inc else None,
        })

    bills = (
        BudgetAllocation.objects.filter(income__earner__household=household)
        .select_related('income', 'item', 'item__category')
        .order_by('actual_due_date', 'budget_allocation_id')
    )

    return Response({
        'household': HouseholdSerializer(household).data,
        'earners': earners,
        'bills': BudgetAllocationSerializer(bills, many=True).data,
    })


# ---------------------------------------------------------------- household

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_household_setup(request):
    household = request.user
    data = request.data

    try:
        hh = data.get('household') or {}
        total = _int(hh.get('total_members'), 'Total family members', 1)
        dependents = _int(hh.get('no_of_dependents'), 'Number of dependents', 0)
        housing = hh.get('housing_type')
        if housing not in HOUSING_TYPES:
            raise SetupError('Choose a housing type')

        items = data.get('earners') or []
        if not items:
            raise SetupError('Add at least one earner')

        parsed = []
        for i, e in enumerate(items, 1):
            first = str(e.get('first_name') or '').strip()
            last = str(e.get('last_name') or '').strip()
            if not first or not last:
                raise SetupError(f'Earner {i}: first and last name are required')
            raw_id = e.get('earner_id')
            parsed.append((_int(raw_id, 'earner_id', 1) if raw_id not in (None, '') else None, first, last))

        with transaction.atomic():
            existing = {e.earner_id: e for e in Earner.objects.filter(household=household)}
            keep, kept = set(), []

            for earner_id, first, last in parsed:
                if earner_id is not None:
                    earner = existing.get(earner_id)
                    if earner is None:
                        raise SetupError('One of the earners was not found')
                    earner.earner_fname, earner.earner_lname = first, last
                    earner.save()
                    keep.add(earner_id)
                else:
                    earner = Earner.objects.create(household=household, earner_fname=first, earner_lname=last)
                kept.append(earner)

            for earner in kept:
                if _latest_income(earner) is None:
                    _default_income(earner)

            # Bills hang off an Income. Before removing an earner, move their bills to a kept earner
            # so deleting the earner can never delete bills.
            target = _latest_income(kept[0])
            for earner_id, earner in existing.items():
                if earner_id not in keep:
                    BudgetAllocation.objects.filter(income__earner=earner).update(income=target)
                    earner.delete()

            household.total_members = total
            household.no_of_dependents = dependents
            household.housing_type = housing
            household.no_of_earners = len(kept)
            household.save()
    except SetupError as exc:
        return _bad(str(exc))

    return Response({'message': 'Household updated.', 'household': HouseholdSerializer(household).data})


# ---------------------------------------------------------------- income

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_income_setup(request):
    household = request.user
    items = request.data.get('earners') or []
    if not items:
        return _bad('No income details were sent')

    try:
        parsed = []
        for i, e in enumerate(items, 1):
            frequency = str(e.get('frequency') or '').strip()
            if not frequency:
                raise SetupError(f'Earner {i}: choose an income frequency')
            parsed.append((
                _int(e.get('earner_id'), 'earner_id', 1),
                frequency,
                _range(e.get('range_amount'), f'Earner {i} income'),
                _date(e.get('next_payday'), f'Earner {i} next payday'),
            ))

        with transaction.atomic():
            for earner_id, frequency, rng, payday in parsed:
                earner = Earner.objects.filter(earner_id=earner_id, household=household).first()
                if earner is None:
                    raise SetupError('One of the earners was not found')
                income = _latest_income(earner) or _default_income(earner)
                income.income_frequency = _frequency(frequency)
                income.range_amount = rng
                income.next_payday = payday
                income.save()
    except SetupError as exc:
        return _bad(str(exc))

    return Response({'message': 'Income updated.'})


# ---------------------------------------------------------------- ranges

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_ranges_setup(request):
    household = request.user
    data = request.data

    try:
        parsed = []
        for b in data.get('bills') or []:
            lo = _num(b.get('min_amount'), 'Minimum amount')
            hi = _num(b.get('max_amount'), 'Maximum amount')
            if lo <= 0 or hi <= 0:
                raise SetupError('Amounts must be greater than 0')
            if lo > hi:
                raise SetupError("The minimum can't be higher than the maximum")
            parsed.append((_int(b.get('allocation_id'), 'allocation_id', 1), lo, hi))
        food = _num(data.get('daily_food'), 'Daily food expenses')
        transport = _num(data.get('daily_transport'), 'Daily transportation')

        with transaction.atomic():
            for allocation_id, lo, hi in parsed:
                bill = _get_bill(household, allocation_id)
                if bill is None:
                    raise SetupError('One of the bills was not found')
                bill.budget_amount_range = f'{lo}-{hi}'
                bill.save(update_fields=['budget_amount_range'])

            household.daily_food_expense_min = household.daily_food_expense_max = food
            household.daily_transport_expense_min = household.daily_transport_expense_max = transport
            household.save()
    except SetupError as exc:
        return _bad(str(exc))

    return Response({'message': 'Budget ranges updated.'})


# ---------------------------------------------------------------- bills: add / edit / delete

def _create_bill(income, b, today):
    item = BudgetItem.objects.create(
        category=_category(b['category']),
        item_desc=b['name'],
        due_day=b['due_day'],
        grace_period_days=b['grace'],
        penalty_classification=b['penalty'],
    )
    due = b['due_date'] or _next_due_date(b['due_day'], today)
    half, start, end = _period_for(due)
    return BudgetAllocation.objects.create(
        income=income,
        item=item,
        amount=b['amount'],
        actual_due_date=due,
        budget_amount_range=f"{b['min']}-{b['max']}",
        budget_start_date=start,
        budget_end_date=end,
        period_half=half,
        is_confirmed=True,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_setup_bill(request):
    household = request.user
    try:
        b = _parse_bills([request.data])[0]
    except SetupError as exc:
        return _bad(str(exc))

    income = _household_income(household)
    if income is None:
        return _bad('Finish your setup before adding bills.')

    with transaction.atomic():
        bill = _create_bill(income, b, date.today())
        _classify_and_save(bill)

    return Response(BudgetAllocationSerializer(bill).data, status=status.HTTP_201_CREATED)


@api_view(['PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def setup_bill_detail(request, allocation_id):
    household = request.user
    bill = _get_bill(household, allocation_id)
    if bill is None:
        return _not_found('Bill not found.')

    if request.method == 'DELETE':
        item = bill.item
        with transaction.atomic():
            bill.delete()
            if not BudgetAllocation.objects.filter(item=item).exists():
                item.delete()
        return Response({'message': 'Bill deleted.'})

    try:
        b = _parse_bills([request.data])[0]
    except SetupError as exc:
        return _bad(str(exc))

    today = date.today()
    with transaction.atomic():
        item = bill.item
        due_changed = item.due_day != b['due_day'] or b['due_date'] is not None

        item.item_desc = b['name']
        item.category = _category(b['category'])
        item.due_day = b['due_day']
        item.grace_period_days = b['grace']
        item.penalty_classification = b['penalty']
        item.save()

        bill.budget_amount_range = f"{b['min']}-{b['max']}"
        if b['amount'] is not None:
            bill.amount = b['amount']
        if due_changed or bill.actual_due_date is None:
            bill.actual_due_date = b['due_date'] or _next_due_date(b['due_day'], today)
            bill.period_half, bill.budget_start_date, bill.budget_end_date = _period_for(bill.actual_due_date)
        bill.save()
        _classify_and_save(bill)

    return Response(BudgetAllocationSerializer(bill).data)


# ---------------------------------------------------------------- update this period's amounts

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_bill_amounts(request):
    household = request.user
    items = request.data.get('bills') or []
    if not items:
        return _bad('Enter at least one new amount.')

    try:
        parsed = []
        for entry in items:
            amount = _num(entry.get('amount'), 'Amount')
            if amount <= 0:
                raise SetupError('Amounts must be greater than 0')
            parsed.append((_int(entry.get('allocation_id'), 'allocation_id', 1), amount))

        today = date.today()
        with transaction.atomic():
            for allocation_id, amount in parsed:
                bill = _get_bill(household, allocation_id)
                if bill is None:
                    raise SetupError('One of the bills was not found')

                # The real bill is known now, so the range collapses to the exact amount.
                bill.amount = amount
                bill.budget_amount_range = f'{amount}-{amount}'

                # Move a past due date on to this period's due date.
                due = bill.actual_due_date
                if due is None or due < today:
                    due = _next_due_date(bill.item.due_day, today)
                bill.actual_due_date = due
                bill.period_half, bill.budget_start_date, bill.budget_end_date = _period_for(due)
                bill.save()
                _classify_and_save(bill)
    except SetupError as exc:
        return _bad(str(exc))

    return Response({'message': 'Bills updated.', 'updated': len(parsed)})
