from django.contrib import admin
from django.utils import timezone

from .models import Cliente, DetallePedido, PagoPendiente, Pedido, Producto, SolicitudPrivacidad

admin.site.register(Cliente)
admin.site.register(Producto)
admin.site.register(Pedido)
admin.site.register(DetallePedido)
admin.site.register(PagoPendiente)


@admin.register(SolicitudPrivacidad)
class SolicitudPrivacidadAdmin(admin.ModelAdmin):
    list_display = ('id', 'cliente', 'tipo', 'estado', 'creado_en', 'resuelto_en')
    list_filter = ('tipo', 'estado')
    search_fields = ('cliente__rut', 'cliente__nombre', 'detalle')
    actions = ('marcar_resueltas', 'anonimizar_y_resolver_solicitudes')

    @admin.action(description='Marcar solicitudes seleccionadas como resueltas')
    def marcar_resueltas(self, request, queryset):
        queryset.exclude(estado='RESUELTA').update(estado='RESUELTA', resuelto_en=timezone.now())

    @admin.action(description='Anonimizar cliente y resolver solicitudes de supresión')
    def anonimizar_y_resolver_solicitudes(self, request, queryset):
        resueltas = 0
        for solicitud in queryset.filter(tipo='SUPRESION').select_related('cliente'):
            if solicitud.cliente:
                solicitud.cliente.anonimizar()
            solicitud.estado = 'RESUELTA'
            solicitud.resuelto_en = timezone.now()
            solicitud.save(update_fields=['estado', 'resuelto_en'])
            resueltas += 1
        self.message_user(request, f'Se procesaron {resueltas} solicitudes de supresión.')
