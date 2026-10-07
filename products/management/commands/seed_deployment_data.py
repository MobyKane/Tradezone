from django.core.management.base import BaseCommand

from products.models import CategoryCommission, CategoryNode, PlatformPaymentSettings, Product


class Command(BaseCommand):
    help = 'Create any missing categories and payment defaults needed by the site.'

    def handle(self, *args, **options):
        category_tree = (
            ('Fashion', 'fashion', (
                ('Men', 'men'),
                ('Women', 'women'),
                ('Unisex', 'unisex'),
            )),
            ('Building Materials', 'building-materials', (
                ('Spanish Tiles', 'spanish-tiles'),
                ('Doors', 'doors'),
                ('Other Building Materials', 'other-building-materials'),
            )),
        )
        for root_name, root_slug, children in category_tree:
            root, _ = CategoryNode.objects.get_or_create(
                parent=None,
                slug=root_slug,
                defaults={'name': root_name},
            )
            for name, slug in children:
                CategoryNode.objects.get_or_create(
                    parent=root,
                    slug=slug,
                    defaults={'name': name},
                )

        for category, _ in Product.CATEGORY_CHOICES:
            CategoryCommission.objects.get_or_create(
                category=category,
                defaults={'commission_bps': 1000},
            )

        PlatformPaymentSettings.objects.get_or_create(
            pk=1,
            defaults={
                'default_commission_bps': 1000,
                'gateway_fee_bearer': PlatformPaymentSettings.PLATFORM,
                'holding_period_days': 5,
                'minimum_withdrawal_kobo': 500000,
            },
        )
        self.stdout.write(self.style.SUCCESS('Deployment seed data is ready.'))
