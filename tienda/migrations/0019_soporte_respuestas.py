import secrets

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import tienda.models


def asignar_codigos_consulta(apps, schema_editor):
    SolicitudPrivacidad = apps.get_model('tienda', 'SolicitudPrivacidad')
    for solicitud in SolicitudPrivacidad.objects.filter(codigo_consulta__isnull=True).iterator():
        codigo = f'SUP-{secrets.token_hex(5).upper()}'
        while SolicitudPrivacidad.objects.filter(codigo_consulta=codigo).exists():
            codigo = f'SUP-{secrets.token_hex(5).upper()}'
        solicitud.codigo_consulta = codigo
        solicitud.save(update_fields=['codigo_consulta'])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tienda', '0018_incidentetecnico'),
    ]

    operations = [
        migrations.AddField(
            model_name='solicitudprivacidad',
            name='codigo_consulta',
            field=models.CharField(blank=True, max_length=14, null=True),
        ),
        migrations.RunPython(asignar_codigos_consulta, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='solicitudprivacidad',
            name='codigo_consulta',
            field=models.CharField(default=tienda.models.generar_codigo_soporte, editable=False, max_length=14, unique=True),
        ),
        migrations.AddField(
            model_name='solicitudprivacidad',
            name='respuesta',
            field=models.TextField(blank=True, max_length=2000),
        ),
        migrations.AddField(
            model_name='solicitudprivacidad',
            name='respondido_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='solicitudprivacidad',
            name='respondido_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='respuestas_soporte', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='solicitudprivacidad',
            name='estado',
            field=models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('EN_REVISION', 'En revisión'), ('RESPONDIDA', 'Respondida'), ('RESUELTA', 'Resuelta'), ('RECHAZADA', 'Rechazada')], default='PENDIENTE', max_length=20),
        ),
        migrations.AlterField(
            model_name='solicitudprivacidad',
            name='tipo',
            field=models.CharField(choices=[('PEDIDO', 'Pedido, pago o seguimiento'), ('DESPACHO', 'Despacho o entrega'), ('CUENTA', 'Cuenta, datos o acceso'), ('OTRO', 'Otra consulta'), ('ACCESO', 'Acceso a mis datos'), ('RECTIFICACION', 'Rectificación de datos'), ('SUPRESION', 'Supresión o anonimización'), ('OPOSICION', 'Oposición al tratamiento'), ('PORTABILIDAD', 'Portabilidad de datos'), ('BLOQUEO', 'Bloqueo temporal del tratamiento')], max_length=20),
        ),
    ]
