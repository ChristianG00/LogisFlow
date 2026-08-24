from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0008_logistica_y_reservas'),
    ]

    operations = [
        migrations.AlterField(
            model_name='producto',
            name='color_base',
            field=models.CharField(
                choices=[
                    ('NEGRO', 'Negro'), ('BLANCO', 'Blanco'), ('BEIGE', 'Beige / Café'),
                    ('JEANS', 'Denim / Jeans'), ('ROSADO', 'Rosado'), ('FUCSIA', 'Fucsia'),
                    ('LILA', 'Lila'), ('MORADO', 'Morado'), ('CORAL', 'Coral'),
                    ('BURDEO', 'Burdeo'), ('ROJO', 'Rojo'), ('AZUL', 'Azul'),
                    ('VERDE', 'Verde'), ('OTRO', 'Otro / Estampado'),
                ],
                default='NEGRO',
                max_length=15,
            ),
        ),
    ]
