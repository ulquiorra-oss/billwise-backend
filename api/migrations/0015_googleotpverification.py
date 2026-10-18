import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0014_dependent'),
    ]

    operations = [
        migrations.CreateModel(
            name='GoogleOTPVerification',
            fields=[
                (
                    'challenge_id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'email',
                    models.EmailField(
                        db_index=True,
                        max_length=254,
                    ),
                ),
                (
                    'first_name',
                    models.CharField(
                        blank=True,
                        default='',
                        max_length=100,
                    ),
                ),
                (
                    'last_name',
                    models.CharField(
                        blank=True,
                        default='',
                        max_length=100,
                    ),
                ),
                ('otp_hash', models.CharField(max_length=128)),
                ('expires_at', models.DateTimeField()),
                (
                    'attempts',
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    'resend_count',
                    models.PositiveSmallIntegerField(default=0),
                ),
                ('last_sent_at', models.DateTimeField()),
                (
                    'consumed_at',
                    models.DateTimeField(blank=True, null=True),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'google_otp_verification',
                'indexes': [
                    models.Index(
                        fields=['email', 'consumed_at'],
                        name='google_otp_email_consumed_idx',
                    ),
                ],
            },
        ),
    ]