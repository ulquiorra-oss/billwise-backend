"""Helpers for the household's dependents (a dependent is just a relationship)."""
from .models import Dependent

VALID = ('Child', 'Parent', 'Grandparent')


def parse_dependents(data):
    """Relationships sent by the app (e.g. ['Child', 'Parent']), or None if the app sent no list."""
    items = data.get('dependents')
    if items is None:
        return None
    out = []
    for d in items:
        rel = str(d.get('relationship') if isinstance(d, dict) else d or '').strip().title()
        if rel in VALID:
            out.append(rel)
    return out


def children_and_seniors(relationships):
    """Child -> children; Parent / Grandparent -> seniors."""
    children = sum(1 for r in relationships if r == 'Child')
    return children, len(relationships) - children


def save_dependents(household, relationships):
    """Replace the household's dependents. Does nothing when relationships is None."""
    if relationships is None:
        return
    Dependent.objects.filter(household=household).delete()
    Dependent.objects.bulk_create([Dependent(household=household, relationship=r) for r in relationships])
