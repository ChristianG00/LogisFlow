from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0007_pagopendiente_and_validated_amounts'),
    ]

    operations = [
        migrations.AddField(
            model_name='varianteproducto',
            name='stock_reservado',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pedido',
            name='costo_despacho',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pedido',
            name='plazo_entrega',
            field=models.CharField(default='No informado', max_length=150),
        ),
        migrations.AddField(
            model_name='pedido',
            name='mecanismo_seguimiento',
            field=models.CharField(default='Consulta con tu RUT en LogisFlow.', max_length=200),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='subtotal_productos',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='costo_despacho',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='plazo_entrega',
            field=models.CharField(default='No informado', max_length=150),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='mecanismo_seguimiento',
            field=models.CharField(default='Consulta con tu RUT en LogisFlow.', max_length=200),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='reserva_activa',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='reserva_expira_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='pagopendiente',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de pago'),
                    ('CONFIRMADO', 'Pago confirmado'),
                    ('SIN_STOCK', 'No se pudo asignar stock'),
                    ('EXPIRADO', 'Reserva expirada'),
                    ('CANCELADO', 'Pago cancelado'),
                ],
                default='PENDIENTE',
                max_length=20,
            ),
        ),
    ]
