from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tienda', '0013_alter_pedido_codigo_seguimiento'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='anonimizado_en',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='usuario',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='perfil_cliente',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='pagopendiente',
            name='anonimizado_en',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.CreateModel(
            name='SolicitudPrivacidad',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('ACCESO', 'Acceso a mis datos'), ('RECTIFICACION', 'Rectificación de datos'), ('SUPRESION', 'Supresión o anonimización'), ('OPOSICION', 'Oposición al tratamiento'), ('PORTABILIDAD', 'Portabilidad de datos'), ('BLOQUEO', 'Bloqueo temporal del tratamiento')], max_length=20)),
                ('detalle', models.TextField(blank=True, max_length=1000)),
                ('estado', models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('EN_REVISION', 'En revisión'), ('RESUELTA', 'Resuelta'), ('RECHAZADA', 'Rechazada')], default='PENDIENTE', max_length=20)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('resuelto_en', models.DateTimeField(blank=True, null=True)),
                ('cliente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='solicitudes_privacidad', to='tienda.cliente')),
            ],
            options={'ordering': ['-creado_en']},
        ),
    ]
