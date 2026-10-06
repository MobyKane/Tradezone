from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0005_vendor_bank_account_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='business_description',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='vendor',
            name='onboarding_step',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='vendor',
            name='onboarding_complete',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vendor',
            name='vendor_terms_accepted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='accountprofile',
            name='tour_completed',
            field=models.BooleanField(default=False),
        ),
    ]
