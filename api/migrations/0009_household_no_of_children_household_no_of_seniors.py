from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0008_budgetallocation_rule_applied'),
    ]

    operations = [
        migrations.AddField(
            model_name='household',
            name='no_of_children',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='household',
            name='no_of_seniors',
            field=models.IntegerField(default=0),
        ),
    ]
