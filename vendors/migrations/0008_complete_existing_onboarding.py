from django.db import migrations
from django.utils import timezone


def complete_existing_onboarding(apps, schema_editor):
    AccountProfile = apps.get_model('vendors', 'AccountProfile')
    Vendor = apps.get_model('vendors', 'Vendor')
    User = apps.get_model('auth', 'User')
    completed_at = timezone.now()
    AccountProfile.objects.update(onboarding_completed_at=completed_at)
    Vendor.objects.update(onboarding_complete=True)

    vendor_user_ids = set(Vendor.objects.values_list('user_id', flat=True))
    existing_user_ids = AccountProfile.objects.values_list('user_id', flat=True)
    missing_profiles = []
    for user_id in User.objects.exclude(pk__in=existing_user_ids).values_list('pk', flat=True).iterator(chunk_size=1000):
        missing_profiles.append(AccountProfile(
            user_id=user_id,
            role='seller' if user_id in vendor_user_ids else 'buyer',
            onboarding_completed_at=completed_at,
        ))
        if len(missing_profiles) == 1000:
            AccountProfile.objects.bulk_create(missing_profiles, batch_size=1000)
            missing_profiles.clear()
    if missing_profiles:
        AccountProfile.objects.bulk_create(missing_profiles, batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0007_accountprofile_onboarding_completed_at'),
    ]

    operations = [
        migrations.RunPython(
            complete_existing_onboarding,
            migrations.RunPython.noop,
        ),
    ]
