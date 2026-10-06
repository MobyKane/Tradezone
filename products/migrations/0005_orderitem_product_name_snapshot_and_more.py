from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0004_categorycommission_paymentwebhookevent_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='product_name_snapshot',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='product_price_snapshot',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
