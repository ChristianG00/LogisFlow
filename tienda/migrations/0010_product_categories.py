from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0009_feminine_colour_palette'),
    ]

    operations = [
        migrations.AlterField(
            model_name='producto',
            name='categoria',
            field=models.CharField(
                choices=[
                    ('SUPERIOR', 'Tops y poleras (general)'),
                    ('POLERAS', 'Poleras y tops'),
                    ('BLUSAS', 'Blusas'),
                    ('INFERIOR', 'Pantalones y faldas (general)'),
                    ('PANTALONES', 'Pantalones y jeans'),
                    ('FALDAS', 'Faldas'),
                    ('VESTIDOS', 'Vestidos'),
                    ('CHAQUETAS', 'Chaquetas y abrigos'),
                    ('CALZADO', 'Calzado'),
                    ('ACCESORIO', 'Accesorios'),
                ],
                default='SUPERIOR',
                max_length=15,
            ),
        ),
    ]
