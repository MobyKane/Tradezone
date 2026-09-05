from django.db import models
from django.contrib.auth.models import User

class Vendor(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=255)
    bank_account_number = models.CharField(max_length=20)
    bank_code = models.CharField(max_length=10)
    paystack_subaccount_code = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.business_name