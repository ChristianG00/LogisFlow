import csv
import json
import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import mercadopago
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import CheckoutForm, ProductoForm, TallaForm, normalizar_rut
from .logistica import (
    OPCIONES_ENTREGA,
    ESTACIONES_METRO,
    estaciones_metro_para_checkout,
    informacion_entrega,
)
from .models import Cliente, DetallePedido, PagoPendiente, Pedido, Producto, VarianteProducto


logger = logging.getLogger(__name__)
MAX_CANTIDAD_POR_LINEA = 1000
CARRITO_DURACION_HORAS = 24
CARRITO_DURACION_MINUTOS = 60 * CARRITO_DURACION_HORAS
RESERVA_DURACION_MINUTOS = 15


class CarritoInvalido(ValueError):
    pass


class PagoNoConfirmado(ValueError):
    pass


def _cantidad_valida(valor):
    try:
        cantidad = int(str(valor))
    except (TypeError, ValueError):
        raise CarritoInvalido('La cantidad debe ser un número entero.')
    if not 1 <= cantidad <= MAX_CANTIDAD_POR_LINEA:
        raise CarritoInvalido('La cantidad debe estar entre 1 y 1000.')
    return cantidad


def _normalizar_carrito(carrito):
    """Reconstruye el carrito con datos actuales de la BD, nunca con precios del cliente."""
    if not isinstance(carrito, dict) or not carrito:
        raise CarritoInvalido('El carrito está vacío o tiene un formato inválido.')

    entradas, ids_variantes = [], []
    for clave, item in carrito.items():
        if not isinstance(item, dict):
            raise CarritoInvalido('El carrito contiene un producto inválido.')
        try:
            producto_id = int(item['producto_id'])
            variante_id = int(item['variante_id'])
        except (KeyError, TypeError, ValueError):
            raise CarritoInvalido('El carrito contiene identificadores inválidos.')
        cantidad = _cantidad_valida(item.get('cantidad'))
        if clave != f'{producto_id}_{variante_id}':
            raise CarritoInvalido('El carrito contiene una relación producto-talla inválida.')
        entradas.append((clave, producto_id, variante_id, cantidad))
        ids_variantes.append(variante_id)

    if len(ids_variantes) != len(set(ids_variantes)):
        raise CarritoInvalido('La misma talla no puede estar repetida en el carrito.')

    variantes = {
        variante.id: variante
        for variante in VarianteProducto.objects.select_related('producto').filter(id__in=ids_variantes)
    }
    if len(variantes) != len(ids_variantes):
        raise CarritoInvalido('Una talla del carrito ya no existe.')

    carrito_limpio, items, total = {}, [], 0
    for clave, producto_id, variante_id, cantidad in entradas:
        variante = variantes[variante_id]
        producto = variante.producto
        if variante.producto_id != producto_id:
            raise CarritoInvalido('La talla no corresponde al producto seleccionado.')
        if producto.precio < 1:
            raise CarritoInvalido('El producto tiene un precio inválido.')
        if variante.stock < cantidad:
            raise CarritoInvalido(f'No hay stock suficiente para {producto.nombre} en talla {variante.talla}.')

        precio = producto.precio
        carrito_limpio[clave] = {
            'producto_id': producto.id,
            'variante_id': variante.id,
            'nombre': producto.nombre,
            'talla': variante.talla,
            'precio': precio,
            'cantidad': cantidad,
            'imagen_url': producto.imagen.url if producto.imagen else '',
        }
        items.append({
            'producto_id': producto.id,
            'variante_id': variante.id,
            'cantidad': cantidad,
            'precio_unitario': precio,
            'nombre': producto.nombre,
        })
        total += precio * cantidad

    if total < 1:
        raise CarritoInvalido('El total del carrito es inválido.')
    return carrito_limpio, items, total


def _obtener_carrito_vigente(request):
    """La expiración se aplica en servidor incluso si el JavaScript no se ejecuta."""
    carrito = request.session.get('carrito', {})
    expiracion = request.session.get('carrito_expira_en')
    if not isinstance(carrito, dict):
        carrito = {}

    try:
        expiracion = int(expiracion) if expiracion is not None else None
    except (TypeError, ValueError):
        expiracion = None

    ahora = int(timezone.now().timestamp())
    if carrito and expiracion is not None and expiracion <= ahora:
        request.session.pop('carrito', None)
        request.session.pop('carrito_expira_en', None)
        request.session.modified = True
        return {}, True, None
    return carrito, False, expiracion


def _guardar_carrito(request, carrito, reiniciar_expiracion=False):
    request.session['carrito'] = carrito
    if carrito:
        expiracion = request.session.get('carrito_expira_en')
        if reiniciar_expiracion or not isinstance(expiracion, int):
            expiracion = timezone.now() + timedelta(minutes=CARRITO_DURACION_MINUTOS)
            request.session['carrito_expira_en'] = int(expiracion.timestamp())
    else:
        request.session.pop('carrito_expira_en', None)
    request.session.modified = True


def _items_de_pago(pago):
    """Devuelve las tallas y cantidades persistidas por el servidor en un intento de pago."""
    try:
        items = pago.items
        if not isinstance(items, list) or not items:
            raise ValueError
        resultado = []
        ids = set()
        for item in items:
            variante_id = int(item['variante_id'])
            producto_id = int(item['producto_id'])
            cantidad = _cantidad_valida(item['cantidad'])
            precio = int(item['precio_unitario'])
            if variante_id in ids or precio < 1:
                raise ValueError
            ids.add(variante_id)
            resultado.append((variante_id, producto_id, cantidad, precio))
        return resultado
    except (KeyError, TypeError, ValueError, CarritoInvalido):
        raise PagoNoConfirmado('El detalle del pago es inválido.')


def _liberar_reserva_bloqueada(pago, estado):
    """Libera una reserva; el PagoPendiente debe venir bloqueado dentro de una transacción."""
    if not pago.reserva_activa:
        return
    try:
        items = _items_de_pago(pago)
    except PagoNoConfirmado:
        items = []

    variantes = {
        variante.id: variante
        for variante in VarianteProducto.objects.select_for_update().filter(
            id__in=[item[0] for item in items]
        )
    }
    for variante_id, _, cantidad, _ in items:
        variante = variantes.get(variante_id)
        if variante:
            variante.stock_reservado = max(0, variante.stock_reservado - cantidad)
            variante.save(update_fields=['stock_reservado'])

    pago.reserva_activa = False
    pago.reserva_expira_en = None
    pago.estado = estado
    pago.save(update_fields=['reserva_activa', 'reserva_expira_en', 'estado'])


def _liberar_reservas_expiradas():
    """Devuelve inventario retenido cuando Mercado Pago ya no debe usar esa preferencia."""
    with transaction.atomic():
        pagos = list(
            PagoPendiente.objects.select_for_update().filter(
                reserva_activa=True,
                reserva_expira_en__lte=timezone.now(),
            )
        )
        for pago in pagos:
            _liberar_reserva_bloqueada(pago, 'EXPIRADO')


def _crear_pago_con_reserva(form, items, subtotal, entrega):
    """Reserva las tallas por 15 minutos antes de redirigir al PSP."""
    _liberar_reservas_expiradas()
    with transaction.atomic():
        ids = [item['variante_id'] for item in items]
        variantes = {
            variante.id: variante
            for variante in VarianteProducto.objects.select_for_update().select_related('producto').filter(id__in=ids)
        }
        if len(variantes) != len(ids):
            raise CarritoInvalido('Una talla ya no está disponible.')

        for item in items:
            variante = variantes[item['variante_id']]
            if variante.producto_id != item['producto_id'] or variante.stock_disponible < item['cantidad']:
                raise CarritoInvalido(
                    f"No queda stock disponible para {variante.producto.nombre} en talla {variante.talla}."
                )

        expira_en = timezone.now() + timedelta(minutes=RESERVA_DURACION_MINUTOS)
        pago = PagoPendiente.objects.create(
            rut=form.cleaned_data['rut'],
            nombre=form.cleaned_data['nombre'],
            telefono=form.cleaned_data['telefono'],
            direccion=form.cleaned_data['direccion'],
            tipo_entrega=form.cleaned_data['tipo_entrega'],
            items=items,
            subtotal_productos=subtotal,
            costo_despacho=entrega['costo'],
            plazo_entrega=entrega['plazo'],
            mecanismo_seguimiento=entrega['seguimiento'],
            total=subtotal + entrega['costo'],
            reserva_activa=True,
            reserva_expira_en=expira_en,
        )
        for item in items:
            variante = variantes[item['variante_id']]
            variante.stock_reservado += item['cantidad']
            variante.save(update_fields=['stock_reservado'])
        return pago


