from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.hashers import make_password, check_password
from .models import Household
import requests
import os


def get_tokens_for_user(household):
    """Generate JWT tokens for a household user"""
    refresh = RefreshToken()
    refresh['household_id'] = household.household_id
    refresh['email'] = household.email
    refresh['first_name'] = household.first_name
    refresh['last_name'] = household.last_name

    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    """Register a new household account"""
    data = request.data

    # Check required fields
    required_fields = ['first_name', 'last_name', 'email', 'password']
    for field in required_fields:
        if not data.get(field):
            return Response(
                {'error': f'{field} is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # Check if email already exists
    if Household.objects.filter(email=data['email']).exists():
        return Response(
            {'error': 'Email already registered'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Create household account
    household = Household.objects.create(
        first_name=data['first_name'],
        last_name=data['last_name'],
        email=data['email'],
        password=make_password(data['password']),
        total_members=0,
        no_of_earners=0,
        no_of_dependents=0,
        housing_type='',
        daily_food_expense_min=0,
        daily_food_expense_max=0,
        daily_transport_expense_min=0,
        daily_transport_expense_max=0,
        survival_threshold_min=0,
        survival_threshold_max=0,
    )

    tokens = get_tokens_for_user(household)

    return Response({
        'message': 'Account created successfully',
        'household_id': household.household_id,
        'first_name': household.first_name,
        'last_name': household.last_name,
        'email': household.email,
        'tokens': tokens
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    """Login with email and password"""
    data = request.data

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return Response(
            {'error': 'Email and password are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Find household by email
    try:
        household = Household.objects.get(email=email)
    except Household.DoesNotExist:
        return Response(
            {'error': 'Invalid email or password'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # Check password
    if not check_password(password, household.password):
        return Response(
            {'error': 'Invalid email or password'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # Update last login
    from django.utils import timezone
    household.last_login = timezone.now()
    household.save()

    tokens = get_tokens_for_user(household)

    return Response({
        'message': 'Login successful',
        'household_id': household.household_id,
        'first_name': household.first_name,
        'last_name': household.last_name,
        'email': household.email,
        'tokens': tokens
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def google_login(request):
    """Login or register using Google OAuth token"""
    google_token = request.data.get('google_token')

    if not google_token:
        return Response(
            {'error': 'Google token is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verify token with Google
    google_url = f'https://oauth2.googleapis.com/tokeninfo?id_token={google_token}'
    google_response = requests.get(google_url)

    if google_response.status_code != 200:
        return Response(
            {'error': 'Invalid Google token'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    google_data = google_response.json()

    # Verify the token is for our app
    if google_data.get('aud') != os.getenv('GOOGLE_CLIENT_ID'):
        return Response(
            {'error': 'Token not valid for this application'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    email = google_data.get('email')
    first_name = google_data.get('given_name', '')
    last_name = google_data.get('family_name', '')

    # Check if household exists
    try:
        household = Household.objects.get(email=email)
        # Existing user — log them in
        message = 'Login successful'
    except Household.DoesNotExist:
        # New user — create account
        household = Household.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=make_password(None),  # No password for Google users
            total_members=0,
            no_of_earners=0,
            no_of_dependents=0,
            housing_type='',
            daily_food_expense_min=0,
            daily_food_expense_max=0,
            daily_transport_expense_min=0,
            daily_transport_expense_max=0,
            survival_threshold_min=0,
            survival_threshold_max=0,
        )
        message = 'Account created successfully'

    # Update last login
    from django.utils import timezone
    household.last_login = timezone.now()
    household.save()

    tokens = get_tokens_for_user(household)

    return Response({
        'message': message,
        'household_id': household.household_id,
        'first_name': household.first_name,
        'last_name': household.last_name,
        'email': household.email,
        'tokens': tokens
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """Logout by blacklisting the refresh token"""
    try:
        refresh_token = request.data.get('refresh_token')
        token = RefreshToken(refresh_token)
        token.blacklist()
        return Response(
            {'message': 'Logged out successfully'},
            status=status.HTTP_200_OK
        )
    except Exception:
        return Response(
            {'error': 'Invalid token'},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_profile(request):
    """Get current logged in user profile"""
    token = request.auth
    household_id = token.get('household_id')

    try:
        household = Household.objects.get(household_id=household_id)
        return Response({
            'household_id': household.household_id,
            'first_name': household.first_name,
            'last_name': household.last_name,
            'email': household.email,
            'total_members': household.total_members,
            'no_of_earners': household.no_of_earners,
            'no_of_dependents': household.no_of_dependents,
            'housing_type': household.housing_type,
        }, status=status.HTTP_200_OK)
    except Household.DoesNotExist:
        return Response(
            {'error': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )