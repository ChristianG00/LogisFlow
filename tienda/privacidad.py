# Reglas de conservación y anonimización

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Cliente, PagoPendiente


# Las compras confirmadas se conservan seis años; los pagos sin compra, 30 días
RETENCION_PEDIDOS_DIAS = 6 * 365
RETENCION_PAGOS_NO_CONFIRMADOS_DIAS = 30
ESTADOS_PAGO_SIN_COMPRA = ('EXPIRADO', 'CANCELADO', 'SIN_STOCK')


def anonimizar_datos_vencidos(ahora=None, aplicar=True):
    # Omite las cuentas activas y conserva solo el historial necesario
    ahora = ahora or timezone.now()
    limite_pagos = ahora - timedelta(days=RETENCION_PAGOS_NO_CONFIRMADOS_DIAS)
    limite_pedidos = ahora - timedelta(days=RETENCION_PEDIDOS_DIAS)

    pagos_sin_compra = PagoPendiente.objects.filter(
        anonimizado_en__isnull=True,
        creado_en__lt=limite_pagos,
        estado__in=ESTADOS_PAGO_SIN_COMPRA,
    )
    pagos_confirmados = PagoPendiente.objects.filter(
        anonimizado_en__isnull=True,
        creado_en__lt=limite_pedidos,
        estado='CONFIRMADO',
    )
    clientes_vencidos = (
        Cliente.objects.filter(
            usuario__isnull=True,
            anonimizado_en__isnull=True,
            pedido__fecha__lt=limite_pedidos,
        )
        .exclude(pedido__fecha__gte=limite_pedidos)
        .distinct()
    )

    resultado = {
        'pagos_sin_compra': pagos_sin_compra.count(),
        'pagos_confirmados': pagos_confirmados.count(),
        'clientes': clientes_vencidos.count(),
    }
    if not aplicar:
        return resultado

    # Se bloquean los registros por ID antes de modificarlos
    clientes_ids = list(clientes_vencidos.values_list('id', flat=True))
    with transaction.atomic():
        for pago in pagos_sin_compra.select_for_update().iterator():
            pago.anonimizar()
        for pago in pagos_confirmados.select_for_update().iterator():
            pago.anonimizar()
        for cliente in Cliente.objects.select_for_update().filter(id__in=clientes_ids).iterator():
            cliente.anonimizar()
    return resultado
