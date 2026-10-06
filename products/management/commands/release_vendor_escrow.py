from django.core.management.base import BaseCommand

from products.money import release_matured_vendor_orders


class Command(BaseCommand):
    help = 'Release vendor earnings whose delivery holding period has elapsed.'

    def handle(self, *args, **options):
        released = release_matured_vendor_orders()
        self.stdout.write(self.style.SUCCESS(f'Released {released} vendor order(s).'))
