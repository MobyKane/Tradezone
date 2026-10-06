from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from products.models import CartItem, OrderItem, Product, ProductReport, ProductViolation


class Command(BaseCommand):
    help = 'Delete product catalog rows while preserving order history and vendor metadata.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm', action='store_true', help='Actually delete product rows. Without this flag, the command only reports what it would remove.')
        parser.add_argument('--force', action='store_true', help='Allow destructive deletion when DEBUG is False.')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError('This destructive command is disabled in production. Re-run with --force to confirm you want to continue.')

        products = list(Product.objects.select_related('vendor').prefetch_related('reports', 'violations').all())
        product_ids = [product.pk for product in products]
        cart_count = CartItem.objects.filter(product__in=product_ids).count()
        report_count = ProductReport.objects.filter(product__in=product_ids).count()
        violation_count = ProductViolation.objects.filter(product__in=product_ids).count()
        order_item_count = OrderItem.objects.filter(product__in=product_ids).count()

        if not options['confirm']:
            self.stdout.write(
                self.style.WARNING(
                    f'Dry run: would delete {len(products)} product(s), {cart_count} cart item(s), '
                    f'{report_count} report(s), and {violation_count} violation(s). '
                    f'{order_item_count} order item(s) would be preserved with product snapshots.'
                )
            )
            return

        with transaction.atomic():
            for order_item in OrderItem.objects.filter(product__in=product_ids).select_related('product').iterator():
                if order_item.product is not None:
                    order_item.product_name_snapshot = order_item.product.name
                    order_item.product_price_snapshot = order_item.product.price
                order_item.product = None
                order_item.save(update_fields=('product', 'product_name_snapshot', 'product_price_snapshot'))

            for product in products:
                if product.image:
                    product.image.delete(save=False)
                product.reports.all().delete()
                product.violations.all().delete()

            CartItem.objects.filter(product__in=product_ids).delete()
            Product.objects.filter(pk__in=product_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'Cleared {len(products)} product(s), {cart_count} cart item(s), {report_count} report(s), '
                f'and {violation_count} violation(s). {order_item_count} order item(s) were kept with product snapshots.'
            )
        )
