"""
Billers: the companies a household pays (CEPALCO, COWD, Converge ...).

Enrolling a bill means picking its biller. The biller supplies the rules the rule-based
prioritization needs (grace period, whether a late penalty applies), so the user is never
asked about penalties or grace periods. Edit the rows (Django admin or SQL) to change the rules.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Biller

# BillWise currently serves households in Cagayan de Oro.
SERVICE_CITY = 'Cagayan de Oro'

# The priority billers for Cagayan de Oro. Every other biller lives in billers_cdo.csv; load it with
#   python manage.py import_billers billers_cdo.csv
# grace_period_days / has_penalty here are placeholders until someone confirms the real rules on the
# biller's website and sets rules_verified = True (Django admin: /admin/). Nothing here is a confirmed rule.
DEFAULT_BILLERS = [
    {'name': 'CEPALCO', 'category': 'Electricity', 'city': SERVICE_CITY,
     'keywords': 'cepalco,cagayan electric power'},
    {'name': 'COWD', 'category': 'Water', 'city': SERVICE_CITY,
     'keywords': 'cowd,cagayan de oro water district,cagayan de oro city water district'},
]


def seed_billers():
    """Create the default billers that don't exist yet. Never overwrites edited rules."""
    for b in DEFAULT_BILLERS:
        Biller.objects.get_or_create(name=b['name'], defaults=b)


def serve_area_billers():
    seed_billers()
    return Biller.objects.filter(city__in=[SERVICE_CITY, 'Nationwide']).order_by('category', 'name')


def biller_json(b):
    return {
        'biller_id': b.biller_id,
        'name': b.name,
        'category': b.category,
        'city': b.city,
        'grace_period_days': b.grace_period_days,
        'has_penalty': b.has_penalty,
        'rules_verified': b.rules_verified,
    }


def detect_biller(text):
    """Find the biller named anywhere in the OCR text (matches any of its keywords)."""
    haystack = (text or '').lower()
    if not haystack:
        return None
    for b in serve_area_billers():
        for kw in (k.strip().lower() for k in b.keywords.split(',')):
            if kw and kw in haystack:
                return b
    return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_billers(request):
    return Response([biller_json(b) for b in serve_area_billers()], status=status.HTTP_200_OK)
