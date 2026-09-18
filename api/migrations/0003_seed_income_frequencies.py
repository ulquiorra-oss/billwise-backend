from django.db import migrations


DEFAULT_FREQUENCIES = [
    (1, 'Weekly'),
    (2, 'Bi-monthly'),
    (3, 'Monthly'),
]


def seed_frequencies(apps, schema_editor):
    IncomeFrequency = apps.get_model('api', 'IncomeFrequency')
    for fid, desc in DEFAULT_FREQUENCIES:
        IncomeFrequency.objects.get_or_create(
            frequency_id=fid,
            defaults={'frequency_desc': desc}
        )


def unseed_frequencies(apps, schema_editor):
    IncomeFrequency = apps.get_model('api', 'IncomeFrequency')
    IncomeFrequency.objects.filter(frequency_id__in=[f[0] for f in DEFAULT_FREQUENCIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0002_seed_categories'),
    ]

    operations = [
        migrations.RunPython(seed_frequencies, unseed_frequencies),
    ]