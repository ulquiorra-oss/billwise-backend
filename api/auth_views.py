from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from .models import Household, GoogleOTPVerification, EmailOTPVerification, TrustedDevice
import requests
import os
import secrets
import uuid
import hashlib
import logging
from datetime import timedelta

from django.core.mail import send_mail
from django.db import transaction
from django.db import IntegrityError, transaction


logger = logging.getLogger(__name__)


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

def hash_device_token(token):
    """Hash a device token before storing or comparing it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_trusted_device(household, device_name=""):
    """Create a trusted device and return its raw token once."""
    raw_token = secrets.token_urlsafe(32)

    trusted_device = TrustedDevice.objects.create(
        household=household,
        device_token_hash=hash_device_token(raw_token),
        device_name=device_name[:100],
        expires_at=timezone.now() + timedelta(days=30),
    )

    return trusted_device, raw_token

def get_valid_trusted_device(household, raw_token):
    """Return a valid trusted device, or None if it cannot be trusted."""
    if not raw_token:
        return None

    token_hash = hash_device_token(raw_token)

    try:
        return TrustedDevice.objects.get(
            household=household,
            device_token_hash=token_hash,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
    except TrustedDevice.DoesNotExist:
        return None

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def register(request):
    """Create a household account and send an email verification OTP."""
    data = request.data

    first_name = str(data.get('first_name') or '').strip()
    last_name = str(data.get('last_name') or '').strip()
    email = str(data.get('email') or '').strip().lower()
    password = data.get('password')

    if not first_name or not last_name or not email or not password:
        return Response(
            {'error': 'first_name, last_name, email, and password are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if Household.objects.filter(email__iexact=email).exists():
        return Response(
            {'error': 'Email already registered'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    household = None

    try:
        with transaction.atomic():
            household = Household.objects.create(
                first_name=first_name,
                last_name=last_name,
                email=email,
                password=make_password(password),
                email_verified=False,
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

            otp = f'{secrets.randbelow(1_000_000):06d}'
            now = timezone.now()

            challenge = EmailOTPVerification.objects.create(
                household=household,
                purpose='registration',
                otp_hash=make_password(otp),
                expires_at=now + timedelta(minutes=5),
                last_sent_at=now,
            )

            try:
                sent = send_mail(
                    subject='Verify your BillWise email',
                    message=(
                        f'Hi {first_name},\n\n'
                        f'Your BillWise email verification code is: {otp}\n\n'
                        'This code expires in 5 minutes. '
                        'If you did not create this account, please ignore this email.'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )

            except Exception as exc:
                logger.exception(
                    'Registration verification email failed for household_id=%s',
                    household.household_id,
                )
                raise RuntimeError(
                    'Verification email could not be sent'
                ) from exc

            if sent != 1:
                logger.error(
                    'Registration verification email returned %s for household_id=%s',
                    sent,
                    household.household_id,
                )
                raise RuntimeError(
                    'Verification email could not be sent'
                )

    except RuntimeError:
        logger.exception('Registration aborted before email send')

        return Response(
            {
                'error': (
                    'We could not send your verification email. '
                    'Please try registering again.'
                )
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    except Exception:
        logger.exception('Registration failed unexpectedly')

        # The atomic transaction rolls back failed account creation.
        # Do not refresh or delete a potentially rolled-back instance.
        if Household.objects.filter(email__iexact=email).exists():
            return Response(
                {'error': 'Email already registered'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'error': (
                    'Could not create the account. Please try again.'
                )
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    local, separator, domain = email.partition('@')
    masked_email = f'{local[:1]}***@{domain}' if separator else '***'

    return Response(
        {
            'message': 'Account created. Enter the verification code sent to your email.',
            'email': masked_email,
            'otp_required': True,
            'challenge_id': str(challenge.challenge_id),
            'expires_in': 300,
            'resend_after': 60,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def verify_email(request):
    """Verify a newly registered household email using its OTP."""

    challenge_id = request.data.get('challenge_id')
    otp = str(request.data.get('otp') or '').strip()

    if not challenge_id or not otp:
        return Response(
            {'error': 'challenge_id and otp are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(otp) != 6 or not otp.isdigit():
        return Response(
            {'error': 'Enter the six-digit verification code'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        challenge_uuid = uuid.UUID(str(challenge_id))
    except (ValueError, TypeError, AttributeError):
        return Response(
            {'error': 'Invalid verification challenge'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        try:
            challenge = (
                EmailOTPVerification.objects
                .select_for_update()
                .select_related('household')
                .get(
                    challenge_id=challenge_uuid,
                    purpose='registration',
                )
            )
        except EmailOTPVerification.DoesNotExist:
            return Response(
                {'error': 'Invalid verification challenge'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()

        if challenge.consumed_at is not None:
            return Response(
                {'error': 'This verification code has already been used'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.expires_at <= now:
            return Response(
                {'error': 'Verification code expired. Request a new code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.attempts >= 5:
            return Response(
                {'error': 'Too many attempts. Request a new code.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if not check_password(otp, challenge.otp_hash):
            challenge.attempts += 1
            challenge.save(update_fields=['attempts'])

            remaining = max(0, 5 - challenge.attempts)

            return Response(
                {
                    'error': 'Incorrect verification code',
                    'attempts_remaining': remaining,
                },
                status=(
                    status.HTTP_429_TOO_MANY_REQUESTS
                    if remaining == 0
                    else status.HTTP_400_BAD_REQUEST
                ),
            )

        household = challenge.household
        household.email_verified = True
        household.last_login = now
        household.save(update_fields=['email_verified', 'last_login'])

        challenge.consumed_at = now
        challenge.save(update_fields=['consumed_at'])

        tokens = get_tokens_for_user(household)

    return Response(
        {
            'message': 'Email verified successfully',
            'household_id': household.household_id,
            'first_name': household.first_name,
            'last_name': household.last_name,
            'email': household.email,
            'tokens': tokens,
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def verify_login_otp(request):
    """Verify a login OTP, issue JWT tokens, and optionally trust the device."""
    challenge_id = str(request.data.get('challenge_id') or '').strip()
    otp = str(request.data.get('otp') or '').strip()
    trust_device = request.data.get('trust_device') is True
    device_name = str(request.data.get('device_name') or '').strip()[:100]

    if not challenge_id or not otp:
        return Response(
            {'error': 'Challenge ID and OTP are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not (otp.isdigit() and len(otp) == 6):
        return Response(
            {'error': 'OTP must be a 6-digit code'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        challenge_uuid = uuid.UUID(challenge_id)
    except (ValueError, TypeError, AttributeError):
        return Response(
            {'error': 'Invalid challenge ID'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        try:
            challenge = (
                EmailOTPVerification.objects
                .select_for_update()
                .select_related('household')
                .get(
                    challenge_id=challenge_uuid,
                    purpose='login',
                )
            )
        except EmailOTPVerification.DoesNotExist:
            return Response(
                {'error': 'Invalid login verification challenge'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.consumed_at is not None:
            return Response(
                {'error': 'This OTP has already been used'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.expires_at <= timezone.now():
            return Response(
                {'error': 'OTP has expired. Please request a new code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.attempts >= 5:
            return Response(
                {'error': 'Too many attempts. Request a new code.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if not check_password(otp, challenge.otp_hash):
            challenge.attempts += 1
            challenge.save(update_fields=['attempts'])

            remaining = max(0, 5 - challenge.attempts)
            return Response(
                {
                    'error': 'Incorrect OTP',
                    'attempts_remaining': remaining,
                },
                status=(
                    status.HTTP_429_TOO_MANY_REQUESTS
                    if remaining == 0
                    else status.HTTP_400_BAD_REQUEST
                ),
            )

        household = challenge.household

        if not household.email_verified:
            challenge.consumed_at = timezone.now()
            challenge.save(update_fields=['consumed_at'])

            return Response(
                {'error': 'Please verify your email before logging in.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=['consumed_at'])

        household.last_login = timezone.now()
        household.save(update_fields=['last_login'])

        tokens = get_tokens_for_user(household)

        response_data = {
            'message': 'Login successful',
            'user': {
                'household_id': household.household_id,
                'email': household.email,
                'first_name': household.first_name,
                'last_name': household.last_name,
            },
            'tokens': tokens,
        }

        if trust_device:
            trusted_device, raw_device_token = create_trusted_device(
                household,
                device_name=device_name,
            )
            response_data['trusted_device'] = {
                'device_id': str(trusted_device.device_id),
                'device_token': raw_device_token,
                'expires_at': trusted_device.expires_at.isoformat(),
            }

        return Response(response_data, status=status.HTTP_200_OK)


def can_request_login_otp(household, cooldown_seconds=60, max_requests=5):
    """
    Check whether another login OTP can be requested.

    Allows a new request after the cooldown, but limits the number
    of login OTP challenges created for an account within one hour.
    """
    now = timezone.now()
    one_hour_ago = now - timedelta(hours=1)

    recent_challenges = EmailOTPVerification.objects.filter(
        household=household,
        purpose="login",
        last_sent_at__gte=one_hour_ago,
    ).order_by("-last_sent_at")

    if recent_challenges.count() >= max_requests:
        return False, "Too many login code requests. Please try again later."

    latest = recent_challenges.first()
    if latest and latest.last_sent_at > now - timedelta(seconds=cooldown_seconds):
        return False, "Please wait before requesting another login code."

    return True, None


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request):
    """Validate credentials and check whether the device is trusted."""
    email = str(request.data.get('email') or '').strip().lower()
    password = request.data.get('password')
    device_token = str(request.data.get('device_token') or '').strip()

    if not email or not password:
        return Response(
            {'error': 'Email and password are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    household = Household.objects.filter(email__iexact=email).first()

    if household is None or not check_password(password, household.password):
        return Response(
            {'error': 'Invalid email or password'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not household.email_verified:
        return Response(
            {
                'error': 'Please verify your email before logging in.',
                'email_verification_required': True,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    # Skip OTP only when the supplied device token is valid.
    trusted_device = get_valid_trusted_device(household, device_token)

    if trusted_device:
        tokens = get_tokens_for_user(household)

        household.last_login = timezone.now()
        household.save(update_fields=['last_login'])

        return Response(
            {
                'message': 'Login successful on trusted device.',
                'otp_required': False,
                'user': {
                    'household_id': household.household_id,
                    'email': household.email,
                    'first_name': household.first_name,
                    'last_name': household.last_name,
                },
                'tokens': tokens,
            },
            status=status.HTTP_200_OK,
        )

    # Unknown, expired, or revoked device: continue with email OTP.
    now = timezone.now()
    otp = f'{secrets.randbelow(1_000_000):06d}'

    with transaction.atomic():
        # Serialize login OTP requests for this household.
        locked_household = Household.objects.select_for_update().get(
            pk=household.pk
        )
        # Check the cooldown and hourly limit while holding the lock.
        allowed, error_message = can_request_login_otp(locked_household)
        if not allowed:
            return Response(
                {
                    'error': error_message,
                    'resend_after': 60,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Invalidate previous unconsumed login challenges by expiring them,
        # rather than deleting them. This preserves the hourly request
        # history used by can_request_login_otp().
        EmailOTPVerification.objects.filter(
            household=locked_household,
            purpose='login',
            consumed_at__isnull=True,
            expires_at__gt=now,
        ).update(expires_at=now)


        challenge = EmailOTPVerification.objects.create(
            household=locked_household,
            purpose='login',
            otp_hash=make_password(otp),
            expires_at=now + timedelta(minutes=5),
            last_sent_at=now,
        )

    try:
        sent = send_mail(
            subject='Your BillWise login verification code',
            message=(
                f'Hi {household.first_name},\n\n'
                f'Your BillWise login code is: {otp}\n\n'
                'This code expires in 5 minutes. '
                'If you did not try to log in, please ignore this email.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[household.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            'Login verification email failed for household_id=%s',
            household.household_id,
        )
        sent = 0

    if sent != 1:
        challenge.expires_at = timezone.now()
        challenge.save(update_fields=['expires_at'])

        return Response(
            {'error': 'Could not send the login code. Please try again later.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    local, separator, domain = household.email.partition('@')
    masked_email = f'{local[:1]}***@{domain}' if separator else '***'

    return Response(
        {
            'message': 'Login verification code sent to your email.',
            'otp_required': True,
            'challenge_id': str(challenge.challenge_id),
            'email': masked_email,
            'expires_in': 300,
            'resend_after': 60,
        },
        status=status.HTTP_200_OK,
    )



@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def google_login(request):
    """Validate Google identity and send an OTP before issuing JWT tokens."""

    google_token = request.data.get('google_token')

    if not google_token:
        return Response(
            {'error': 'Google token is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        google_response = requests.get(
            'https://oauth2.googleapis.com/tokeninfo',
            params={'id_token': google_token},
            timeout=10,
        )
    except requests.RequestException:
        logger.exception('Google token verification request failed')
        return Response(
            {'error': 'Google authentication service is unavailable'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if google_response.status_code != 200:
        return Response(
            {'error': 'Invalid Google token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        google_data = google_response.json()
    except ValueError:
        logger.warning('Google token verification returned invalid JSON')
        return Response(
            {'error': 'Could not verify Google account'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    client_id = (
        getattr(settings, 'GOOGLE_CLIENT_ID', None)
        or os.getenv('GOOGLE_CLIENT_ID')
    )

    if not client_id or google_data.get('aud') != client_id:
        return Response(
            {'error': 'Token not valid for this application'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    email = (google_data.get('email') or '').strip().lower()
    email_verified = (
        str(google_data.get('email_verified', '')).lower() == 'true'
    )

    if not email or not email_verified:
        return Response(
            {'error': 'Google must provide a verified email address'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    first_name = (google_data.get('given_name') or '')[:100]
    last_name = (google_data.get('family_name') or '')[:100]
    otp = f'{secrets.randbelow(1_000_000):06d}'
    now = timezone.now()

    # Preserve old records and invalidate older unused challenges.
    with transaction.atomic():
        list(
            GoogleOTPVerification.objects
            .select_for_update()
            .filter(email__iexact=email, consumed_at__isnull=True)
            .values_list('challenge_id', flat=True)
        )

        GoogleOTPVerification.objects.filter(
            email__iexact=email,
            consumed_at__isnull=True,
        ).update(expires_at=now)

        challenge = GoogleOTPVerification.objects.create(
            email=email,
            first_name=first_name,
            last_name=last_name,
            otp_hash=make_password(otp),
            expires_at=now + timedelta(minutes=5),
            last_sent_at=now,
        )

    try:
        sent = send_mail(
            subject='Your BillWise verification code',
            message=(
                f'Your BillWise verification code is {otp}. '
                'It expires in 5 minutes. '
                'If you did not request this code, ignore this email.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            'Google OTP email delivery failed for challenge_id=%s',
            challenge.challenge_id,
        )
        sent = 0

    if sent != 1:
        GoogleOTPVerification.objects.filter(
            challenge_id=challenge.challenge_id,
            consumed_at__isnull=True,
        ).update(expires_at=timezone.now())

        return Response(
            {
                'error': (
                    'Could not send the verification email. '
                    'Please try again later.'
                )
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    local, separator, domain = email.partition('@')
    masked_email = (
        f'{local[:1]}***@{domain}' if separator else '***'
    )

    return Response(
        {
            'message': 'Verification code sent. Enter it to continue.',
            'otp_required': True,
            'challenge_id': str(challenge.challenge_id),
            'email': masked_email,
            'expires_in': 300,
            'resend_after': 60,
        },
        status=status.HTTP_200_OK,
    )



@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def verify_google_otp(request):
    """Verify Google OTP, create/retrieve household, and issue JWT tokens."""

    challenge_id = request.data.get('challenge_id')
    otp = str(request.data.get('otp') or '').strip()

    # 1. Validate request data
    if not challenge_id or not otp:
        return Response(
            {'error': 'challenge_id and otp are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(otp) != 6 or not otp.isdigit():
        return Response(
            {'error': 'Enter the six-digit verification code'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        challenge_uuid = uuid.UUID(str(challenge_id))
    except (ValueError, TypeError, AttributeError):
        return Response(
            {'error': 'Invalid verification challenge'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 2. Lock and validate the OTP challenge
    with transaction.atomic():
        try:
            challenge = (
                GoogleOTPVerification.objects
                .select_for_update()
                .get(challenge_id=challenge_uuid)
            )
        except GoogleOTPVerification.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired verification challenge'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()

        if challenge.consumed_at is not None:
            return Response(
                {'error': 'This code has already been used'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.expires_at <= now:
            return Response(
                {
                    'error': (
                        'Code expired. Sign in with Google again.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.attempts >= 5:
            return Response(
                {
                    'error': (
                        'Too many attempts. Sign in with Google again.'
                    )
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # 3. Verify the OTP
        if not check_password(otp, challenge.otp_hash):
            challenge.attempts += 1
            challenge.save(update_fields=['attempts'])

            remaining = max(0, 5 - challenge.attempts)

            return Response(
                {
                    'error': 'Incorrect verification code',
                    'attempts_remaining': remaining,
                },
                status=(
                    status.HTTP_429_TOO_MANY_REQUESTS
                    if remaining == 0
                    else status.HTTP_400_BAD_REQUEST
                ),
            )


        # 4. Find or create the household account.
        # The unique email constraint protects against concurrent creation.
        household = (
            Household.objects
            .select_for_update()
            .filter(email__iexact=challenge.email)
            .first()
        )

        if household is None:
            try:
                # Use a savepoint so a duplicate-email error does not
                # break the surrounding transaction.
                with transaction.atomic():
                    household = Household.objects.create(
                        first_name=challenge.first_name,
                        last_name=challenge.last_name,
                        email=challenge.email,
                        password=make_password(
                            secrets.token_urlsafe(32)
                        ),
                        email_verified=True,
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
                    message = (
                        'Account created and Google verification successful'
                    )

            except IntegrityError:
                # Another request may have created this email first.
                household = (
                    Household.objects
                    .select_for_update()
                    .filter(email__iexact=challenge.email)
                    .first()
                )

                if household is None:
                    # The integrity error was caused by something else.
                    raise

                message = 'Google sign-in verified successfully'
        else:
            message = 'Google sign-in verified successfully'

        # Mark the account as verified and record this login.
        household.email_verified = True
        household.last_login = now
        household.save(
            update_fields=['email_verified', 'last_login']
        )


        # 6. Mark the OTP as consumed
        challenge.consumed_at = now
        challenge.save(update_fields=['consumed_at'])

        # 7. Generate JWT tokens
        tokens = get_tokens_for_user(household)

    # 8. Return the response after the transaction completes
    return Response(
        {
            'message': message,
            'household_id': household.household_id,
            'first_name': household.first_name,
            'last_name': household.last_name,
            'email': household.email,
            'tokens': tokens,
        },
        status=status.HTTP_200_OK,
    )

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def resend_google_otp(request):
    """Resend a code with cooldown and resend limits."""

    challenge_id = request.data.get('challenge_id')

    if not challenge_id:
        return Response(
            {'error': 'challenge_id is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        challenge_uuid = uuid.UUID(str(challenge_id))
    except (ValueError, TypeError, AttributeError):
        return Response(
            {'error': 'Invalid verification challenge'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        try:
            challenge = (
                GoogleOTPVerification.objects
                .select_for_update()
                .get(challenge_id=challenge_uuid)
            )
        except GoogleOTPVerification.DoesNotExist:
            return Response(
                {'error': 'Invalid verification challenge'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()

        if challenge.consumed_at is not None:
            return Response(
                {'error': 'This challenge has already been used'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.expires_at <= now:
            return Response(
                {'error': 'Challenge expired. Sign in with Google again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if challenge.attempts >= 5:
            return Response(
                {'error': 'Too many attempts. Sign in with Google again.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if challenge.resend_count >= 3:
            return Response(
                {'error': 'Resend limit reached. Sign in with Google again.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        seconds_since_last_send = int(
            (now - challenge.last_sent_at).total_seconds()
        )

        if seconds_since_last_send < 60:
            return Response(
                {
                    'error': 'Please wait before requesting another code',
                    'resend_after': 60 - seconds_since_last_send,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp = f'{secrets.randbelow(1_000_000):06d}'

        try:
            sent = send_mail(
                subject='Your BillWise verification code',
                message=(
                    f'Your new BillWise verification code is {otp}. '
                    'It expires in 5 minutes. Only the latest code is valid.'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[challenge.email],
                fail_silently=False,
            )
        except Exception:
            logger.exception(
                'Google OTP resend email failed for challenge_id=%s',
                challenge.challenge_id,
            )
            sent = 0

        if sent != 1:
            return Response(
                {
                    'error': (
                        'Could not send the email. Please try again later.'
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        challenge.otp_hash = make_password(otp)
        challenge.expires_at = now + timedelta(minutes=5)
        challenge.last_sent_at = now
        challenge.resend_count += 1
        challenge.save(
            update_fields=[
                'otp_hash',
                'expires_at',
                'last_sent_at',
                'resend_count',
            ]
        )

    return Response(
        {
            'message': 'A new verification code has been sent',
            'expires_in': 300,
            'resend_after': 60,
        },
        status=status.HTTP_200_OK,
    )



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
    """Safe debug endpoint: does not leak JWT claims or exception details."""
    if not settings.DEBUG:
        return Response(
            {'error': 'Not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    auth_header = request.headers.get('Authorization', '')

    if not auth_header:
        return Response(
            {'error': 'No Authorization header found'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not auth_header.startswith('Bearer '):
        return Response(
            {'error': 'Authorization header must use the Bearer scheme'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token = auth_header.split(' ', 1)[1]

    try:
        decoded = AccessToken(token)
        return Response({
            'valid': True,
            'has_household_id': 'household_id' in decoded,
        })
    except Exception:
        logger.exception('debug_token: token validation failed')
        return Response(
            {'error': 'Invalid or expired token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )


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
            'no_of_children': household.no_of_children,
            'no_of_seniors': household.no_of_seniors,
            'housing_type': household.housing_type,
        }, status=status.HTTP_200_OK)
    except Exception:
        logger.exception("Failed to load user profile")
        return Response(
            {'error': 'Unable to load profile. Please try again later.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_trusted_devices(request):
    """List trusted devices belonging to the logged-in household."""
    household = request.user

    devices = TrustedDevice.objects.filter(
        household=household,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).order_by('-created_at')

    return Response(
        {
            'trusted_devices': [
                {
                    'device_id': str(device.device_id),
                    'device_name': device.device_name,
                    'created_at': device.created_at.isoformat(),
                    'expires_at': device.expires_at.isoformat(),
                }
                for device in devices
            ]
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def revoke_trusted_device(request, device_id):
    """Revoke a trusted device belonging to the logged-in household."""
    household = request.user

    device = TrustedDevice.objects.filter(
        device_id=device_id,
        household=household,
        revoked_at__isnull=True,
    ).first()

    if device is None:
        return Response(
            {'error': 'Trusted device not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    device.revoked_at = timezone.now()
    device.save(update_fields=['revoked_at'])

    return Response(
        {'message': 'Trusted device revoked successfully.'},
        status=status.HTTP_200_OK,
    )