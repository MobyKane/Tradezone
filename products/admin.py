from django.contrib import admin
from .models import (
    CategoryCommission,
    CategoryNode,
    CartItem,
    LedgerTransaction,
    Order,
    OrderItem,
    Payment,
    PaymentWebhookEvent,
    PlatformPaymentSettings,
    Payout,
    Product,
    ProductReport,
    ProductViolation,
    VendorDispute,
    VendorOrder,
    VendorWallet,
)

class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'vendor', 'price', 'category', 'section', 'auto_section', 'classification_overridden', 'moderation_status', 'flagged_for_review', 'is_active', 'created_at')
    list_filter = ('category', 'moderation_status', 'flagged_for_review', 'is_active', 'created_at')
    search_fields = ('name', 'description', 'vendor__business_name')
    list_editable = ('moderation_status', 'flagged_for_review', 'is_active')


@admin.register(CategoryNode)
class CategoryNodeAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'slug')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'full_name', 'email', 'total_kobo', 'payment_status', 'legacy', 'status', 'created_at')
    list_filter = ('status', 'payment_status', 'legacy', 'created_at')
    search_fields = ('full_name', 'email', 'address')
    inlines = [OrderItemInline]
    readonly_fields = ('legacy',)


@admin.register(ProductReport)
class ProductReportAdmin(admin.ModelAdmin):
    list_display = ('product', 'reporter', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('product__name', 'reporter__username', 'reason')
    list_editable = ('status',)


@admin.register(ProductViolation)
class ProductViolationAdmin(admin.ModelAdmin):
    list_display = ('vendor', 'product', 'reason', 'issued_by', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('vendor__business_name', 'product__name', 'reason')
    readonly_fields = ('created_at',)

    def save_model(self, request, obj, form, change):
        if not obj.issued_by_id:
            obj.issued_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(PlatformPaymentSettings)
class PlatformPaymentSettingsAdmin(admin.ModelAdmin):
    list_display = ('default_commission_bps', 'gateway_fee_bearer', 'holding_period_days', 'minimum_withdrawal_kobo', 'require_payout_approval')

    def has_add_permission(self, request):
        return not PlatformPaymentSettings.objects.exists()


@admin.register(CategoryCommission)
class CategoryCommissionAdmin(admin.ModelAdmin):
    list_display = ('category', 'commission_bps')
    list_editable = ('commission_bps',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('reference', 'order', 'provider', 'amount_kobo', 'currency', 'status', 'created_at', 'paid_at')
    list_filter = ('provider', 'status', 'currency', 'created_at')
    search_fields = ('reference', 'order__email', 'provider_transaction_id')
    readonly_fields = tuple(field.name for field in Payment._meta.fields)


@admin.register(VendorWallet)
class VendorWalletAdmin(admin.ModelAdmin):
    list_display = ('vendor', 'pending_balance_kobo', 'available_balance_kobo', 'total_paid_out_kobo', 'updated_at')
    search_fields = ('vendor__business_name', 'vendor__user__username')
    readonly_fields = tuple(field.name for field in VendorWallet._meta.fields)


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = ('reference', 'wallet', 'transaction_type', 'balance_bucket', 'amount_kobo', 'created_at')
    list_filter = ('transaction_type', 'balance_bucket', 'created_at')
    search_fields = ('reference', 'wallet__vendor__business_name', 'description')
    readonly_fields = tuple(field.name for field in LedgerTransaction._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ('reference', 'vendor', 'amount_kobo', 'status', 'requested_at', 'completed_at')
    list_filter = ('status', 'requested_at')
    search_fields = ('reference', 'vendor__business_name', 'vendor__user__username')
    readonly_fields = tuple(field.name for field in Payout._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VendorOrder)
class VendorOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'vendor', 'subtotal_kobo', 'status', 'delivered_at', 'release_at', 'has_open_dispute', 'released_at')
    list_filter = ('status', 'has_open_dispute', 'released_at')
    search_fields = ('order__email', 'vendor__business_name')
    actions = ('mark_delivered',)

    @admin.action(description='Mark selected vendor orders delivered; starts holding period')
    def mark_delivered(self, request, queryset):
        from .money import mark_vendor_order_delivered
        for vendor_order in queryset:
            mark_vendor_order_delivered(vendor_order)


@admin.register(VendorDispute)
class VendorDisputeAdmin(admin.ModelAdmin):
    list_display = ('vendor_order', 'opened_by', 'is_open', 'created_at', 'resolved_at')
    list_filter = ('is_open', 'created_at')
    search_fields = ('reason', 'vendor_order__vendor__business_name', 'vendor_order__order__email')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.vendor_order.has_open_dispute = obj.vendor_order.disputes.filter(is_open=True).exists()
        obj.vendor_order.save(update_fields=('has_open_dispute',))


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ('provider', 'event_key', 'received_at')
    list_filter = ('provider', 'received_at')
    search_fields = ('event_key',)
    readonly_fields = tuple(field.name for field in PaymentWebhookEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(Product, ProductAdmin)
admin.site.register(CartItem)
admin.site.register(Order, OrderAdmin)
