from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework import exceptions
from .models import Household


class HouseholdJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        try:
            household_id = validated_token.get('household_id')
            if not household_id:
                raise InvalidToken('No household_id in token')

            household = Household.objects.get(household_id=household_id)
            return household
        except Household.DoesNotExist:
            raise exceptions.AuthenticationFailed('Household not found')
        except TokenError as e:
            raise InvalidToken(e.args[0])