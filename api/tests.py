from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.hashers import check_password, make_password
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from .models import (
    EmailOTPVerification,
    GoogleOTPVerification,
    Household,
    TrustedDevice,
)

from .auth_views import (
    create_trusted_device,
    get_valid_trusted_device,
    hash_device_token,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="test@billwise.local",
    GOOGLE_CLIENT_ID="test-google-client-id",
)
class GoogleOTPTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.google_url = reverse("google-login")
        self.verify_url = reverse("google-verify-otp")
        self.resend_url = reverse("google-resend-otp")

    def mock_google_response(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "aud": "test-google-client-id",
            "email": "otp-test@example.com",
            "email_verified": "true",
            "given_name": "Test",
            "family_name": "User",
        }
        return response

    @patch("api.auth_views.requests.get")
    def test_google_login_sends_otp_email(self, mock_get):
        mock_get.return_value = self.mock_google_response()

        response = self.client.post(
            self.google_url,
            {"google_token": "fake-token-for-test"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])
        self.assertEqual(len(mail.outbox), 1)

        challenge = GoogleOTPVerification.objects.get(
            challenge_id=response.data["challenge_id"]
        )
        self.assertEqual(
            mail.outbox[0].subject,
            "Your BillWise verification code",
        )

        emailed_otp = mail.outbox[0].body.split(
            "verification code is "
        )[1].split(".")[0]

        self.assertEqual(len(emailed_otp), 6)
        self.assertTrue(emailed_otp.isdigit())
        self.assertTrue(check_password(emailed_otp, challenge.otp_hash))

    @patch("api.auth_views.requests.get")
    def test_google_login_rejects_missing_token(self, mock_get):
        response = self.client.post(
            self.google_url,
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Google token is required")
        mock_get.assert_not_called()

    @patch("api.auth_views.requests.get")
    def test_google_login_rejects_invalid_google_token(self, mock_get):
        google_response = Mock()
        google_response.status_code = 401
        mock_get.return_value = google_response

        response = self.client.post(
            self.google_url,
            {"google_token": "invalid-token"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"], "Invalid Google token")
        self.assertEqual(GoogleOTPVerification.objects.count(), 0)

    def create_challenge(self, otp="123456", **overrides):
        now = timezone.now()
        values = {
            "email": "otp-test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "otp_hash": make_password(otp),
            "expires_at": now + timedelta(minutes=5),
            "last_sent_at": now,
        }
        values.update(overrides)
        return GoogleOTPVerification.objects.create(**values)

    def test_verify_rejects_missing_challenge(self):
        response = self.client.post(
            self.verify_url,
            {"challenge_id": "", "otp": "123456"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_verify_rejects_malformed_otp(self):
        response = self.client.post(
            self.verify_url,
            {"challenge_id": "not-a-uuid", "otp": "12"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_verify_rejects_expired_code(self):
        challenge = self.create_challenge(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.client.post(
            self.verify_url,
            {"challenge_id": str(challenge.challenge_id), "otp": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.data["error"].lower())

    def test_verify_rejects_wrong_code_and_counts_attempt(self):
        challenge = self.create_challenge()

        response = self.client.post(
            self.verify_url,
            {"challenge_id": str(challenge.challenge_id), "otp": "654321"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)

    def test_verify_blocks_after_five_wrong_attempts(self):
        challenge = self.create_challenge(attempts=4)

        response = self.client.post(
            self.verify_url,
            {"challenge_id": str(challenge.challenge_id), "otp": "654321"},
            format="json",
        )

        self.assertEqual(response.status_code, 429)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 5)

    def test_resend_obeys_sixty_second_cooldown(self):
        challenge = self.create_challenge()

        response = self.client.post(
            self.resend_url,
            {"challenge_id": str(challenge.challenge_id)},
            format="json",
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("wait", response.data["error"].lower())
        self.assertEqual(len(mail.outbox), 0)

    def test_verify_success_creates_account_and_tokens(self):
        challenge = self.create_challenge()

        response = self.client.post(
            self.verify_url,
            {"challenge_id": str(challenge.challenge_id), "otp": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("tokens", response.data)
        self.assertTrue(
            Household.objects.filter(email=challenge.email).exists()
        )

        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.consumed_at)

    def test_consumed_code_cannot_be_reused(self):
        challenge = self.create_challenge(consumed_at=timezone.now())

        response = self.client.post(
            self.verify_url,
            {"challenge_id": str(challenge.challenge_id), "otp": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already been used", response.data["error"])


class TrustedDeviceTests(TestCase):
    def setUp(self):
        # Initialize the API client for integration tests
        self.client = APIClient()

        self.household = Household.objects.create(
            email="trusted-test@example.com",
            first_name="Test",
            last_name="User",
            password=make_password("TestPassword123!"),
            email_verified=True,
        )

        self.other_household = Household.objects.create(
            email="other-test@example.com",
            first_name="Other",
            last_name="User",
            password=make_password("TestPassword123!"),
            email_verified=True,
        )

    def test_created_device_has_valid_token(self):
        device, raw_token = create_trusted_device(
            self.household,
            device_name="Test browser",
        )

        self.assertNotEqual(device.device_token_hash, raw_token)
        self.assertEqual(
            device.device_token_hash,
            hash_device_token(raw_token),
        )
        self.assertIsNotNone(
            get_valid_trusted_device(self.household, raw_token)
        )

    def test_invalid_token_is_rejected(self):
        create_trusted_device(self.household)

        self.assertIsNone(
            get_valid_trusted_device(self.household, "invalid-token")
        )

    def test_expired_device_is_rejected(self):
        device, raw_token = create_trusted_device(self.household)
        device.expires_at = timezone.now() - timedelta(seconds=1)
        device.save(update_fields=["expires_at"])

        self.assertIsNone(
            get_valid_trusted_device(self.household, raw_token)
        )

    def test_revoked_device_is_rejected(self):
        device, raw_token = create_trusted_device(self.household)
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at"])

        self.assertIsNone(
            get_valid_trusted_device(self.household, raw_token)
        )

    def test_device_cannot_be_used_by_another_household(self):
        _, raw_token = create_trusted_device(self.household)

        self.assertIsNone(
            get_valid_trusted_device(self.other_household, raw_token)
        )

    def test_list_trusted_devices_requires_authentication(self):
        response = self.client.get(
            reverse("list-trusted-devices")
        )

        self.assertEqual(response.status_code, 401)

    def test_list_trusted_devices_shows_only_own_active_devices(self):
        own_device, _ = create_trusted_device(
            self.household,
            device_name="My browser",
        )
        create_trusted_device(
            self.other_household,
            device_name="Other browser",
        )

        self.client.force_authenticate(user=self.household)
        response = self.client.get(
            reverse("list-trusted-devices")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["trusted_devices"]), 1)
        self.assertEqual(
            response.data["trusted_devices"][0]["device_id"],
            str(own_device.device_id),
        )

    def test_revoke_trusted_device(self):
        device, _ = create_trusted_device(self.household)

        self.client.force_authenticate(user=self.household)
        response = self.client.post(
            reverse(
                "revoke-trusted-device",
                kwargs={"device_id": device.device_id},
            )
        )

        self.assertEqual(response.status_code, 200)

        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)

    def test_cannot_revoke_another_households_device(self):
        device, _ = create_trusted_device(self.other_household)

        self.client.force_authenticate(user=self.household)
        response = self.client.post(
            reverse(
                "revoke-trusted-device",
                kwargs={"device_id": device.device_id},
            )
        )

        self.assertEqual(response.status_code, 404)

        device.refresh_from_db()
        self.assertIsNone(device.revoked_at)

@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="test@billwise.local",
)
class TrustedDeviceLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.household = Household.objects.create(
            email="login-test@example.com",
            first_name="Test",
            last_name="User",
            password=make_password("TestPassword123!"),
            email_verified=True,
        )

    def login(self, device_token=""):
        return self.client.post(
            reverse("login"),
            {
                "email": "login-test@example.com",
                "password": "TestPassword123!",
                "device_token": device_token,
            },
            format="json",
        )

    def test_unknown_device_requires_email_otp(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])
        self.assertEqual(
            EmailOTPVerification.objects.filter(
                household=self.household,
                purpose="login",
                consumed_at__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_trusted_device_can_login_without_otp(self):
        _, raw_token = create_trusted_device(
            self.household,
            device_name="Test browser",
        )

        response = self.login(device_token=raw_token)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["otp_required"])
        self.assertIn("access", response.data["tokens"])
        self.assertIn("refresh", response.data["tokens"])
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            EmailOTPVerification.objects.filter(
                household=self.household,
                purpose="login",
            ).count(),
            0,
        )

    def test_invalid_device_token_requires_otp(self):
        response = self.login(device_token="invalid-device-token")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])

    def test_expired_device_requires_otp(self):
        device, raw_token = create_trusted_device(self.household)
        device.expires_at = timezone.now() - timedelta(seconds=1)
        device.save(update_fields=["expires_at"])

        response = self.login(device_token=raw_token)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])

    def test_revoked_device_requires_otp(self):
        device, raw_token = create_trusted_device(self.household)
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at"])

        response = self.login(device_token=raw_token)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])

    def test_login_email_is_case_insensitive(self):
        response = self.client.post(
            reverse("login"),
            {
                "email": "LOGIN-TEST@EXAMPLE.COM",
                "password": "TestPassword123!",
                "device_token": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["otp_required"])


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="test@billwise.local",
)
class VerifyLoginOTPTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.household = Household.objects.create(
            email="otp-test@example.com",
            first_name="Test",
            last_name="User",
            password=make_password("TestPassword123!"),
            email_verified=True,
        )

    def create_login_challenge(self, otp="123456", expires_in=300):
        return EmailOTPVerification.objects.create(
            household=self.household,
            purpose="login",
            otp_hash=make_password(otp),
            expires_at=timezone.now() + timedelta(seconds=expires_in),
            last_sent_at=timezone.now(),
        )

    def verify(self, challenge, otp="123456", trust_device=False):
        return self.client.post(
            reverse("login-verify-otp"),
            {
                "challenge_id": str(challenge.challenge_id),
                "otp": otp,
                "trust_device": trust_device,
                "device_name": "Test browser",
            },
            format="json",
        )

    def test_successful_otp_verification(self):
        challenge = self.create_login_challenge()

        response = self.verify(challenge)

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data["tokens"])
        self.assertIn("refresh", response.data["tokens"])

        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.consumed_at)

    def test_incorrect_otp_is_rejected(self):
        challenge = self.create_login_challenge()

        response = self.verify(challenge, otp="654321")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["attempts_remaining"], 4)

        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)

    def test_fifth_incorrect_attempt_is_rate_limited(self):
        challenge = self.create_login_challenge()

        for _ in range(4):
            response = self.verify(challenge, otp="654321")
            self.assertEqual(response.status_code, 400)

        response = self.verify(challenge, otp="654321")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["attempts_remaining"], 0)

    def test_expired_otp_is_rejected(self):
        challenge = self.create_login_challenge(expires_in=-1)

        response = self.verify(challenge)

        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.data["error"].lower())

    def test_consumed_otp_cannot_be_reused(self):
        challenge = self.create_login_challenge()
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])

        response = self.verify(challenge)

        self.assertEqual(response.status_code, 400)
        self.assertIn("already been used", response.data["error"])

    def test_trusting_device_returns_device_token(self):
        challenge = self.create_login_challenge()

        response = self.verify(challenge, trust_device=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("trusted_device", response.data)
        self.assertTrue(response.data["trusted_device"]["device_token"])

        device = TrustedDevice.objects.get(
            household=self.household,
            device_id=response.data["trusted_device"]["device_id"],
        )
        self.assertNotEqual(
            device.device_token_hash,
            response.data["trusted_device"]["device_token"],
        )

@override_settings(
    REST_FRAMEWORK={
        "DEFAULT_AUTHENTICATION_CLASSES": (
            "api.authentication.HouseholdJWTAuthentication",
        ),
        "DEFAULT_PERMISSION_CLASSES": (
            "rest_framework.permissions.IsAuthenticated",
        ),
    },
)
class RefreshTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.household = Household.objects.create(
            email="refresh-test@example.com",
            first_name="Refresh",
            last_name="Test",
            password=make_password("TestPassword123!"),
            email_verified=True,
        )
        self.url = reverse("token-refresh")

    def create_refresh_token(self):
        token = RefreshToken()
        token["household_id"] = self.household.household_id
        return str(token)

    def test_refresh_returns_new_tokens_and_blacklists_old_token(self):
        from api.models import RevokedRefreshToken

        old_token = self.create_refresh_token()

        response = self.client.post(
            self.url,
            {"refresh": old_token},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertNotEqual(response.data["refresh"], old_token)

        old = RefreshToken(old_token)

        self.assertTrue(
            RevokedRefreshToken.objects.filter(
                jti=old["jti"]
            ).exists()
        )

    def test_refresh_rejects_reuse_of_old_token(self):
        old_token = self.create_refresh_token()

        first_response = self.client.post(
            self.url,
            {"refresh": old_token},
            format="json",
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            self.url,
            {"refresh": old_token},
            format="json",
        )
        self.assertEqual(second_response.status_code, 401)

    def test_refresh_rejects_missing_token(self):
        response = self.client.post(
            self.url,
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_refresh_rejects_unverified_household(self):
        self.household.email_verified = False
        self.household.save(update_fields=["email_verified"])
        token = self.create_refresh_token()

        response = self.client.post(
            self.url,
            {"refresh": token},
            format="json",
        )
        self.assertEqual(response.status_code, 403)