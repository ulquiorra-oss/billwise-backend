
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework.exceptions import AuthenticationFailed

from .models import Household


class HouseholdJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        household_id = validated_token.get('household_id')

        if not household_id:
            raise InvalidToken('No household_id in token')

        try:
            return Household.objects.get(
                household_id=household_id
            )
        except Household.DoesNotExist:
            raise AuthenticationFailed('Household not found')
