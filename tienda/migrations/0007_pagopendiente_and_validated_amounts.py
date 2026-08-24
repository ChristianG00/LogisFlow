# Generated manually to preserve existing PostgreSQL configuration.

import uuid

from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0006_auto_20260820_1359'),
    ]

    operations = [
        migrations.AlterField(
            model_name='producto',
            name='precio',
            field=models.PositiveIntegerField(validators=[MinValueValidator(1)]),
        ),
        migrations.AlterField(
            model_name='varianteproducto',
            name='stock',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='detallepedido',
            name='cantidad',
            field=models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)]),
        ),
        migrations.AlterField(
            model_name='detallepedido',
            name='precio_unitario',
            field=models.PositiveIntegerField(validators=[MinValueValidator(1)]),
        ),
        migrations.CreateModel(
            name='PagoPendiente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('referencia', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('rut', models.CharField(max_length=12)),
                ('nombre', models.CharField(max_length=100)),
                ('telefono', models.CharField(max_length=20)),
                ('direccion', models.CharField(max_length=200)),
                ('tipo_entrega', models.CharField(max_length=50)),
                ('items', models.JSONField()),
                ('total', models.PositiveIntegerField(validators=[MinValueValidator(1)])),
                ('estado', models.CharField(choices=[('PENDIENTE', 'Pendiente de pago'), ('CONFIRMADO', 'Pago confirmado'), ('SIN_STOCK', 'No se pudo asignar stock')], default='PENDIENTE', max_length=20)),
                ('mercadopago_payment_id', models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('pedido', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pago', to='tienda.pedido')),
            ],
        ),
    ]
