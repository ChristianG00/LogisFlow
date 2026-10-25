from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0019_soporte_respuestas'),
    ]

    operations = [
        migrations.AddField(
            model_name='pagopendiente',
            name='guardar_direccion_predeterminada',
            field=models.BooleanField(default=False),
        ),
    ]
