from django.db import migrations


# The common categories every household starts with.
# These map to the classification used by the rule engine:
#   ESSENTIAL:              Utilities, Housing, Loans, Transportation
#   IMPORTANT_NON_ESSENTIAL: Communication, Education
#   DISCRETIONARY:          Food, Personal, Other
DEFAULT_CATEGORIES = [
    'Utilities',         # Electricity, water, gas
    'Housing',           # Rent, mortgage
    'Loans',             # Bank / personal loans
    'Transportation',    # Fuel, fare
    'Communication',     # Internet, phone, cable
    'Education',         # Tuition, school fees
    'Food',              # Groceries, dining
    'Personal',          # Shopping, entertainment
    'Other',             # Fallback / catch-all
]


def seed_categories(apps, schema_editor):
    ItemCategory = apps.get_model('api', 'ItemCategory')
    for name in DEFAULT_CATEGORIES:
        ItemCategory.objects.get_or_create(category_desc=name)


def unseed_categories(apps, schema_editor):
    """Reverses the seed — only removes the defaults, leaves custom ones alone."""
    ItemCategory = apps.get_model('api', 'ItemCategory')
    ItemCategory.objects.filter(category_desc__in=DEFAULT_CATEGORIES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]