def _cancelar_reserva(pago):
    with transaction.atomic():
        pago_bloqueado = PagoPendiente.objects.select_for_update().get(pk=pago.pk)
        if pago_bloqueado.estado == 'PENDIENTE':
            _liberar_reserva_bloqueada(pago_bloqueado, 'CANCELADO')


def _referencias_de_sesion(request):
    referencias = request.session.get('pagos_pendientes', [])
    return {str(referencia) for referencia in referencias if isinstance(referencia, str)}


def _registrar_referencia_en_sesion(request, referencia):
    referencias = _referencias_de_sesion(request)
    referencias.add(str(referencia))
    request.session['pagos_pendientes'] = list(referencias)[-20:]
    request.session.modified = True


def _extraer_id_pago(request):
    payment_id = request.GET.get('payment_id') or request.GET.get('id') or request.GET.get('data.id')
    if payment_id:
        return str(payment_id)
    if request.method == 'POST' and request.body:
        try:
            cuerpo = json.loads(request.body.decode('utf-8'))
            return str(cuerpo.get('data', {}).get('id', ''))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return ''
    return ''


def _confirmar_pago(payment_id, referencia):
    """Verifica el pago contra Mercado Pago y crea el pedido de manera atómica."""
    if not re.fullmatch(r'\d{1,100}', str(payment_id)):
        raise PagoNoConfirmado('El identificador de pago no es válido.')
    try:
        respuesta = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN).payment().get(str(payment_id))
        pago_mp = respuesta.get('response', {})
    except Exception as exc:
        logger.warning('No fue posible consultar el pago %s: %s', payment_id, exc)
        raise PagoNoConfirmado('No fue posible validar el pago.')

    if (
        pago_mp.get('status') != 'approved'
        or pago_mp.get('external_reference') != str(referencia)
        or pago_mp.get('currency_id') != 'CLP'
    ):
        raise PagoNoConfirmado('El pago no está aprobado para esta compra.')
    try:
        monto_pagado = Decimal(str(pago_mp.get('transaction_amount')))
    except (InvalidOperation, TypeError, ValueError):
        raise PagoNoConfirmado('Mercado Pago informó un monto inválido.')

    with transaction.atomic():
        pago = PagoPendiente.objects.select_for_update().select_related('pedido').get(referencia=referencia)
        if pago.pedido_id:
            if pago.mercadopago_payment_id != str(payment_id):
                raise PagoNoConfirmado('El pago no corresponde a esta compra.')
            return pago.pedido
        if pago.estado in {'SIN_STOCK', 'EXPIRADO', 'CANCELADO'}:
            return None
        if pago.mercadopago_payment_id and pago.mercadopago_payment_id != str(payment_id):
            raise PagoNoConfirmado('Ya existe otro pago asociado a esta compra.')
        if monto_pagado != Decimal(pago.total):
            raise PagoNoConfirmado('El monto pagado no coincide con el carrito validado.')

        if pago.reserva_activa and pago.reserva_expira_en and pago.reserva_expira_en <= timezone.now():
            _liberar_reserva_bloqueada(pago, 'EXPIRADO')
            return None

        items = _items_de_pago(pago)
        variantes_ids = [item[0] for item in items]

        variantes = {
            variante.id: variante
            for variante in VarianteProducto.objects.select_for_update().select_related('producto').filter(id__in=variantes_ids)
        }
        if len(variantes) != len(variantes_ids):
            raise PagoNoConfirmado('Una talla pagada ya no existe.')

        detalle_validado, total_calculado = [], 0
        for variante_id, producto_id, cantidad, precio in items:
            variante = variantes[variante_id]
            if variante.producto_id != producto_id or precio < 1:
                raise PagoNoConfirmado('El detalle del pago no coincide con el inventario.')
            reserva_invalida = pago.reserva_activa and variante.stock_reservado < cantidad
            if variante.stock < cantidad or reserva_invalida:
                if pago.reserva_activa:
                    _liberar_reserva_bloqueada(pago, 'SIN_STOCK')
                else:
                    pago.estado = 'SIN_STOCK'
                    pago.mercadopago_payment_id = str(payment_id)
                    pago.save(update_fields=['estado', 'mercadopago_payment_id'])
                return None
            detalle_validado.append((variante, cantidad, precio))
            total_calculado += cantidad * precio

        subtotal_esperado = pago.subtotal_productos or total_calculado
        if (
            total_calculado != subtotal_esperado
            or subtotal_esperado + pago.costo_despacho != pago.total
        ):
            raise PagoNoConfirmado('El total del detalle no coincide con el pago.')

        cliente, creado = Cliente.objects.get_or_create(
            rut=pago.rut,
            defaults={'nombre': pago.nombre, 'telefono': pago.telefono, 'direccion': pago.direccion},
        )
        if not creado:
            cliente.nombre, cliente.telefono, cliente.direccion = pago.nombre, pago.telefono, pago.direccion
            cliente.save(update_fields=['nombre', 'telefono', 'direccion'])

        pedido = Pedido.objects.create(
            cliente=cliente,
            tipo_entrega=pago.tipo_entrega,
            estado='Preparando',
            costo_despacho=pago.costo_despacho,
            plazo_entrega=pago.plazo_entrega,
            mecanismo_seguimiento=pago.mecanismo_seguimiento,
        )
        for variante, cantidad, precio in detalle_validado:
            variante.stock -= cantidad
            if pago.reserva_activa:
                variante.stock_reservado = max(0, variante.stock_reservado - cantidad)
                variante.save(update_fields=['stock', 'stock_reservado'])
            else:
                variante.save(update_fields=['stock'])
            DetallePedido.objects.create(pedido=pedido, variante=variante, cantidad=cantidad, precio_unitario=precio)

        pago.estado = 'CONFIRMADO'
        pago.mercadopago_payment_id = str(payment_id)
        pago.pedido = pedido
        pago.reserva_activa = False
        pago.reserva_expira_en = None
        pago.save(update_fields=[
            'estado', 'mercadopago_payment_id', 'pedido', 'reserva_activa', 'reserva_expira_en',
        ])
        return pedido


def catalogo(request):
    _liberar_reservas_expiradas()
    query = (request.GET.get('q') or '').strip()
    categoria = request.GET.get('categoria', '')
    categorias = Producto.CATEGORIAS
    categorias_validas = {codigo for codigo, _ in categorias}
    if categoria not in categorias_validas:
        categoria = ''

    productos = Producto.objects.all()
    if query:
        productos = productos.filter(nombre__icontains=query)
    if categoria:
        productos = productos.filter(categoria=categoria)
    return render(request, 'index.html', {
        'productos': productos.order_by('nombre'),
        'query': query,
        'categorias': categorias,
        'categoria_actual': categoria,
        'categoria_actual_nombre': dict(categorias).get(categoria, ''),
    })


def producto_detalle(request, producto_id):
    _liberar_reservas_expiradas()
    producto = get_object_or_404(Producto, id=producto_id)
    return render(request, 'detalle.html', {'producto': producto})


@require_POST
def agregar_carrito(request, producto_id):
    _liberar_reservas_expiradas()
    producto = get_object_or_404(Producto, id=producto_id)
    try:
        cantidad = _cantidad_valida(request.POST.get('cantidad'))
        variante_id = int(request.POST.get('variante_id', ''))
    except (ValueError, CarritoInvalido):
        return HttpResponseBadRequest('La talla o cantidad enviada no es válida.')
    variante = get_object_or_404(VarianteProducto, id=variante_id, producto=producto)
    if variante.stock_disponible < cantidad:
        return HttpResponseBadRequest('No hay stock suficiente para esa talla.')

    carrito, _, _ = _obtener_carrito_vigente(request)
    item_id = f'{producto.id}_{variante.id}'
    cantidad_existente = 0
    if item_id in carrito:
        try:
            cantidad_existente = _cantidad_valida(carrito[item_id].get('cantidad'))
        except (AttributeError, CarritoInvalido):
            return HttpResponseBadRequest('El carrito contiene datos inválidos.')
    if cantidad_existente + cantidad > variante.stock_disponible:
        return HttpResponseBadRequest('No hay stock suficiente para la cantidad solicitada.')

    carrito[item_id] = {
        'producto_id': producto.id,
        'variante_id': variante.id,
        'nombre': producto.nombre,
        'talla': variante.talla,
        'precio': producto.precio,
        'cantidad': cantidad_existente + cantidad,
        'imagen_url': producto.imagen.url if producto.imagen else '',
    }
    _guardar_carrito(request, carrito, reiniciar_expiracion=True)
    return redirect('ver_carrito')


def ver_carrito(request):
    _liberar_reservas_expiradas()
    carrito_original, carrito_expirado, expiracion = _obtener_carrito_vigente(request)
    if not carrito_original:
        return render(request, 'carrito.html', {
            'carrito': {}, 'total': 0, 'carrito_expirado': carrito_expirado,
        })
    try:
        carrito, _, total = _normalizar_carrito(carrito_original)
    except CarritoInvalido:
        _guardar_carrito(request, {})
        return render(request, 'carrito.html', {'carrito': {}, 'total': 0})
    _guardar_carrito(request, carrito)
    return render(request, 'carrito.html', {
        'carrito': carrito,
        'total': total,
        'carrito_expira_en_timestamp': expiracion or request.session.get('carrito_expira_en'),
    })


@require_POST
def sumar_carrito(request, item_id):
    try:
        _liberar_reservas_expiradas()
        carrito_original, expirado, _ = _obtener_carrito_vigente(request)
        if expirado:
            return redirect('ver_carrito')
        carrito, _, _ = _normalizar_carrito(carrito_original)
    except CarritoInvalido:
        return HttpResponseBadRequest('El carrito contiene datos inválidos.')
    item = carrito.get(str(item_id))
    if item:
        variante = get_object_or_404(VarianteProducto, id=item['variante_id'], producto_id=item['producto_id'])
        if item['cantidad'] < variante.stock_disponible:
            item['cantidad'] += 1
    _guardar_carrito(request, carrito, reiniciar_expiracion=True)
    return redirect('ver_carrito')


@require_POST
def restar_carrito(request, item_id):
    carrito, expirado, _ = _obtener_carrito_vigente(request)
    if expirado:
        return redirect('ver_carrito')
    if isinstance(carrito, dict) and str(item_id) in carrito:
        try:
            cantidad = _cantidad_valida(carrito[str(item_id)].get('cantidad'))
        except (AttributeError, CarritoInvalido):
            return HttpResponseBadRequest('El carrito contiene datos inválidos.')
        if cantidad > 1:
            carrito[str(item_id)]['cantidad'] = cantidad - 1
        else:
            del carrito[str(item_id)]
        _guardar_carrito(request, carrito, reiniciar_expiracion=True)
    return redirect('ver_carrito')


@require_POST
def eliminar_del_carrito(request, item_id):
    carrito, expirado, _ = _obtener_carrito_vigente(request)
    if expirado:
        return redirect('ver_carrito')
    if isinstance(carrito, dict) and str(item_id) in carrito:
        del carrito[str(item_id)]
        _guardar_carrito(request, carrito, reiniciar_expiracion=True)
    return redirect('ver_carrito')


def crear_pedido(request):
    _liberar_reservas_expiradas()
    carrito_original, expirado, expiracion = _obtener_carrito_vigente(request)
    if expirado:
        messages.info(request, 'Tu carrito expiró después de 24 horas sin cambios.')
        return redirect('ver_carrito')
    try:
        carrito, items, total = _normalizar_carrito(carrito_original)
    except CarritoInvalido:
        _guardar_carrito(request, {})
        return redirect('ver_carrito')
    _guardar_carrito(request, carrito)

    form = CheckoutForm(request.POST or None)
    tipo_entrega = request.POST.get('tipo_entrega') if request.method == 'POST' else None
    estacion_metro = request.POST.get('estacion_metro') if request.method == 'POST' else None
    try:
        entrega_previsualizada = (
            informacion_entrega(tipo_entrega, estacion_metro)
            if tipo_entrega in OPCIONES_ENTREGA
            and (tipo_entrega != 'Metro' or estacion_metro in ESTACIONES_METRO)
            else None
        )
    except ValueError:
        entrega_previsualizada = None
    if request.method == 'POST' and form.is_valid():
        entrega = informacion_entrega(
            form.cleaned_data['tipo_entrega'],
            form.cleaned_data['estacion_metro'],
        )
        try:
            pago = _crear_pago_con_reserva(form, items, total, entrega)
        except CarritoInvalido as exc:
            form.add_error(None, str(exc))
            pago = None

        if pago:
            _registrar_referencia_en_sesion(request, pago.referencia)
        items_mp = [
            {'title': item['nombre'], 'quantity': item['cantidad'], 'unit_price': float(item['precio_unitario']), 'currency_id': 'CLP'}
            for item in items
        ]
        if pago:
            if entrega['costo']:
                items_mp.append({
                    'title': f"Despacho: {entrega['nombre']}",
                    'quantity': 1,
                    'unit_price': float(entrega['costo']),
                    'currency_id': 'CLP',
                })
            preference_data = {
                'items': items_mp,
                'external_reference': str(pago.referencia),
                'back_urls': {
                    'success': f'https://logisflow.alwaysdata.net/exito/{pago.referencia}/',
                    'failure': 'https://logisflow.alwaysdata.net/checkout/',
                    'pending': f'https://logisflow.alwaysdata.net/exito/{pago.referencia}/',
                },
                'auto_return': 'approved',
                'notification_url': 'https://logisflow.alwaysdata.net/webhook/',
                'expires': True,
                'expiration_date_from': timezone.now().isoformat(),
                'expiration_date_to': pago.reserva_expira_en.isoformat(),
            }
            try:
                preference_response = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN).preference().create(preference_data)
                init_point = preference_response.get('response', {}).get('init_point')
            except Exception as exc:
                logger.warning('No fue posible crear una preferencia de pago: %s', exc)
                init_point = None
            if init_point:
                return redirect(init_point)
            _cancelar_reserva(pago)
            form.add_error(None, 'No fue posible iniciar el pago. Intenta nuevamente.')
    return render(request, 'checkout.html', {
        'form': form,
        'carrito': carrito,
        'subtotal_productos': total,
        'total': total + (entrega_previsualizada['costo'] if entrega_previsualizada else 0),
        'entrega_previsualizada': entrega_previsualizada,
        'opciones_entrega': OPCIONES_ENTREGA,
        'estaciones_metro': estaciones_metro_para_checkout(),
        'carrito_expira_en_timestamp': expiracion or request.session.get('carrito_expira_en'),
        'carrito_duracion_horas': CARRITO_DURACION_HORAS,
    })


def pedido_exitoso(request, referencia):
    referencia = str(referencia)
    if referencia not in _referencias_de_sesion(request):
        return HttpResponseForbidden('No tienes autorización para ver esta confirmación.')
    pago = get_object_or_404(PagoPendiente.objects.select_related('pedido'), referencia=referencia)
    payment_id = _extraer_id_pago(request)
    if payment_id and not pago.pedido_id:
        try:
            _confirmar_pago(payment_id, pago.referencia)
        except PagoNoConfirmado as exc:
            logger.info('Pago aún no confirmable para %s: %s', referencia, exc)
        pago.refresh_from_db()
    if not pago.pedido_id:
        return render(request, 'pago_pendiente.html', {'pago': pago}, status=202)
    _guardar_carrito(request, {})
    return render(request, 'exito.html', {'pedido': pago.pedido})


def seguimiento(request):
    pedido, error = None, None
    rut_consultado = ''
    codigo_consultado = ''
    if request.method == 'POST':
        rut_consultado = request.POST.get('rut', '').strip()
        codigo_consultado = re.sub(r'\s+', '', request.POST.get('codigo', '')).upper()
        try:
            rut = normalizar_rut(rut_consultado)
            if not re.fullmatch(r'LF-[A-F0-9]{10}', codigo_consultado):
                raise ValueError
            pedido = Pedido.objects.select_related('cliente').filter(
                cliente__rut=rut,
                codigo_seguimiento=codigo_consultado,
            ).first()
            if not pedido:
                error = 'No encontramos un pedido con ese RUT y código de seguimiento.'
        except ValueError:
            error = 'Ingresa un RUT y código de seguimiento válidos.'
    return render(request, 'seguimiento.html', {
        'pedido': pedido,
        'error': error,
        'rut_consultado': rut_consultado,
        'codigo_consultado': codigo_consultado,
    })


def politicas_despacho(request):
    return render(request, 'politicas_despacho.html', {
        'opciones_entrega': OPCIONES_ENTREGA.values(),
    })


@staff_member_required(login_url='login')
@require_POST
def actualizar_estado_pedido(request):
    pedido = get_object_or_404(Pedido, id=request.POST.get('pedido_id'))
    nuevo_estado = request.POST.get('estado')
    if pedido.estado != 'Entregado' and nuevo_estado in dict(Pedido.ESTADOS) and nuevo_estado != 'Pendiente':
        pedido.estado = nuevo_estado
        pedido.save(update_fields=['estado'])
    return redirect('dashboard')


