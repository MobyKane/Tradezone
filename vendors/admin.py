from django.contrib import admin
from .models import Vendor

class VendorAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'user', 'fashion_audience', 'fashion_trusted', 'is_suspended')
    search_fields = ('business_name', 'user__username', 'user__email')
    list_editable = ('fashion_audience', 'fashion_trusted', 'is_suspended')

    def save_model(self, request, obj, form, change):
        if change:
            previous = Vendor.objects.get(pk=obj.pk)
            if previous.fashion_trusted and not obj.fashion_trusted:
                obj.fashion_trust_revoked = True
            elif not previous.fashion_trusted and obj.fashion_trusted:
                obj.fashion_trust_revoked = False
        super().save_model(request, obj, form, change)

admin.site.register(Vendor, VendorAdmin)
