from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tienda', '0017_pedido_fecha_entregado'),
    ]

    operations = [
        migrations.CreateModel(
            name='IncidenteTecnico',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('asunto', models.CharField(max_length=150)),
                ('descripcion', models.TextField(max_length=2000)),
                ('estado', models.CharField(choices=[('ENVIADO', 'Enviado a soporte técnico'), ('PENDIENTE', 'Pendiente de envío')], default='PENDIENTE', max_length=15)),
                ('correo_enviado_en', models.DateTimeField(blank=True, null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('reportado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='incidentes_tecnicos_reportados', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-creado_en']},
        ),
    ]
