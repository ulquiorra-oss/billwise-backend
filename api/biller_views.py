"""
Billers: the companies a household pays (CEPALCO, COWD, Converge ...).

Enrolling a bill means picking its biller. The biller supplies the rules the rule-based
prioritization needs (grace period, whether a late penalty applies), so the user is never
asked about penalties or grace periods. Edit the rows (Django admin or SQL) to change the rules.
"""
import re
from difflib import SequenceMatcher

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


# ---------------------------------------------------------------
# BILLER DETECTION
# ---------------------------------------------------------------
#
# What was wrong with the old version:
#   - Exact substring only. One misread letter ("CEPALGO", "CAG ORO CITY WATER DISTRICT")
#     and the biller was missed, so the app fell back to the garbled merchant text.
#   - First match wins. A bill that merely lists other billers as payment channels could be
#     assigned to the wrong one.
#   - A short keyword like "cowd" matched inside longer words.
#
# What this does:
#   - Whole-word exact matches score 1.0.
#   - Keywords of 7+ letters also match with small OCR errors (fuzzy, via difflib: stdlib,
#     no new dependency), on short lines only. Short keywords such as "cowd" or "pldt" must
#     match exactly.
#   - Digits that OCR confuses with letters (0/o, 1/l, 5/s, 8/b) are tried as letters too.
#   - Every biller is scored, and the best wins. Matches near the top of the bill (where the
#     company name is printed) and repeated matches score higher.
#   - The biller's own name counts as a keyword, so billers loaded from the CSV without
#     keywords can still be detected.

FUZZY_MIN_LEN = 7        # keywords shorter than this must match exactly, as a whole word
FUZZY_THRESHOLD = 0.85   # 1.0 = identical; one wrong letter in a 7-letter name is ~0.86
HEADER_LINES = 12        # the further down a match is, the less likely it's the issuer (vs a payment channel)

_OCR_DIGITS = str.maketrans({'0': 'o', '1': 'l', '5': 's', '8': 'b'})
_NON_ALNUM = re.compile(r'[^a-z0-9]+')


def _norm(s):
    """Lowercase, punctuation -> spaces, collapse whitespace."""
    return _NON_ALNUM.sub(' ', (s or '').lower()).strip()


def _keywords_for(biller):
    """Normalised, de-duplicated keywords for a biller (its name is one of them)."""
    raw = (biller.keywords or '').split(',') + [biller.name]
    seen, out = set(), []
    for k in raw:
        n = _norm(k)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _fuzzy_in_line(keyword, line):
    """Best similarity (0..1) between the keyword and any run of words on the line."""
    tokens = line.split()
    want = len(keyword.split())
    if len(tokens) > want + 4:  # a long sentence is prose, not a company name; exact matches still work
        return 0.0
    best = 0.0
    for size in {max(1, want - 1), want, want + 1}:
        for start in range(0, len(tokens) - size + 1):
            candidate = ' '.join(tokens[start:start + size])
            if abs(len(candidate) - len(keyword)) > max(3, 0.3 * len(keyword)):
                continue
            matcher = SequenceMatcher(None, keyword, candidate)
            if matcher.real_quick_ratio() < FUZZY_THRESHOLD or matcher.quick_ratio() < FUZZY_THRESHOLD:
                continue
            best = max(best, matcher.ratio())
    return best


def _keyword_score(keyword, lines):
    """(best similarity, index of first matching line or None, number of matching lines)."""
    exact = re.compile(rf'(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])')
    best, first, count = 0.0, None, 0
    for idx, line in enumerate(lines):
        if exact.search(line):
            score = 1.0
        elif len(keyword) >= FUZZY_MIN_LEN:
            score = _fuzzy_in_line(keyword, line)
        else:
            score = 0.0
        if score >= FUZZY_THRESHOLD:
            count += 1
            best = max(best, score)
            if first is None:
                first = idx
    return best, first, count


def _match_biller(text, billers):
    """The best-matching biller for the OCR text, or None. Returns (biller, similarity)."""
    pairs = [(_norm(ln), _norm(ln.translate(_OCR_DIGITS))) for ln in (text or '').splitlines()]
    pairs = [p for p in pairs if p[0]]
    if not pairs:
        return None, 0.0
    lines_raw = [p[0] for p in pairs]
    lines_as_letters = [p[1] for p in pairs]

    winner, winner_total, winner_similarity = None, 0.0, 0.0
    for biller in billers:
        biller_best = None  # (similarity, first_line, count)
        for keyword in _keywords_for(biller):
            result = _keyword_score(keyword, lines_raw)
            if not any(c.isdigit() for c in keyword):  # only try digit->letter when the keyword has no digits
                alt = _keyword_score(keyword, lines_as_letters)
                if alt[0] > result[0]:
                    result = alt
            if result[0] >= FUZZY_THRESHOLD and (biller_best is None or result[0] > biller_best[0]):
                biller_best = result
        if biller_best is None:
            continue

        similarity, first, count = biller_best
        total = similarity
        if first is not None:
            total += 0.3 * max(0.0, 1 - first / HEADER_LINES)  # earlier line = more likely the issuer
        total += 0.05 * min(count, 3)
        if total > winner_total:
            winner, winner_total, winner_similarity = biller, total, similarity

    return winner, winner_similarity


def detect_biller(text):
    """Find the biller named in the OCR text, tolerating small OCR errors."""
    biller, _ = _match_biller(text, serve_area_billers())
    return biller


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_billers(request):
    return Response([biller_json(b) for b in serve_area_billers()], status=status.HTTP_200_OK)