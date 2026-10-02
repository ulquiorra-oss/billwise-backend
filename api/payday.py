"""
Payday schedules.

A schedule is what the user enrolls (not a one-time "next payday" date):
  Weekly         -> weekday (0 = Monday ... 6 = Sunday)
  Twice a month  -> two days of the month (e.g. 15 & 31, or 12 & 27)
  Monthly        -> one day of the month

Day 31 means "end of month" (clamped to the real last day, so it also covers 28/29/30).
If a payday lands on a Saturday or Sunday it is paid on the Friday before.
The next payday is computed from the schedule, so it never goes stale.
"""
from calendar import monthrange
from datetime import date, timedelta

WEEKLY = 'weekly'
TWICE = 'twice'
MONTHLY = 'monthly'

END_OF_MONTH = 31

CANONICAL = {WEEKLY: 'Weekly', TWICE: 'Twice a month', MONTHLY: 'Monthly'}


class ScheduleError(ValueError):
    pass


def frequency_kind(desc):
    """'Weekly' / 'Twice a month' / 'Monthly'. Old 'Bi-monthly' rows count as twice a month."""
    d = (desc or '').lower()
    if 'week' in d:
        return WEEKLY
    if 'twice' in d or 'semi' in d or 'bi' in d:
        return TWICE
    return MONTHLY


def canonical_frequency(desc):
    return CANONICAL[frequency_kind(desc)]


def _int(value, label, lo, hi):
    try:
        n = int(str(value))
    except (TypeError, ValueError):
        raise ScheduleError(f'{label} is required')
    if n < lo or n > hi:
        raise ScheduleError(f'{label} is out of range')
    return n


def parse_schedule(kind, data, who):
    """Validate the schedule fields sent by the app -> (weekday, day1, day2); unused ones are None."""
    if kind == WEEKLY:
        return _int(data.get('payday_weekday'), f'{who}: choose a payday (day of the week)', 0, 6), None, None
    if kind == MONTHLY:
        return None, _int(data.get('payday_day_1'), f'{who}: choose a payday', 1, 31), None
    d1 = _int(data.get('payday_day_1'), f'{who}: choose the first payday', 1, 31)
    d2 = _int(data.get('payday_day_2'), f'{who}: choose the second payday', 1, 31)
    if d1 == d2:
        raise ScheduleError(f'{who}: the two paydays must be different days')
    return None, min(d1, d2), max(d1, d2)


def _pay_date(year, month, day):
    d = date(year, month, min(day, monthrange(year, month)[1]))
    if d.weekday() == 5:      # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:      # Sunday -> Friday
        return d - timedelta(days=2)
    return d


def next_payday(kind, weekday, day1, day2, today=None):
    today = today or date.today()
    if kind == WEEKLY:
        if weekday is None:
            return None
        return today + timedelta(days=(weekday - today.weekday()) % 7)

    days = [d for d in (day1, day2 if kind == TWICE else None) if d]
    if not days:
        return None
    candidates = []
    for offset in (-1, 0, 1):
        month = today.month + offset
        year = today.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        candidates += [_pay_date(year, month, d) for d in days]
    return min(c for c in candidates if c >= today)


def _infer(kind, stored):
    """Schedules saved before this feature only have a date; guess the schedule from it."""
    if kind == WEEKLY:
        return stored.weekday(), None, None
    if kind == MONTHLY:
        return None, stored.day, None
    d = stored.day
    if d >= 28 or d == 15:
        return None, 15, END_OF_MONTH
    if d < 15:
        return None, d, d + 15
    return None, d - 15, d


def income_next_payday(income, today=None):
    """Next payday for an Income row (None if it has no usable schedule)."""
    kind = frequency_kind(income.income_frequency.frequency_desc if income.income_frequency else '')
    weekday, d1, d2 = income.payday_weekday, income.payday_day_1, income.payday_day_2
    has_schedule = (weekday is not None) if kind == WEEKLY else (d1 is not None)
    if not has_schedule:
        if income.next_payday is None:
            return None
        weekday, d1, d2 = _infer(kind, income.next_payday)
    return next_payday(kind, weekday, d1, d2, today)
