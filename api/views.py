from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import Household, Earner, Income, IncomeFrequency
from .serializers import (
    HouseholdSerializer,
    EarnerSerializer,
    IncomeSerializer,
    IncomeFrequencySerializer,
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