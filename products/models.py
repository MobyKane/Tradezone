from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator
from django.utils import timezone
from vendors.models import Vendor


class CategoryNode(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(fields=('parent', 'slug'), name='unique_category_node_sibling_slug'),
        ]

    def __str__(self):
        return self.name

    def is_under(self, ancestor):
        node = self
        while node is not None:
            if node.pk == ancestor.pk:
                return True
            node = node.parent
        return False


class Product(models.Model):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    MODERATION_CHOICES = (
        (PENDING, 'Pending review'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
    )

    CATEGORY_CHOICES = (
        ('Electronics', 'Electronics'),
        ('Fashion', 'Fashion'),
        ('Home & Garden', 'Home & Garden'),
        ('Sports', 'Sports'),
        ('Building Materials', 'Building Materials'),
        ('Automotive', 'Automotive'),
        ('Toys & Games', 'Toys & Games'),
    )

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    price_kobo = models.PositiveBigIntegerField(null=True, blank=True)
    discount_price_kobo = models.PositiveBigIntegerField(null=True, blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Electronics')
    section = models.ForeignKey(CategoryNode, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    auto_section = models.ForeignKey(CategoryNode, on_delete=models.SET_NULL, null=True, blank=True, related_name='auto_classified_products')
    classification_reason = models.TextField(blank=True)
    classification_overridden = models.BooleanField(default=False)
    moderation_status = models.CharField(max_length=12, choices=MODERATION_CHOICES, default=APPROVED)
    rejection_reason = models.TextField(blank=True)
    flagged_for_review = models.BooleanField(default=False)
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    stock = models.PositiveIntegerField(default=10)
    unit_of_sale = models.CharField(max_length=40, blank=True)
    quantity_per_carton = models.PositiveIntegerField(null=True, blank=True)
    dimensions = models.CharField(max_length=120, blank=True)
    material_finish = models.CharField(max_length=120, blank=True)
    country_of_origin = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from decimal import Decimal, ROUND_HALF_UP
        self.price_kobo = int((Decimal(self.price) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        self.discount_price_kobo = (
            int((Decimal(self.discount_price) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            if self.discount_price is not None else None
        )
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        section_roots = {'Fashion': 'fashion', 'Building Materials': 'building-materials'}
        root_slug = section_roots.get(self.category)
        if root_slug:
            root = CategoryNode.objects.filter(parent__isnull=True, slug=root_slug).first()
            if self.section_id is None or root is None or not self.section.is_under(root) or self.section_id == root.pk:
                raise ValidationError({'section': f'Choose a valid {self.category} section.'})
        elif self.section_id is not None:
            raise ValidationError({'section': 'A section may only be set for Fashion or Building Materials products.'})

    @property
    def current_price(self):
        return self.discount_price if self.discount_price else self.price

    @property
    def current_price_kobo(self):
        if self.discount_price_kobo:
            return self.discount_price_kobo
        if self.price_kobo is not None:
            return self.price_kobo
        from decimal import Decimal, ROUND_HALF_UP
        return int((Decimal(self.price) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class CartItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_key = models.CharField(max_length=40, null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_total_price(self):
        return self.product.current_price * self.quantity

    def get_total_price_kobo(self):
        return self.product.current_price_kobo * self.quantity

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"


class Order(models.Model):
    STATUS_CHOICES = (
        ('Pending', 'Pending'),
        ('Processing', 'Processing'),
        ('Shipped', 'Shipped'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
    )

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    address = models.TextField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_kobo = models.PositiveBigIntegerField(null=True, blank=True)
    legacy = models.BooleanField(default=False)
    shipping_kobo = models.PositiveBigIntegerField(default=0)
    payment_status = models.CharField(max_length=16, default='unpaid')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order #{self.id} - {self.full_name}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name_snapshot = models.CharField(max_length=255, blank=True, default='')
    product_price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    price_kobo = models.PositiveBigIntegerField(null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    commission_rate_bps = models.PositiveSmallIntegerField(default=1000)
    commission_kobo = models.PositiveBigIntegerField(default=0)
    gateway_fee_kobo = models.PositiveBigIntegerField(default=0)
    vendor_net_kobo = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        name = self.product_name_snapshot or (self.product.name if self.product else 'Deleted Product')
        return f"{self.quantity} x {name}"


class ProductReport(models.Model):
    OPEN = 'open'
    REVIEWED = 'reviewed'
    DISMISSED = 'dismissed'
    STATUS_CHOICES = (
        (OPEN, 'Open'),
        (REVIEWED, 'Reviewed'),
        (DISMISSED, 'Dismissed'),
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_reports')
    reason = models.TextField(max_length=1000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at',)
        constraints = [models.UniqueConstraint(fields=('product', 'reporter'), name='unique_reporter_product_report')]

    def __str__(self):
        return f'Report for {self.product} by {self.reporter}'


class ProductViolation(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='violations')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='violations')
    reason = models.TextField()
    issued_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='issued_product_violations')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'Violation for {self.vendor}: {self.reason[:60]}'


class PlatformPaymentSettings(models.Model):
    PLATFORM = 'platform'
    VENDOR = 'vendor'
    GATEWAY_FEE_BEARER_CHOICES = ((PLATFORM, 'Platform'), (VENDOR, 'Vendor'))

    default_commission_bps = models.PositiveSmallIntegerField(default=1000, validators=[MaxValueValidator(10000)])
    gateway_fee_bearer = models.CharField(max_length=10, choices=GATEWAY_FEE_BEARER_CHOICES, default=PLATFORM)
    holding_period_days = models.PositiveSmallIntegerField(default=5)
    minimum_withdrawal_kobo = models.PositiveBigIntegerField(default=500000)
    payout_approval_threshold_kobo = models.PositiveBigIntegerField(default=0)
    require_payout_approval = models.BooleanField(default=False)
    weekly_auto_payouts = models.BooleanField(default=False)

    def __str__(self):
        return 'Platform payment settings'

    @classmethod
    def get_solo(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class CategoryCommission(models.Model):
    category = models.CharField(max_length=50, choices=Product.CATEGORY_CHOICES, unique=True)
    commission_bps = models.PositiveSmallIntegerField(validators=[MaxValueValidator(10000)])

    def __str__(self):
        return f'{self.category}: {self.commission_bps / 100}%'


class Payment(models.Model):
    PAYSTACK = 'paystack'
    OPAY = 'opay'  # Legacy provider value kept for historical DB compatibility.
    PROVIDER_CHOICES = ((PAYSTACK, 'Paystack'), (OPAY, 'OPay (legacy)'))
    PENDING = 'pending'
    SUCCESS = 'success'
    FAILED = 'failed'
    STATUS_CHOICES = ((PENDING, 'Pending'), (SUCCESS, 'Successful'), (FAILED, 'Failed'))

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    reference = models.CharField(max_length=100, unique=True)
    amount_kobo = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default='NGN')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    provider_transaction_id = models.CharField(max_length=100, blank=True)
    gateway_fee_kobo = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('order',), condition=models.Q(status='success'), name='one_successful_payment_per_order')]

    def __str__(self):
        return f'{self.provider} {self.reference} ({self.status})'


class VendorOrder(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='vendor_orders')
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='vendor_orders')
    status = models.CharField(max_length=20, default='processing')
    subtotal_kobo = models.PositiveBigIntegerField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    release_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    has_open_dispute = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('order', 'vendor'), name='unique_vendor_suborder')]

    def __str__(self):
        return f'Order #{self.order_id} for {self.vendor}'


class VendorWallet(models.Model):
    vendor = models.OneToOneField(Vendor, on_delete=models.PROTECT, related_name='wallet')
    pending_balance_kobo = models.PositiveBigIntegerField(default=0)
    available_balance_kobo = models.PositiveBigIntegerField(default=0)
    total_paid_out_kobo = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Wallet for {self.vendor}'


class LedgerTransaction(models.Model):
    CREDIT = 'credit'
    RELEASE = 'release'
    PAYOUT = 'payout'
    REFUND = 'refund'
    ADJUSTMENT = 'adjustment'
    TYPE_CHOICES = ((CREDIT, 'Credit'), (RELEASE, 'Release'), (PAYOUT, 'Payout'), (REFUND, 'Refund'), (ADJUSTMENT, 'Adjustment'))

    wallet = models.ForeignKey(VendorWallet, on_delete=models.PROTECT, related_name='transactions')
    transaction_type = models.CharField(max_length=12, choices=TYPE_CHOICES)
    amount_kobo = models.PositiveBigIntegerField()
    balance_bucket = models.CharField(max_length=12, choices=(('pending', 'Pending'), ('available', 'Available')))
    reference = models.CharField(max_length=120, unique=True)
    vendor_order = models.ForeignKey(VendorOrder, on_delete=models.PROTECT, null=True, blank=True, related_name='ledger_entries')
    payout = models.ForeignKey('Payout', on_delete=models.PROTECT, null=True, blank=True, related_name='ledger_entries')
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('Ledger transactions are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Ledger transactions are immutable.')

    def __str__(self):
        return f'{self.transaction_type} {self.amount_kobo} kobo ({self.reference})'


class Payout(models.Model):
    REQUESTED = 'requested'
    AWAITING_APPROVAL = 'awaiting_approval'
    PROCESSING = 'processing'
    SUCCESS = 'success'
    FAILED = 'failed'
    REVERSED = 'reversed'
    STATUS_CHOICES = ((REQUESTED, 'Requested'), (AWAITING_APPROVAL, 'Awaiting approval'), (PROCESSING, 'Processing'), (SUCCESS, 'Successful'), (FAILED, 'Failed'), (REVERSED, 'Reversed'))

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='payouts')
    amount_kobo = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=REQUESTED)
    transfer_code = models.CharField(max_length=100, blank=True)
    failure_reason = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Payout {self.reference} for {self.vendor}'


class PaymentWebhookEvent(models.Model):
    provider = models.CharField(max_length=10, choices=Payment.PROVIDER_CHOICES)
    event_key = models.CharField(max_length=128, unique=True)
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.provider} event {self.event_key}'


class VendorDispute(models.Model):
    vendor_order = models.ForeignKey(VendorOrder, on_delete=models.PROTECT, related_name='disputes')
    opened_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.TextField()
    is_open = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def resolve(self):
        self.is_open = False
        self.resolved_at = timezone.now()
        self.save(update_fields=('is_open', 'resolved_at'))
        self.vendor_order.has_open_dispute = self.vendor_order.disputes.filter(is_open=True).exclude(pk=self.pk).exists()
        self.vendor_order.save(update_fields=('has_open_dispute',))

    def __str__(self):
        return f'Dispute on vendor order {self.vendor_order_id}'