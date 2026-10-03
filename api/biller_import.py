"""
Reads billers_cdo.csv (the biller directory). Pure Python so it can be tested without a database.

Columns:  name, category, city, keywords, grace_period_days, has_penalty, rules_verified
  - name and category are required. category must be one of CATEGORIES.
  - city defaults to 'Cagayan de Oro' (use 'Nationwide' for billers every household can have).
  - keywords: comma separated words that identify the biller on a scanned bill.
  - grace_period_days / has_penalty / rules_verified: leave EMPTY to keep what is already stored
    (new billers get 0 days, penalty yes, not verified). Fill them only from the biller's own rules.
"""
import csv

CATEGORIES = [
    'Electricity', 'Water', 'Rent', 'Loan', 'Internet', 'Subscription',
    'Groceries', 'Shopping', 'Entertainment', 'Insurance', 'Phone', 'Other',
]
DEFAULT_CITY = 'Cagayan de Oro'

_TRUE = {'1', 'true', 'yes', 'y'}
_FALSE = {'0', 'false', 'no', 'n'}


def _bool(value, label):
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f'{label} must be yes/no (or 1/0), got "{value}"')


def parse_rows(f):
    """Return (rows, errors). Each row is a dict of the fields to save; absent keys mean "leave as is"."""
    reader = csv.DictReader(f)
    rows, errors, seen = [], [], set()

    for line, raw in enumerate(reader, start=2):  # line 1 is the header
        r = {(k or '').strip().lower(): (v or '').strip() for k, v in raw.items()}
        if not any(r.values()):
            continue  # blank line

        name = r.get('name', '')
        category = r.get('category', '')
        if not name:
            errors.append(f'line {line}: name is required')
            continue
        if category not in CATEGORIES:
            errors.append(f'line {line} ({name}): category "{category}" must be one of {", ".join(CATEGORIES)}')
            continue
        if name.lower() in seen:
            errors.append(f'line {line} ({name}): listed twice')
            continue
        seen.add(name.lower())

        row = {
            'name': name,
            'category': category,
            'city': r.get('city') or DEFAULT_CITY,
            'keywords': ','.join(k.strip().lower() for k in r.get('keywords', '').split(',') if k.strip()),
        }
        try:
            if r.get('grace_period_days'):
                days = int(r['grace_period_days'])
                if days < 0 or days > 365:
                    raise ValueError('grace_period_days must be between 0 and 365')
                row['grace_period_days'] = days
            if r.get('has_penalty'):
                row['has_penalty'] = _bool(r['has_penalty'], 'has_penalty')
            if r.get('rules_verified'):
                row['rules_verified'] = _bool(r['rules_verified'], 'rules_verified')
        except ValueError as exc:
            errors.append(f'line {line} ({name}): {exc}')
            continue
        rows.append(row)

    return rows, errors
