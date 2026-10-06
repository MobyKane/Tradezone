from django.db import migrations, models
from django.utils import timezone


def preserve_completed_tours(apps, schema_editor):
    AccountProfile = apps.get_model('vendors', 'AccountProfile')
    AccountProfile.objects.filter(tour_completed=True).update(onboarding_completed_at=timezone.now())


def restore_completed_tours(apps, schema_editor):
    AccountProfile = apps.get_model('vendors', 'AccountProfile')
    AccountProfile.objects.filter(onboarding_completed_at__isnull=False).update(tour_completed=True)


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0006_vendor_onboarding_and_profile_tour'),
    ]

    operations = [
        migrations.AddField(
            model_name='accountprofile',
            name='onboarding_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(preserve_completed_tours, restore_completed_tours),
        migrations.RemoveField(
            model_name='accountprofile',
            name='tour_completed',
        ),
    ]
