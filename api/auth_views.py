from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from .models import Household
import requests
import os


def get_tokens_for_user(household):
    """Generate JWT tokens for a household user without creating OutstandingToken entries."""
    user_id_claim = settings.SIMPLE_JWT.get('USER_ID_CLAIM', 'user_id')

    # Manually build a RefreshToken
    refresh = RefreshToken()
    refresh[user_id_claim] = household.household_id
    refresh['email'] = household.email
    refresh['first_name'] = household.first_name
    refresh['last_name'] = household.last_name

    # Build the access token off the refresh token (so it inherits the same claims)
    access = refresh.access_token
    access['email'] = household.email
    access['first_name'] = household.first_name
    access['last_name'] = household.last_name

    return {
        'refresh': str(refresh),
        'access': str(access),
    }


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def register(request):
    """Register a new household account"""
    data = request.data

    required_fields = ['first_name', 'last_name', 'email', 'password']
    for field in required_fields:
        if not data.get(field):
            return Response(
                {'error': f'{field} is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

    if Household.objects.filter(email=data['email']).exists():
        return Response(
            {'error': 'Email already registered'},
            status=status.HTTP_400_BAD_REQUEST
        )

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
@authentication_classes([])
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

    try:
        household = Household.objects.get(email=email)
    except Household.DoesNotExist:
        return Response(
            {'error': 'Invalid email or password'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    if not check_password(password, household.password):
        return Response(
            {'error': 'Invalid email or password'},
            status=status.HTTP_401_UNAUTHORIZED
        )

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
@authentication_classes([])
@permission_classes([AllowAny])
def google_login(request):
    """Login or register using Google OAuth token"""
    google_token = request.data.get('google_token')

    if not google_token:
        return Response(
            {'error': 'Google token is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    google_url = f'https://oauth2.googleapis.com/tokeninfo?id_token={google_token}'
    google_response = requests.get(google_url)

    if google_response.status_code != 200:
        return Response(
            {'error': 'Invalid Google token'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    google_data = google_response.json()

    if google_data.get('aud') != os.getenv('GOOGLE_CLIENT_ID'):
        return Response(
            {'error': 'Token not valid for this application'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    email = google_data.get('email')
    first_name = google_data.get('given_name', '')
    last_name = google_data.get('family_name', '')

    try:
        household = Household.objects.get(email=email)
        message = 'Login successful'
    except Household.DoesNotExist:
        household = Household.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=make_password(None),
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
@authentication_classes([])
@permission_classes([AllowAny])
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
@authentication_classes([])
@permission_classes([AllowAny])
def debug_token(request):
    """Debug endpoint to check token contents"""
    auth_header = request.headers.get('Authorization', '')

    if not auth_header:
        return Response({'error': 'No Authorization header found'})

    if not auth_header.startswith('Bearer '):
        return Response({'error': 'Must start with Bearer'})

    token = auth_header.split(' ')[1]

    try:
        decoded = AccessToken(token)
        return Response({
            'token_contents': dict(decoded),
            'has_household_id': 'household_id' in decoded,
        })
    except Exception as e:
        return Response({'error': str(e)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_profile(request):
    """Get current logged in user profile"""
    try:
        household = request.user
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
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )