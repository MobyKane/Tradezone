from django.db import models
from django.contrib.auth.models import User

class Vendor(models.Model):
    AUDIENCE_CHOICES = (
        ('', 'No restriction'),
        ('men', 'Men'),
        ('women', 'Women'),
        ('both', 'Both'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='vendor_profile')
    business_name = models.CharField(max_length=255)
    business_description = models.TextField(blank=True)
    fashion_audience = models.CharField(max_length=8, choices=AUDIENCE_CHOICES, blank=True, default='')
    onboarding_step = models.PositiveSmallIntegerField(default=1)
    onboarding_complete = models.BooleanField(default=False)
    vendor_terms_accepted_at = models.DateTimeField(null=True, blank=True)
    fashion_trusted = models.BooleanField(default=False)
    fashion_trust_revoked = models.BooleanField(default=False)
    is_suspended = models.BooleanField(default=False)
    bank_account_number = models.CharField(max_length=20, blank=True, null=True)
    bank_code = models.CharField(max_length=10, blank=True, null=True)
    bank_account_name = models.CharField(max_length=255, blank=True)
    paystack_recipient_code = models.CharField(max_length=100, blank=True)
    paystack_subaccount_code = models.CharField(max_length=100, blank=True, null=True)
    commission_override_bps = models.PositiveSmallIntegerField(null=True, blank=True)
    weekly_payout_enabled = models.BooleanField(default=False)
    last_weekly_payout_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.business_name


class AccountProfile(models.Model):
    BUYER = 'buyer'
    SELLER = 'seller'
    ROLE_CHOICES = (
        (BUYER, 'Buyer'),
        (SELLER, 'Seller'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='account_profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=BUYER)
    vendor_terms_accepted_at = models.DateTimeField(null=True, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.user.username} ({self.role})'