@staff_member_required(login_url='login')
def dashboard(request):
    pedidos = Pedido.objects.exclude(estado='Pendiente').select_related('cliente').order_by('-fecha')
    abandonados = PagoPendiente.objects.filter(estado='PENDIENTE').order_by('-creado_en')
    detalles = DetallePedido.objects.filter(pedido__in=pedidos)
    ingresos_totales = (
        sum(detalle.precio_unitario * detalle.cantidad for detalle in detalles)
        + sum(pedido.costo_despacho for pedido in pedidos)
    )
    pedidos_pagados = pedidos.count()
    context = {
        'pedidos': pedidos, 'abandonados': abandonados, 'productos': Producto.objects.all().order_by('nombre'),
        'estados': Pedido.ESTADOS, 'total_pedidos': pedidos_pagados, 'ingresos_totales': ingresos_totales,
        'stock_critico': VarianteProducto.objects.filter(stock__lte=F('stock_reservado') + 3).count(), 'pedidos_pendientes': abandonados.count(),
        'ticket_promedio': int(ingresos_totales / pedidos_pagados) if pedidos_pagados else 0,
        'clientes_totales': Cliente.objects.count(),
    }
    return render(request, 'dashboard.html', context)


@staff_member_required(login_url='login')
@require_POST
def guardar_producto(request):
    producto_id = request.POST.get('id')
    producto = get_object_or_404(Producto, id=producto_id) if producto_id else None
    form = ProductoForm(request.POST, request.FILES, instance=producto)
    if form.is_valid():
        form.save()
    else:
        messages.error(request, 'No se guardó el producto: corrige los datos ingresados.')
    return redirect('dashboard')


@staff_member_required(login_url='login')
@require_POST
def guardar_talla(request):
    form = TallaForm(request.POST)
    if form.is_valid():
        producto = get_object_or_404(Producto, id=form.cleaned_data['producto_id'])
        with transaction.atomic():
            variante = VarianteProducto.objects.select_for_update().filter(
                producto=producto, talla=form.cleaned_data['talla'],
            ).first()
            if variante and form.cleaned_data['stock'] < variante.stock_reservado:
                messages.error(request, 'No puedes dejar el stock bajo las unidades reservadas para pagos en curso.')
            else:
                VarianteProducto.objects.update_or_create(
                    producto=producto,
                    talla=form.cleaned_data['talla'],
                    defaults={'stock': form.cleaned_data['stock']},
                )
    else:
        messages.error(request, 'No se actualizó el stock: revisa la talla y las unidades.')
    return redirect('dashboard')


@staff_member_required(login_url='login')
@require_POST
def eliminar_producto(request, producto_id):
    Producto.objects.filter(id=producto_id).delete()
    return redirect('dashboard')


@staff_member_required(login_url='login')
def exportar_excel(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="Reporte_Ventas.csv"'
    response.write(u'\ufeff'.encode('utf8'))
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['N° Ticket', 'Código seguimiento', 'Fecha', 'Cliente', 'RUT', 'Estado', 'Modo Entrega', 'Total Pagado ($)'])
    for pedido in Pedido.objects.select_related('cliente').all().order_by('-fecha'):
        total = (
            sum(detalle.precio_unitario * detalle.cantidad for detalle in DetallePedido.objects.filter(pedido=pedido))
            + pedido.costo_despacho
        )
        writer.writerow([pedido.id, pedido.codigo_seguimiento, pedido.fecha.strftime('%d/%m/%Y %H:%M'), pedido.cliente.nombre,
                         pedido.cliente.rut, pedido.estado, pedido.get_tipo_entrega_display(), total])
    return response


@csrf_exempt
@require_POST
def webhook_mercadopago(request):
    payment_id = _extraer_id_pago(request)
    if not payment_id:
        return HttpResponse(status=200)
    try:
        datos = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN).payment().get(payment_id).get('response', {})
        referencia = datos.get('external_reference')
        if referencia:
            _confirmar_pago(payment_id, referencia)
    except (PagoNoConfirmado, PagoPendiente.DoesNotExist) as exc:
        logger.warning('Webhook de Mercado Pago no confirmado: %s', exc)
    except Exception:
        logger.exception('Error inesperado al procesar webhook de Mercado Pago.')
    return HttpResponse(status=200)
