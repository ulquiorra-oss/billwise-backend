from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

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
    household = request.user  # set by HouseholdJWTAuthentication
    serializer = HouseholdSerializer(household)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_household(request):
    """
    Update the current household's profile.
    PATCH = partial update (recommended)
    PUT   = full update
    """
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
    """List all earners for the current household."""
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
    """Create a new earner under the current household."""
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
    """Get a specific earner's details."""
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
    """Update an earner's details."""
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
    """Delete an earner."""
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
# INCOME FREQUENCY ENDPOINTS (Reference table — read only)
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_income_frequencies(request):
    """List all available income frequencies (Weekly, Bi-monthly, Monthly)."""
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
    """List all income records belonging to the current household."""
    household = request.user
    incomes = Income.objects.filter(
        earner__household=household
    ).select_related('earner', 'income_frequency').order_by('-income_startdate')

    serializer = IncomeSerializer(incomes, many=True)
    return Response({
        'count': incomes.count(),
        'incomes': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_income(request):
    """Create a new income record for one of the household's earners."""
    household = request.user
    data = request.data

    required = ['earner', 'income_frequency', 'range_amount', 'income_startdate', 'next_payday']
    for field in required:
        if data.get(field) in (None, ''):
            return Response(
                {'error': f'{field} is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # SECURITY: verify the earner belongs to the current household
    try:
        earner = Earner.objects.get(
            earner_id=data['earner'],
            household=household
        )
    except Earner.DoesNotExist:
        return Response(
            {'error': 'Earner not found in your household'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Verify the frequency exists
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
    """Get one income record, scoped to the current household."""
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
    """Update an income record."""
    household = request.user
    try:
        income = Income.objects.get(
            income_id=income_id,
            earner__household=household
        )
    except Income.DoesNotExist:
        return Response(
            {'error': 'Income not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    data = request.data

    # If earner is being changed, verify it belongs to the household
    if 'earner' in data:
        try:
            new_earner = Earner.objects.get(
                earner_id=data['earner'],
                household=household
            )
            income.earner = new_earner
        except Earner.DoesNotExist:
            return Response(
                {'error': 'Earner not found in your household'},
                status=status.HTTP_404_NOT_FOUND
            )

    # If frequency is being changed, verify it exists
    if 'income_frequency' in data:
        try:
            new_freq = IncomeFrequency.objects.get(
                frequency_id=data['income_frequency']
            )
            income.income_frequency = new_freq
        except IncomeFrequency.DoesNotExist:
            return Response(
                {'error': 'Invalid income_frequency'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # Update other scalar fields
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
    """Delete an income record."""
    household = request.user
    try:
        income = Income.objects.get(
            income_id=income_id,
            earner__household=household
        )
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
    """List all budget item categories."""
    categories = ItemCategory.objects.all().order_by('category_id')
    serializer = ItemCategorySerializer(categories, many=True)

    return Response({
        'count': categories.count(),
        'categories': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_category(request):
    """Create a new budget item category."""
    serializer = ItemCategorySerializer(data=request.data)

    if serializer.is_valid():
        category = serializer.save()

        return Response({
            'message': 'Category created successfully',
            'category': ItemCategorySerializer(category).data
        }, status=status.HTTP_201_CREATED)

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


# ============================================================
# BUDGET ITEM ENDPOINTS
# ============================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_budget_items(request):
    """List all budget items with their categories."""
    budget_items = BudgetItem.objects.select_related(
        'category'
    ).order_by('item_id')

    serializer = BudgetItemSerializer(budget_items, many=True)

    return Response({
        'count': budget_items.count(),
        'budget_items': serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_budget_item(request):
    """Create a new budget item."""
    serializer = BudgetItemSerializer(data=request.data)

    if serializer.is_valid():
        budget_item = serializer.save()

        return Response({
            'message': 'Budget item created successfully',
            'budget_item': BudgetItemSerializer(budget_item).data
        }, status=status.HTTP_201_CREATED)

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_budget_item(request, item_id):
    """Update an existing budget item."""

    try:
        budget_item = BudgetItem.objects.get(item_id=item_id)
    except BudgetItem.DoesNotExist:
        return Response(
            {'error': 'Budget item not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = BudgetItemSerializer(
        budget_item,
        data=request.data
    )

    if serializer.is_valid():
        budget_item = serializer.save()

        return Response({
            'message': 'Budget item updated successfully',
            'budget_item': BudgetItemSerializer(budget_item).data
        }, status=status.HTTP_200_OK)

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_budget_item(request, item_id):
    """Delete an existing budget item."""

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

    # Make sure the selected income belongs to this household
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

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


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

        return Response(
            serializer.data,
            status=status.HTTP_200_OK
        )

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

    serializer = BudgetAllocationSerializer(
        bill,
        data=data,
        partial=True
    )

    if serializer.is_valid():
        bill = serializer.save()

        return Response(
            BudgetAllocationSerializer(bill).data,
            status=status.HTTP_200_OK
        )

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )

# ============================================================
# BILL PRIORITIZATION / RULE ENGINE
# ============================================================

def classify_bill(bill):
    """
    Classifies a bill based on the rules defined in the
    Capstone documentation.

    The bill type is determined from the bill/item description
    using the examples provided in the documentation:
    Essential: Electricity, Water, Rent, Loan
    Important: Internet, Subscription
    Discretionary: Groceries, Shopping, Entertainment
    """

    item_desc = (bill.item.item_desc or '').lower()

    # Determine bill category based on the examples in the capstone.
    if any(keyword in item_desc for keyword in [
        'electricity',
        'water',
        'rent',
        'loan'
    ]):
        bill_type = 'essential'

    elif any(keyword in item_desc for keyword in [
        'internet',
        'subscription'
    ]):
        bill_type = 'important'

    elif any(keyword in item_desc for keyword in [
        'groceries',
        'shopping',
        'entertainment'
    ]):
        bill_type = 'discretionary'

    else:
        # No documented example matches this bill.
        return None, None

    penalty = bill.item.penalty_classification
    grace_period = bill.item.grace_period_days
    due_date = bill.actual_due_date

    # --------------------------------------------------------
    # RULE 1
    # Essential + penalty + no grace period
    # => HIGH + NON-DEFERRABLE
    # --------------------------------------------------------
    if (
        bill_type == 'essential'
        and penalty is True
        and grace_period == 0
    ):
        return 'High', 'Non-deferrable'

    # --------------------------------------------------------
    # RULE 2
    # Essential + penalty + grace period > 0
    # + due date within current pay period
    # => HIGH + NON-DEFERRABLE
    # --------------------------------------------------------
    if (
        bill_type == 'essential'
        and penalty is True
        and grace_period > 0
        and due_date is not None
        and bill.budget_start_date <= due_date <= bill.budget_end_date
    ):
        return 'High', 'Non-deferrable'

    # --------------------------------------------------------
    # RULE 3
    # Important + penalty + grace period > 0
    # => MEDIUM + DEFERRABLE
    # --------------------------------------------------------
    if (
        bill_type == 'important'
        and penalty is True
        and grace_period > 0
    ):
        return 'Medium', 'Deferrable'

    # --------------------------------------------------------
    # RULE 4a
    # Discretionary + no penalty
    # => LOW + DEFERRABLE
    # --------------------------------------------------------
    if (
        bill_type == 'discretionary'
        and penalty is False
    ):
        return 'Low', 'Deferrable'

    # --------------------------------------------------------
    # RULE 4b
    # Discretionary + penalty
    # => MEDIUM + DEFERRABLE
    # --------------------------------------------------------
    if (
        bill_type == 'discretionary'
        and penalty is True
    ):
        return 'Medium', 'Deferrable'

    # No documented rule matches the bill.
    return None, None


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
        priority, classification = classify_bill(bill)

        bill.priority_level = priority
        bill.budget_classification = classification

        bill.save(
            update_fields=[
                'priority_level',
                'budget_classification'
            ]
        )

        if priority is not None:
            prioritized_count += 1
        else:
            unclassified_count += 1

    return Response(
        {
            'message': 'Bill prioritization completed.',
            'prioritized_count': prioritized_count,
            'unclassified_count': unclassified_count
        },
        status=status.HTTP_200_OK
    )


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

    serializer = BudgetAllocationSerializer(
        bills,
        many=True
    )

    return Response(
        serializer.data,
        status=status.HTTP_200_OK
    )