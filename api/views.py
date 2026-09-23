from datetime import date
from decimal import Decimal

from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status

from .ocr import scan_bill_image
from .risk import assess_financial_risk, compute_days_until_next_payday, _parse_range

from .models import (
    Household,
    Earner,
    Income,
    IncomeFrequency,
    ItemCategory,
    BudgetItem,
    BudgetAllocation,
)

from .serializers import (
    HouseholdSerializer,
    EarnerSerializer,
    IncomeSerializer,
    IncomeFrequencySerializer,
    ItemCategorySerializer,
    BudgetItemSerializer,
    BudgetAllocationSerializer,
)


# ============================================================
# HOUSEHOLD ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_household(request):
    """Get the current logged-in household's profile."""
    household = request.user
    serializer = HouseholdSerializer(household)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_household(request):
    """Update the current household's profile."""
    household = request.user
    partial = request.method == 'PATCH'
    serializer = HouseholdSerializer(household, data=request.data, partial=partial)

    if serializer.is_valid():
        serializer.save()
        return Response({
            'message': 'Household updated successfully',
            'household': serializer.data
        }, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ============================================================
# EARNER ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_earners(request):
    household = request.user
    earners = Earner.objects.filter(household=household)
    serializer = EarnerSerializer(earners, many=True)

    return Response({
        'count': earners.count(),
        'earners': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_earner(request):
    household = request.user
    earner_fname = request.data.get('earner_fname')
    earner_lname = request.data.get('earner_lname')

    if not earner_fname or not earner_lname:
        return Response(
            {'error': 'earner_fname and earner_lname are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    earner = Earner.objects.create(
        household=household,
        earner_fname=earner_fname,
        earner_lname=earner_lname,
    )

    serializer = EarnerSerializer(earner)

    return Response({
        'message': 'Earner created successfully',
        'earner': serializer.data
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_earner(request, earner_id):
    household = request.user

    try:
        earner = Earner.objects.get(earner_id=earner_id, household=household)
    except Earner.DoesNotExist:
        return Response(
            {'error': 'Earner not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = EarnerSerializer(earner)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_earner(request, earner_id):
    household = request.user

    try:
        earner = Earner.objects.get(earner_id=earner_id, household=household)
    except Earner.DoesNotExist:
        return Response(
            {'error': 'Earner not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    partial = request.method == 'PATCH'
    serializer = EarnerSerializer(earner, data=request.data, partial=partial)

    if serializer.is_valid():
        serializer.save()
        return Response({
            'message': 'Earner updated successfully',
            'earner': serializer.data
        }, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_earner(request, earner_id):
    household = request.user

    try:
        earner = Earner.objects.get(earner_id=earner_id, household=household)
    except Earner.DoesNotExist:
        return Response(
            {'error': 'Earner not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    earner.delete()

    return Response(
        {'message': 'Earner deleted successfully'},
        status=status.HTTP_200_OK
    )


# ============================================================
# INCOME FREQUENCY ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_income_frequencies(request):
    frequencies = IncomeFrequency.objects.all().order_by('frequency_id')
    serializer = IncomeFrequencySerializer(frequencies, many=True)

    return Response({
        'count': frequencies.count(),
        'frequencies': serializer.data
    }, status=status.HTTP_200_OK)


# ============================================================
# INCOME ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_income(request):
    household = request.user

    incomes = Income.objects.filter(
        earner__household=household
    ).select_related(
        'earner',
        'income_frequency'
    ).order_by('-income_startdate')

    serializer = IncomeSerializer(incomes, many=True)

    return Response({
        'count': incomes.count(),
        'incomes': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_income(request):
    household = request.user
    data = request.data

    required = [
        'earner',
        'income_frequency',
        'range_amount',
        'income_startdate',
        'next_payday'
    ]

    for field in required:
        if data.get(field) in (None, ''):
            return Response(
                {'error': f'{field} is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

    try:
        earner = Earner.objects.get(earner_id=data['earner'], household=household)
    except Earner.DoesNotExist:
        return Response(
            {'error': 'Earner not found in your household'},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        frequency = IncomeFrequency.objects.get(frequency_id=data['income_frequency'])
    except IncomeFrequency.DoesNotExist:
        return Response(
            {'error': 'Invalid income_frequency'},
            status=status.HTTP_400_BAD_REQUEST
        )

    income = Income.objects.create(
        earner=earner,
        income_frequency=frequency,
        range_amount=data['range_amount'],
        income_startdate=data['income_startdate'],
        next_payday=data['next_payday'],
    )

    serializer = IncomeSerializer(income)

    return Response({
        'message': 'Income created successfully',
        'income': serializer.data
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_income(request, income_id):
    household = request.user

    try:
        income = Income.objects.select_related(
            'earner', 'income_frequency'
        ).get(
            income_id=income_id,
            earner__household=household
        )
    except Income.DoesNotExist:
        return Response(
            {'error': 'Income not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = IncomeSerializer(income)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_income(request, income_id):
    household = request.user

    try:
        income = Income.objects.get(income_id=income_id, earner__household=household)
    except Income.DoesNotExist:
        return Response(
            {'error': 'Income not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    data = request.data

    if 'earner' in data:
        try:
            new_earner = Earner.objects.get(earner_id=data['earner'], household=household)
            income.earner = new_earner
        except Earner.DoesNotExist:
            return Response(
                {'error': 'Earner not found in your household'},
                status=status.HTTP_404_NOT_FOUND
            )

    if 'income_frequency' in data:
        try:
            new_freq = IncomeFrequency.objects.get(frequency_id=data['income_frequency'])
            income.income_frequency = new_freq
        except IncomeFrequency.DoesNotExist:
            return Response(
                {'error': 'Invalid income_frequency'},
                status=status.HTTP_400_BAD_REQUEST
            )

    for field in ['range_amount', 'income_startdate', 'next_payday']:
        if field in data:
            setattr(income, field, data[field])

    income.save()

    serializer = IncomeSerializer(income)

    return Response({
        'message': 'Income updated successfully',
        'income': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_income(request, income_id):
    household = request.user

    try:
        income = Income.objects.get(income_id=income_id, earner__household=household)
    except Income.DoesNotExist:
        return Response(
            {'error': 'Income not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    income.delete()

    return Response(
        {'message': 'Income deleted successfully'},
        status=status.HTTP_200_OK
    )


# ============================================================
# CATEGORY ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_categories(request):
    categories = ItemCategory.objects.all().order_by('category_id')
    serializer = ItemCategorySerializer(categories, many=True)

    return Response({
        'count': categories.count(),
        'categories': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_category(request):
    serializer = ItemCategorySerializer(data=request.data)

    if serializer.is_valid():
        category = serializer.save()

        return Response({
            'message': 'Category created successfully',
            'category': ItemCategorySerializer(category).data
        }, status=status.HTTP_201_CREATED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ============================================================
# BUDGET ITEM ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_budget_items(request):
    budget_items = BudgetItem.objects.select_related('category').order_by('item_id')
    serializer = BudgetItemSerializer(budget_items, many=True)

    return Response({
        'count': budget_items.count(),
        'budget_items': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_budget_item(request):
    serializer = BudgetItemSerializer(data=request.data)

    if serializer.is_valid():
        budget_item = serializer.save()

        return Response({
            'message': 'Budget item created successfully',
            'budget_item': BudgetItemSerializer(budget_item).data
        }, status=status.HTTP_201_CREATED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_budget_item(request, item_id):
    try:
        budget_item = BudgetItem.objects.get(item_id=item_id)
    except BudgetItem.DoesNotExist:
        return Response(
            {'error': 'Budget item not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = BudgetItemSerializer(budget_item, data=request.data)

    if serializer.is_valid():
        budget_item = serializer.save()

        return Response({
            'message': 'Budget item updated successfully',
            'budget_item': BudgetItemSerializer(budget_item).data
        }, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_budget_item(request, item_id):
    try:
        budget_item = BudgetItem.objects.get(item_id=item_id)
    except BudgetItem.DoesNotExist:
        return Response(
            {'error': 'Budget item not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    budget_item.delete()

    return Response(
        {'message': 'Budget item deleted successfully'},
        status=status.HTTP_200_OK
    )


# ============================================================
# BILLS / BUDGET ALLOCATIONS
# ============================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_bill(request):
    household = request.user

    try:
        income = Income.objects.get(
            income_id=request.data.get('income'),
            earner__household=household
        )
    except Income.DoesNotExist:
        return Response(
            {'error': 'Income not found for this household.'},
            status=status.HTTP_404_NOT_FOUND
        )

    data = request.data.copy()
    data['income'] = income.income_id

    serializer = BudgetAllocationSerializer(data=data)

    if serializer.is_valid():
        bill = serializer.save()

        return Response(
            BudgetAllocationSerializer(bill).data,
            status=status.HTTP_201_CREATED
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_bills(request):
    household = request.user

    bills = BudgetAllocation.objects.filter(
        income__earner__household=household
    ).select_related(
        'income',
        'item',
        'item__category'
    )

    serializer = BudgetAllocationSerializer(bills, many=True)

    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def bill_detail(request, allocation_id):
    household = request.user

    try:
        bill = BudgetAllocation.objects.select_related(
            'income',
            'item',
            'item__category'
        ).get(
            budget_allocation_id=allocation_id,
            income__earner__household=household
        )
    except BudgetAllocation.DoesNotExist:
        return Response(
            {'error': 'Bill not found.'},
            status=status.HTTP_404_NOT_FOUND
        )

    if request.method == 'GET':
        serializer = BudgetAllocationSerializer(bill)
        return Response(serializer.data, status=status.HTTP_200_OK)

    bill.delete()

    return Response(
        {'message': 'Bill deleted successfully.'},
        status=status.HTTP_200_OK
    )


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def confirm_bill(request, allocation_id):
    household = request.user

    try:
        bill = BudgetAllocation.objects.get(
            budget_allocation_id=allocation_id,
            income__earner__household=household
        )
    except BudgetAllocation.DoesNotExist:
        return Response(
            {'error': 'Bill not found.'},
            status=status.HTTP_404_NOT_FOUND
        )

    data = request.data.copy()
    data['is_confirmed'] = True

    serializer = BudgetAllocationSerializer(bill, data=data, partial=True)

    if serializer.is_valid():
        bill = serializer.save()

        return Response(
            BudgetAllocationSerializer(bill).data,
            status=status.HTTP_200_OK
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ============================================================
# BILL PRIORITIZATION / RULE ENGINE
# ============================================================

def classify_bill(bill):
    """
    Classifies a bill based on Chapter III rules (Section 3.1.2.2),
    extended to cover combinations the original rules did not address
    so that every confirmed bill receives a classification.

    Chapter III rules:
        Rule 1: Essential + Penalty + Grace = 0    -> High / Non-deferrable
        Rule 2: Essential + Penalty + Grace > 0    -> High / Non-deferrable
        Rule 3: Important + Penalty + Grace > 0    -> Medium / Deferrable
        Rule 4a: Discretionary + No penalty        -> Low / Deferrable
        Rule 4b: Discretionary + Penalty           -> Medium / Deferrable

    Extensions (same principle, fill the gaps):
        Extension A: Essential + Penalty + Grace > 0 but outside period -> Medium / Deferrable
        Extension B: Essential + No penalty                              -> Medium / Deferrable
        Extension C: Important + Penalty + Grace = 0                     -> Medium / Deferrable
        Extension D: Important + No penalty                              -> Low / Deferrable

    Returns:
        tuple: (priority_level, budget_classification, rule_id, reason)
    """
    item_desc = (bill.item.item_desc or '').lower() if bill.item else ''
    category_desc = (
        bill.item.category.category_desc or ''
    ).lower() if bill.item and bill.item.category else ''

    # ---- Category group detection ----
    essential_categories = {
        'utilities', 'housing', 'loans', 'transportation',
        'electricity', 'water', 'gas', 'rent',
    }
    important_categories = {
        'communication', 'education',
        'internet', 'subscription', 'phone', 'mobile', 'cable',
    }
    discretionary_categories = {
        'food', 'personal', 'other',
        'groceries', 'grocery', 'shopping', 'entertainment',
    }

    if category_desc in essential_categories:
        bill_type = 'essential'
    elif category_desc in important_categories:
        bill_type = 'important'
    elif category_desc in discretionary_categories:
        bill_type = 'discretionary'
    # Fallback: match by item_desc keywords
    elif any(kw in item_desc for kw in ['electricity', 'water', 'rent', 'loan', 'gas']):
        bill_type = 'essential'
    elif any(kw in item_desc for kw in ['internet', 'subscription', 'subscribe',
                                        'wifi', 'cable', 'phone', 'mobile']):
        bill_type = 'important'
    elif any(kw in item_desc for kw in ['groceries', 'grocery',
                                        'shopping', 'entertainment']):
        bill_type = 'discretionary'
    else:
        return (
            'Medium', 'Deferrable', 'Fallback',
            'Category could not be determined. Conservative default applied.'
        )

    penalty = bill.item.penalty_classification
    grace = bill.item.grace_period_days
    due_date = bill.actual_due_date
    in_period = (
        due_date is not None
        and bill.budget_start_date is not None
        and bill.budget_end_date is not None
        and bill.budget_start_date <= due_date <= bill.budget_end_date
    )

    # ==========================================================
    # ESSENTIAL CATEGORY
    # ==========================================================

    # Rule 1: Essential + Penalty + Grace = 0
    if bill_type == 'essential' and penalty is True and grace == 0:
        return (
            'High', 'Non-deferrable', 'Rule 1',
            'Essential bill with a late penalty and no grace period. '
            'Must be settled within the current pay period.'
        )

    # Rule 2: Essential + Penalty + Grace > 0 + due in current period
    if bill_type == 'essential' and penalty is True and grace > 0 and in_period:
        return (
            'High', 'Non-deferrable', 'Rule 2',
            f'Essential bill with a {grace}-day grace period. '
            f'The grace period only delays the penalty — it does not remove it.'
        )

    # Extension A: Essential + Penalty + Grace > 0 + due outside period
    if bill_type == 'essential' and penalty is True and grace > 0 and not in_period:
        return (
            'Medium', 'Deferrable', 'Rule 2 (out of period)',
            'Essential bill due outside the current pay period — '
            'defer to the period when it is actually due.'
        )

    # Extension B: Essential + No penalty
    if bill_type == 'essential' and penalty is False:
        return (
            'Medium', 'Deferrable', 'Rule 2 extension (no penalty)',
            'Essential service without a late penalty — '
            'deferrable only if budget requires it.'
        )

    # ==========================================================
    # IMPORTANT (NON-ESSENTIAL) CATEGORY
    # ==========================================================

    # Rule 3: Important + Penalty + Grace > 0
    if bill_type == 'important' and penalty is True and grace > 0:
        return (
            'Medium', 'Deferrable', 'Rule 3',
            f'Non-essential but penalized. Has a {grace}-day grace period — '
            f'safe to defer when budget is tight.'
        )

    # Extension C: Important + Penalty + Grace = 0
    if bill_type == 'important' and penalty is True and grace == 0:
        return (
            'Medium', 'Deferrable', 'Rule 3 (no grace)',
            'Non-essential service with a late penalty and no grace period. '
            'Deferrable if budget is tight.'
        )

    # Extension D: Important + No penalty
    if bill_type == 'important' and penalty is False:
        return (
            'Low', 'Deferrable', 'Rule 3 (no penalty)',
            'Non-essential service with no late penalty — freely deferrable.'
        )

    # ==========================================================
    # DISCRETIONARY CATEGORY
    # ==========================================================

    # Rule 4a: Discretionary + No penalty
    if bill_type == 'discretionary' and penalty is False:
        return (
            'Low', 'Deferrable', 'Rule 4a',
            'Discretionary expense with no penalty — freely deferrable to any future period.'
        )

    # Rule 4b: Discretionary + Penalty
    if bill_type == 'discretionary' and penalty is True:
        return (
            'Medium', 'Deferrable', 'Rule 4b',
            f'Discretionary but penalized. Has a {grace}-day grace period — '
            f'settle soon to avoid charges.'
        )

    # Final fallback — should rarely be reached
    return (
        'Medium', 'Deferrable', 'Fallback',
        'No specific rule matched. Conservative default applied.'
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def prioritize_bills(request):
    household = request.user

    bills = BudgetAllocation.objects.filter(
        income__earner__household=household,
        is_confirmed=True
    ).select_related(
        'income',
        'item',
        'item__category'
    )

    prioritized_count = 0
    unclassified_count = 0

    for bill in bills:
        priority, classification, rule_id, reason = classify_bill(bill)

        bill.priority_level = priority
        bill.budget_classification = classification
        bill.rule_applied = rule_id

        bill.save(
            update_fields=[
                'priority_level',
                'budget_classification',
                'rule_applied'
            ]
        )

        if priority is not None:
            prioritized_count += 1
        else:
            unclassified_count += 1

    return Response({
        'message': 'Bill prioritization completed.',
        'prioritized_count': prioritized_count,
        'unclassified_count': unclassified_count
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def prioritized_bills(request):
    household = request.user

    bills = BudgetAllocation.objects.filter(
        income__earner__household=household,
        priority_level__isnull=False
    ).select_related(
        'income',
        'item',
        'item__category'
    )

    serializer = BudgetAllocationSerializer(bills, many=True)

    return Response(serializer.data, status=status.HTTP_200_OK)


# ============================================================
# OCR BILL SCAN
# ============================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def scan_bill(request):
    """Upload a bill photo or PDF receipt → OCR extraction. Does NOT save to DB."""

    if 'image' not in request.FILES:
        return Response(
            {'error': 'No file provided. Use form field "image".'},
            status=status.HTTP_400_BAD_REQUEST
        )

    uploaded_file = request.FILES['image']
    content_type = uploaded_file.content_type or ''
    is_pdf = content_type == 'application/pdf' or uploaded_file.name.lower().endswith('.pdf')

    if not (content_type.startswith('image/') or is_pdf):
        return Response(
            {'error': f'Invalid file type: {content_type}. Upload a photo (JPEG/PNG) or a PDF receipt.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if uploaded_file.size > 10 * 1024 * 1024:
        return Response(
            {'error': 'File too large. Maximum 10 MB.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        extracted = scan_bill_image(uploaded_file, is_pdf=is_pdf)
    except Exception as e:
        return Response(
            {'error': f'OCR failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return Response({
        'message': 'File processed. Please review the extracted fields.',
        'extracted': {
            'amount': extracted['amount'],
            'due_date': extracted['due_date'],
            'merchant': extracted['merchant'],
        },
        'raw_text': extracted['raw_text'],
        'note': 'Nothing has been saved. Confirm via POST /api/bills/<id>/confirm/ once verified.'
    }, status=status.HTTP_200_OK)


# ============================================================
# PRIORITY 6: FINANCIAL RISK ASSESSMENT & RECOMMENDATIONS
# ============================================================

def _compute_risk_for_household(household):
    """
    Shared helper — computes the risk assessment for a household.
    Used by assess_risk, get_recommendations, and list_notifications.
    """

    incomes = Income.objects.filter(earner__household=household)

    combined_income_min = Decimal('0')
    combined_income_max = Decimal('0')
    next_payday = None

    for inc in incomes:
        lo, hi = _parse_range(inc.range_amount)
        combined_income_min += lo
        combined_income_max += hi
        if inc.next_payday and (next_payday is None or inc.next_payday < next_payday):
            next_payday = inc.next_payday

    bills = BudgetAllocation.objects.filter(
        income__earner__household=household,
        is_confirmed=True,
    )

    alloc_min = Decimal('0')
    alloc_max = Decimal('0')

    for b in bills:
        lo, hi = _parse_range(b.budget_amount_range)
        alloc_min += lo
        alloc_max += hi

    days_until = compute_days_until_next_payday(next_payday)

    result = assess_financial_risk(
        combined_income_min=combined_income_min,
        combined_income_max=combined_income_max,
        total_bill_allocations_min=alloc_min,
        total_bill_allocations_max=alloc_max,
        daily_expense_min=(
            household.daily_food_expense_min + household.daily_transport_expense_min
        ),
        daily_expense_max=(
            household.daily_food_expense_max + household.daily_transport_expense_max
        ),
        days_until_next_payday=days_until,
    )

    result['next_payday'] = next_payday
    return result


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def assess_risk(request):
    """Compute financial risk using Chapter III's 3-step formula."""
    result = _compute_risk_for_household(request.user)
    return Response(result, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_recommendations(request):
    """Suggest deferrable bills when the household is At Risk or Critical."""
    household = request.user

    risk_data = _compute_risk_for_household(household)

    deferrable_bills = BudgetAllocation.objects.filter(
        income__earner__household=household,
        is_confirmed=True,
        budget_classification='Deferrable'
    ).select_related('item', 'item__category')

    deferred_list = []
    total_freed = Decimal('0')

    for b in deferrable_bills:
        lo, hi = _parse_range(b.budget_amount_range)
        total_freed += lo

        deferred_list.append({
            'budget_allocation_id': b.budget_allocation_id,
            'item_desc': b.item.item_desc if b.item else None,
            'category_desc': (
                b.item.category.category_desc
                if b.item and b.item.category else None
            ),
            'priority_level': b.priority_level,
            'budget_classification': b.budget_classification,
            'rule_applied': b.rule_applied,
            'amount_range': b.budget_amount_range,
            'actual_due_date': b.actual_due_date,
        })

    return Response({
        'risk_level': risk_data['risk_level'],
        'label': risk_data['label'],
        'color_indicator': risk_data['color_indicator'],
        'activate_deferral': risk_data['label'] in ('AT RISK', 'CRITICAL'),
        'deferrable_bills': deferred_list,
        'estimated_budget_freed': str(total_freed),
        'message': (
            'Your remaining budget may not cover daily expenses until payday. '
            'Consider deferring the bills below.'
            if risk_data['label'] in ('AT RISK', 'CRITICAL')
            else 'Your current budget looks stable. No action needed.'
        ),
    }, status=status.HTTP_200_OK)


# ============================================================
# PRIORITY 7: BUDGET ALLOCATION SUMMARY
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def budget_allocation_summary(request):
    """Return the household's budget allocation grouped by category."""
    household = request.user

    bills = BudgetAllocation.objects.filter(
        income__earner__household=household,
        is_confirmed=True,
    ).select_related('item', 'item__category')

    by_category = {}
    total_min = Decimal('0')
    total_max = Decimal('0')
    period_half = None

    for b in bills:
        cat_name = (
            b.item.category.category_desc
            if b.item and b.item.category else 'Uncategorized'
        )
        lo, hi = _parse_range(b.budget_amount_range)

        total_min += lo
        total_max += hi

        if period_half is None and b.period_half:
            period_half = b.period_half

        if cat_name not in by_category:
            by_category[cat_name] = {
                'min': Decimal('0'),
                'max': Decimal('0'),
                'bill_count': 0
            }

        by_category[cat_name]['min'] += lo
        by_category[cat_name]['max'] += hi
        by_category[cat_name]['bill_count'] += 1

    categories = [
        {
            'category': cat,
            'min': str(data['min']),
            'max': str(data['max']),
            'bill_count': data['bill_count'],
        }
        for cat, data in by_category.items()
    ]

    return Response({
        'period_half': period_half,
        'total_allocated_min': str(total_min),
        'total_allocated_max': str(total_max),
        'by_category': categories,
    }, status=status.HTTP_200_OK)


# ============================================================
# PRIORITY 8: NOTIFICATIONS / DUE REMINDERS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_notifications(request):
    """Return due date reminders and risk alerts for the household."""
    household = request.user
    today = date.today()
    alerts = []

    bills = BudgetAllocation.objects.filter(
        income__earner__household=household,
        is_confirmed=True,
        is_paid=False,
    ).select_related('item', 'item__category')

    for b in bills:
        if not b.actual_due_date:
            continue

        days_until_due = (b.actual_due_date - today).days
        item_name = b.item.item_desc if b.item else 'Bill'

        if days_until_due < 0:
            alerts.append({
                'type': 'overdue',
                'severity': 'critical',
                'title': f"{item_name} is overdue",
                'message': (
                    f"Due {b.actual_due_date} "
                    f"({-days_until_due} day(s) ago). "
                    f"{'Non-deferrable' if b.budget_classification == 'Non-deferrable' else 'Deferrable'}."
                ),
                'bill_id': b.budget_allocation_id,
                'due_date': b.actual_due_date,
            })

        elif days_until_due <= 3:
            alerts.append({
                'type': 'due_soon',
                'severity': 'high' if b.priority_level == 'High' else 'medium',
                'title': f"{item_name} due in {days_until_due} day(s)",
                'message': f"Due {b.actual_due_date}. Priority: {b.priority_level or 'N/A'}.",
                'bill_id': b.budget_allocation_id,
                'due_date': b.actual_due_date,
            })

        elif days_until_due <= 7:
            alerts.append({
                'type': 'upcoming',
                'severity': 'low',
                'title': f"{item_name} due in {days_until_due} day(s)",
                'message': f"Due {b.actual_due_date}.",
                'bill_id': b.budget_allocation_id,
                'due_date': b.actual_due_date,
            })

    risk_data = _compute_risk_for_household(household)

    if risk_data['label'] in ('AT RISK', 'CRITICAL'):
        alerts.insert(0, {
            'type': 'risk_alert',
            'severity': 'high' if risk_data['label'] == 'CRITICAL' else 'medium',
            'title': f"Financial status: {risk_data['label']}",
            'message': (
                f"Remaining budget is {risk_data['remaining_budget_min']}. "
                f"Consider deferring bills."
            ),
            'risk_level': risk_data['risk_level'],
        })

    order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    alerts.sort(key=lambda a: order.get(a['severity'], 99))

    return Response({
        'count': len(alerts),
        'notifications': alerts,
    }, status=status.HTTP_200_OK)