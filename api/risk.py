"""
Formula-Based Financial Risk Assessment
BillWise - Chapter III, Section 3.1.2.1

Steps:
  1. Remaining Budget = Combined Income - Total Bill Allocations
  2. Total Daily Need = Total Daily Expenses × Days Until Next Payday
  3. Apply risk classification rules (80% threshold)
"""

from datetime import date
from decimal import Decimal


def _to_decimal(value, default='0'):
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _parse_range(range_str):
    """
    Parse a range string like '15000-20000' into (min, max) Decimals.
    Falls back to (value, value) if there's no hyphen.
    """
    if not range_str:
        return Decimal('0'), Decimal('0')

    text = str(range_str).replace(',', '').replace(' ', '')
    if '-' in text:
        parts = text.split('-', 1)
        try:
            return _to_decimal(parts[0]), _to_decimal(parts[1])
        except Exception:
            pass
    return _to_decimal(text), _to_decimal(text)


def compute_days_until_next_payday(next_payday, today=None):
    if next_payday is None:
        return 0
    today = today or date.today()
    delta = (next_payday - today).days
    return max(delta, 0)


def assess_financial_risk(
    combined_income_min,
    combined_income_max,
    total_bill_allocations_min,
    total_bill_allocations_max,
    daily_expense_min,
    daily_expense_max,
    days_until_next_payday,
):
    """
    Implements Steps 1-3 of the risk assessment from Chapter III.

    Args:
        combined_income_min/max         — total household income range
        total_bill_allocations_min/max  — sum of all bill allocations
        daily_expense_min/max           — daily food + transport costs
        days_until_next_payday          — integer days

    Returns:
        dict with intermediate values + final classification.
    """
    inc_min = _to_decimal(combined_income_min)
    inc_max = _to_decimal(combined_income_max)
    alloc_min = _to_decimal(total_bill_allocations_min)
    alloc_max = _to_decimal(total_bill_allocations_max)
    daily_min = _to_decimal(daily_expense_min)
    daily_max = _to_decimal(daily_expense_max)

    # Step 1 — Remaining Budget (range)
    # Conservative: smaller income - larger bills (min)
    # Optimistic:   larger income - smaller bills (max)
    remaining_min = inc_min - alloc_max
    remaining_max = inc_max - alloc_min

    # Step 2 — Total Daily Need (range)
    total_daily_need_min = daily_min * days_until_next_payday
    total_daily_need_max = daily_max * days_until_next_payday

    # Step 3 — Risk rules, using the CONSERVATIVE (minimum) remaining budget
    # per Chapter III: "the system uses the minimum value of that range."
    if total_daily_need_min == 0:
        risk_level = 'LOW RISK'
        color = 'GREEN'
        label = 'STABLE'
    elif remaining_min > total_daily_need_min:
        risk_level = 'LOW RISK'
        color = 'GREEN'
        label = 'STABLE'
    elif remaining_min >= (total_daily_need_min * Decimal('0.80')):
        risk_level = 'MODERATE RISK'
        color = 'AMBER'
        label = 'AT RISK'
    else:
        risk_level = 'HIGH RISK'
        color = 'RED'
        label = 'CRITICAL'

    return {
        'combined_income': inc_max,
        'total_bill_allocations': alloc_max,
        'remaining_budget_min': remaining_min,
        'remaining_budget_max': remaining_max,
        'total_daily_expense_min': daily_min,
        'total_daily_expense_max': daily_max,
        'days_until_next_payday': days_until_next_payday,
        'total_daily_need_min': total_daily_need_min,
        'total_daily_need_max': total_daily_need_max,
        'risk_level': risk_level,
        'color_indicator': color,
        'label': label,
    }