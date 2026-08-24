import secrets

from django.db import migrations, models


def generar_codigo_seguimiento():
    return f'LF-{secrets.token_hex(5).upper()}'


def asignar_codigos_de_seguimiento(apps, schema_editor):
    pedido_modelo = apps.get_model('tienda', 'Pedido')
    codigos_usados = set(
        pedido_modelo.objects.exclude(codigo_seguimiento__isnull=True).values_list(
            'codigo_seguimiento', flat=True,
        )
    )
    for pedido in pedido_modelo.objects.filter(codigo_seguimiento__isnull=True).iterator():
        codigo = generar_codigo_seguimiento()
        while codigo in codigos_usados:
            codigo = generar_codigo_seguimiento()
        pedido.codigo_seguimiento = codigo
        pedido.save(update_fields=['codigo_seguimiento'])
        codigos_usados.add(codigo)


def actualizar_mensajes_de_seguimiento(apps, schema_editor):
    for nombre_modelo in ('Pedido', 'PagoPendiente'):
        modelo = apps.get_model('tienda', nombre_modelo)
        for registro in modelo.objects.filter(mecanismo_seguimiento__contains='RUT'):
            mensaje = registro.mecanismo_seguimiento.replace(
                'RUT en LogisFlow',
                'RUT y código de seguimiento en LogisFlow',
            )
            if mensaje != registro.mecanismo_seguimiento:
                registro.mecanismo_seguimiento = mensaje
                registro.save(update_fields=['mecanismo_seguimiento'])


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0011_remove_producto_sku'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='codigo_seguimiento',
            field=models.CharField(
                editable=False,
                max_length=13,
                null=True,
            ),
        ),
        migrations.RunPython(asignar_codigos_de_seguimiento, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='pedido',
            name='codigo_seguimiento',
            field=models.CharField(
                default=generar_codigo_seguimiento,
                editable=False,
                max_length=13,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name='pedido',
            name='mecanismo_seguimiento',
            field=models.CharField(
                default='Consulta con tu RUT y código de seguimiento en LogisFlow.',
                max_length=200,
            ),
        ),
        migrations.AlterField(
            model_name='pagopendiente',
            name='mecanismo_seguimiento',
            field=models.CharField(
                default='Consulta con tu RUT y código de seguimiento en LogisFlow.',
                max_length=200,
            ),
        ),
        migrations.RunPython(actualizar_mensajes_de_seguimiento, migrations.RunPython.noop),
    ]
