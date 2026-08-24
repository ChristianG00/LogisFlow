from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0010_product_categories'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='producto',
            name='sku',
        ),
    ]
