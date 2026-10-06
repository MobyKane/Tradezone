from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    CategoryCommission,
    LedgerTransaction,
    Order,
    OrderItem,
    Payment,
    Payout,
    PlatformPaymentSettings,
    VendorOrder,
    VendorWallet,
)


def naira_to_kobo(value):
    return int((Decimal(value) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def kobo_to_naira_decimal(value):
    return (Decimal(value) / Decimal(100)).quantize(Decimal('0.01'))


def commission_rate_bps(vendor, category, settings=None):
    if vendor.commission_override_bps is not None:
        return vendor.commission_override_bps
    override = CategoryCommission.objects.filter(category=category).first()
    if override:
        return override.commission_bps
    settings = settings or PlatformPaymentSettings.get_solo()
    return settings.default_commission_bps


def _round_basis_points(amount_kobo, rate_bps):
    return (amount_kobo * rate_bps + 5000) // 10000


def snapshot_order_item(item):
    gross_kobo = item.product.current_price_kobo * item.quantity
    rate = commission_rate_bps(item.vendor, item.product.category)
    commission = _round_basis_points(gross_kobo, rate)
    item.commission_rate_bps = rate
    item.commission_kobo = commission
    item.vendor_net_kobo = gross_kobo - commission
    item.save(update_fields=('commission_rate_bps', 'commission_kobo', 'vendor_net_kobo'))
    return gross_kobo


def create_vendor_orders(order):
    grouped = {}
    for item in order.items.select_related('product', 'vendor'):
        grouped[item.vendor_id] = grouped.get(item.vendor_id, 0) + (item.price_kobo * item.quantity)
    return [
        VendorOrder.objects.create(order=order, vendor_id=vendor_id, subtotal_kobo=subtotal)
        for vendor_id, subtotal in grouped.items()
    ]


def _allocate_gateway_fee(order, gateway_fee_kobo, vendor_bears_fee):
    items = list(order.items.select_related('product', 'vendor'))
    if not items:
        return
    gross_total = sum(item.price_kobo * item.quantity for item in items)
    remaining = gateway_fee_kobo
    for index, item in enumerate(items):
        gross = item.price_kobo * item.quantity
        fee = remaining if index == len(items) - 1 else (gateway_fee_kobo * gross // gross_total)
        remaining -= fee
        net_before_fee = gross - item.commission_kobo
        item.gateway_fee_kobo = fee
        item.vendor_net_kobo = net_before_fee - (fee if vendor_bears_fee else 0)
        if item.vendor_net_kobo < 0:
            raise ValueError('Gateway fee exceeds the vendor earnings for an order item.')
        item.save(update_fields=('gateway_fee_kobo', 'vendor_net_kobo'))


def record_successful_payment(payment, *, verified_amount_kobo, currency, gateway_fee_kobo=0, provider_transaction_id=''):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related('order').get(pk=payment.pk)
        if payment.status == Payment.SUCCESS:
            return False
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if order.legacy:
            raise ValueError('Legacy demo orders cannot create vendor ledger balances.')
        if currency != 'NGN' or verified_amount_kobo != payment.amount_kobo or payment.amount_kobo != order.total_kobo:
            raise ValueError('Verified payment currency or amount does not match the order.')
        if order.payment_status == 'paid':
            payment.status = Payment.SUCCESS
            payment.paid_at = timezone.now()
            payment.provider_transaction_id = provider_transaction_id
            payment.gateway_fee_kobo = gateway_fee_kobo
            payment.save(update_fields=('status', 'paid_at', 'provider_transaction_id', 'gateway_fee_kobo'))
            return False

        settings = PlatformPaymentSettings.get_solo()
        _allocate_gateway_fee(
            order,
            gateway_fee_kobo,
            settings.gateway_fee_bearer == PlatformPaymentSettings.VENDOR,
        )
        for vendor_order in order.vendor_orders.select_for_update().all():
            earning = sum(
                item.vendor_net_kobo
                for item in order.items.filter(vendor_id=vendor_order.vendor_id)
            )
            wallet, _ = VendorWallet.objects.get_or_create(vendor_id=vendor_order.vendor_id)
            wallet = VendorWallet.objects.select_for_update().get(pk=wallet.pk)
            wallet.pending_balance_kobo = F('pending_balance_kobo') + earning
            wallet.save(update_fields=('pending_balance_kobo', 'updated_at'))
            LedgerTransaction.objects.create(
                wallet=wallet,
                transaction_type=LedgerTransaction.CREDIT,
                amount_kobo=earning,
                balance_bucket='pending',
                reference=f'order:{order.pk}:vendor:{vendor_order.vendor_id}:credit',
                vendor_order=vendor_order,
                description=f'Escrow earnings for order {order.pk}',
            )
        payment.status = Payment.SUCCESS
        payment.paid_at = timezone.now()
        payment.provider_transaction_id = provider_transaction_id
        payment.gateway_fee_kobo = gateway_fee_kobo
        payment.save(update_fields=('status', 'paid_at', 'provider_transaction_id', 'gateway_fee_kobo'))
        order.payment_status = 'paid'
        order.status = 'Processing'
        order.save(update_fields=('payment_status', 'status'))
        return True


def mark_vendor_order_delivered(vendor_order):
    settings = PlatformPaymentSettings.get_solo()
    delivered_at = timezone.now()
    vendor_order.status = 'delivered'
    vendor_order.delivered_at = delivered_at
    vendor_order.release_at = delivered_at + timezone.timedelta(days=settings.holding_period_days)
    vendor_order.save(update_fields=('status', 'delivered_at', 'release_at'))
    return vendor_order


def release_matured_vendor_orders(now=None):
    now = now or timezone.now()
    released = 0
    ids = list(VendorOrder.objects.filter(
        released_at__isnull=True,
        release_at__lte=now,
        has_open_dispute=False,
        order__payment_status='paid',
        order__legacy=False,
    ).values_list('pk', flat=True))
    for vendor_order_id in ids:
        with transaction.atomic():
            vendor_order = VendorOrder.objects.select_for_update().select_related('vendor').get(pk=vendor_order_id)
            if vendor_order.released_at or vendor_order.has_open_dispute or vendor_order.release_at > now:
                continue
            earning = sum(
                item.vendor_net_kobo
                for item in vendor_order.order.items.filter(vendor_id=vendor_order.vendor_id)
            )
            wallet = VendorWallet.objects.select_for_update().get(vendor=vendor_order.vendor)
            if wallet.pending_balance_kobo < earning:
                raise ValueError('Vendor pending balance is lower than the releasable order earnings.')
            wallet.pending_balance_kobo = F('pending_balance_kobo') - earning
            wallet.available_balance_kobo = F('available_balance_kobo') + earning
            wallet.save(update_fields=('pending_balance_kobo', 'available_balance_kobo', 'updated_at'))
            LedgerTransaction.objects.create(
                wallet=wallet,
                transaction_type=LedgerTransaction.RELEASE,
                amount_kobo=earning,
                balance_bucket='available',
                reference=f'vendororder:{vendor_order.pk}:release',
                vendor_order=vendor_order,
                description=f'Escrow released for vendor order {vendor_order.pk}',
            )
            vendor_order.released_at = now
            vendor_order.save(update_fields=('released_at',))
            released += 1
    return released


def request_payout(vendor, amount_kobo):
    settings = PlatformPaymentSettings.get_solo()
    if amount_kobo < settings.minimum_withdrawal_kobo:
        raise ValueError('Withdrawal amount is below the configured minimum.')
    if not vendor.paystack_recipient_code:
        raise ValueError('Set up and confirm a Paystack bank recipient before requesting a payout.')
    with transaction.atomic():
        wallet, _ = VendorWallet.objects.get_or_create(vendor=vendor)
        wallet = VendorWallet.objects.select_for_update().get(pk=wallet.pk)
        if wallet.available_balance_kobo < amount_kobo:
            raise ValueError('Insufficient available balance.')
        status = Payout.AWAITING_APPROVAL if (
            settings.require_payout_approval and amount_kobo >= settings.payout_approval_threshold_kobo
        ) else Payout.PROCESSING
        payout = Payout.objects.create(
            vendor=vendor,
            amount_kobo=amount_kobo,
            reference=f'tzpay-{uuid4().hex}',
            status=status,
        )
        wallet.available_balance_kobo = F('available_balance_kobo') - amount_kobo
        wallet.save(update_fields=('available_balance_kobo', 'updated_at'))
        LedgerTransaction.objects.create(
            wallet=wallet,
            transaction_type=LedgerTransaction.PAYOUT,
            amount_kobo=amount_kobo,
            balance_bucket='available',
            reference=f'payout:{payout.reference}:reserve',
            payout=payout,
            description='Funds reserved for vendor payout',
        )
        return payout


def fail_payout(payout, reason, *, reversed_transfer=False):
    with transaction.atomic():
        payout = Payout.objects.select_for_update().select_related('vendor').get(pk=payout.pk)
        target_status = Payout.REVERSED if reversed_transfer else Payout.FAILED
        if payout.status == target_status or payout.status in {Payout.FAILED, Payout.REVERSED}:
            return False
        wallet = VendorWallet.objects.select_for_update().get(vendor=payout.vendor)
        wallet.available_balance_kobo = F('available_balance_kobo') + payout.amount_kobo
        if payout.status == Payout.SUCCESS:
            wallet.total_paid_out_kobo = F('total_paid_out_kobo') - payout.amount_kobo
        wallet.save(update_fields=('available_balance_kobo', 'total_paid_out_kobo', 'updated_at'))
        LedgerTransaction.objects.create(
            wallet=wallet,
            transaction_type=LedgerTransaction.REFUND,
            amount_kobo=payout.amount_kobo,
            balance_bucket='available',
            reference=f'payout:{payout.reference}:{target_status}:restore',
            payout=payout,
            description=f'Payout funds restored: {reason[:180]}',
        )
        payout.status = target_status
        payout.failure_reason = reason
        payout.completed_at = timezone.now()
        payout.save(update_fields=('status', 'failure_reason', 'completed_at'))
        return True


def complete_payout(payout):
    with transaction.atomic():
        payout = Payout.objects.select_for_update().select_related('vendor').get(pk=payout.pk)
        if payout.status == Payout.SUCCESS:
            return False
        if payout.status not in {Payout.PROCESSING, Payout.AWAITING_APPROVAL}:
            return False
        wallet = VendorWallet.objects.select_for_update().get(vendor=payout.vendor)
        wallet.total_paid_out_kobo = F('total_paid_out_kobo') + payout.amount_kobo
        wallet.save(update_fields=('total_paid_out_kobo', 'updated_at'))
        payout.status = Payout.SUCCESS
        payout.completed_at = timezone.now()
        payout.save(update_fields=('status', 'completed_at'))
        return True


def reverse_vendor_earnings(vendor_order, amount_kobo, reason='Refund'):
    with transaction.atomic():
        vendor_order = VendorOrder.objects.select_for_update().select_related('vendor').get(pk=vendor_order.pk)
        wallet = VendorWallet.objects.select_for_update().get(vendor=vendor_order.vendor)
        if amount_kobo <= 0 or amount_kobo > vendor_order.subtotal_kobo:
            raise ValueError('Refund amount must be positive and no greater than the vendor order subtotal.')
        remaining = amount_kobo
        for bucket in ('available', 'pending'):
            field = f'{bucket}_balance_kobo'
            balance = getattr(wallet, field)
            deduction = min(balance, remaining)
            if deduction:
                setattr(wallet, field, F(field) - deduction)
                LedgerTransaction.objects.create(
                    wallet=wallet,
                    transaction_type=LedgerTransaction.REFUND,
                    amount_kobo=deduction,
                    balance_bucket=bucket,
                    reference=f'vendororder:{vendor_order.pk}:refund:{uuid4().hex}',
                    vendor_order=vendor_order,
                    description=reason[:255],
                )
                remaining -= deduction
        if remaining:
            raise ValueError('Refund exceeds this vendor wallet balance; admin adjustment is required.')
        wallet.save(update_fields=('pending_balance_kobo', 'available_balance_kobo', 'updated_at'))
        return amount_kobo