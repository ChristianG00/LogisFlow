import csv
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

import mercadopago
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.encoding import force_bytes, force_str
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    AccesoClienteForm,
    CheckoutForm,
    ConsultaRespuestaSoporteForm,
    DireccionClienteForm,
    IncidenteTecnicoForm,
    ProductoForm,
    ReenviarVerificacionCorreoForm,
    RespuestaSoporteForm,
    RestablecerClaveClienteForm,
    RegistroClienteForm,
    SoporteInvitadoForm,
    SolicitudRecuperacionClaveClienteForm,
    SolicitudPrivacidadForm,
    TallaForm,
    normalizar_rut,
)
from .logistica import (
    OPCIONES_ENTREGA,
    ESTACIONES_METRO,
    estaciones_metro_para_checkout,
    tarifas_metro_para_checkout,
    informacion_entrega,
)
from .models import Cliente, DetallePedido, IncidenteTecnico, PagoPendiente, Pedido, Producto, SolicitudPrivacidad, VarianteProducto
from .privacidad import anonimizar_datos_vencidos


logger = logging.getLogger(__name__)
MAX_CANTIDAD_POR_LINEA = 1000
CARRITO_DURACION_MINUTOS = 15
RESERVA_DURACION_MINUTOS = 15
SLA_DIAS_HABILES = {'Metro': 2, 'Delivery': 5}

# Agrupa las categorías antiguas sin repetirlas en la tienda
CATEGORIAS_CATALOGO = (
    ('POLERAS', 'Poleras y tops', ('POLERAS', 'SUPERIOR')),
    ('BLUSAS', 'Blusas', ('BLUSAS',)),
    ('PANTALONES', 'Pantalones y jeans', ('PANTALONES', 'INFERIOR')),
    ('FALDAS', 'Faldas', ('FALDAS',)),
    ('VESTIDOS', 'Vestidos', ('VESTIDOS',)),
    ('CHAQUETAS', 'Chaquetas y abrigos', ('CHAQUETAS',)),
    ('CALZADO', 'Calzado', ('CALZADO',)),
    ('ACCESORIO', 'Accesorios', ('ACCESORIO',)),
)


class CarritoInvalido(ValueError):
    pass


class PagoNoConfirmado(ValueError):
    pass


def _destinatarios_incidente_tecnico():
    correos_configurados = os.getenv('TECHNICAL_SUPPORT_EMAILS', '')
    destinatarios = [
        correo.strip().lower()
        for correo in correos_configurados.split(',')
        if '@' in correo.strip()
    ]
    if not destinatarios and settings.EMAIL_HOST_USER:
        destinatarios.append(settings.EMAIL_HOST_USER)
    return list(dict.fromkeys(destinatarios))


def _perfil_cliente(request):
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.perfil_cliente
    except Cliente.DoesNotExist:
        return None


def _destino_seguro(request, predeterminado):
    destino = request.POST.get('next') or request.GET.get('next')
    if destino and url_has_allowed_host_and_scheme(
        destino,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destino
    return predeterminado


def _sumar_dias_habiles(fecha_inicial, dias_habiles):
    fecha = fecha_inicial
    dias_restantes = dias_habiles
    while dias_restantes:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            dias_restantes -= 1
    return fecha


def _preparar_indicadores_sla(pedidos):
    hoy = timezone.localdate()

    for pedido in pedidos:
        dias_habiles = SLA_DIAS_HABILES.get(pedido.tipo_entrega)
        pedido.sla_aplicable = bool(dias_habiles)
        pedido.sla_limite = None
        pedido.sla_etiqueta = 'Coordinación manual'
        pedido.sla_clase = 'secondary'

        if not dias_habiles:
            continue

        fecha_confirmacion = timezone.localtime(pedido.fecha).date()
        pedido.sla_limite = _sumar_dias_habiles(fecha_confirmacion, dias_habiles)

        if pedido.estado == 'Entregado':
            if not pedido.fecha_entregado:
                pedido.sla_etiqueta = 'Sin registro histórico'
                continue
            fecha_entrega = timezone.localtime(pedido.fecha_entregado).date()
            if fecha_entrega <= pedido.sla_limite:
                pedido.sla_etiqueta = 'Cumplido'
                pedido.sla_clase = 'success'
            else:
                pedido.sla_etiqueta = 'Fuera de plazo'
                pedido.sla_clase = 'danger'
            continue

        if pedido.sla_limite < hoy:
            pedido.sla_etiqueta = 'Plazo vencido'
            pedido.sla_clase = 'danger'
        elif pedido.sla_limite == hoy:
            pedido.sla_etiqueta = 'Vence hoy'
            pedido.sla_clase = 'warning'
        else:
            pedido.sla_etiqueta = 'En plazo'
            pedido.sla_clase = 'success'


def _nombre_usuario_cliente():
    usuario_modelo = get_user_model()
    while True:
        nombre = f'cliente-{secrets.token_urlsafe(12)}'
        if not usuario_modelo.objects.filter(username=nombre).exists():
            return nombre


def _enviar_verificacion_correo_cliente(request, cliente):
    # Envía el enlace para activar la cuenta
    usuario = cliente.usuario
    uidb64 = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    enlace = request.build_absolute_uri(reverse(
        'verificar_correo_cliente',
        kwargs={'uidb64': uidb64, 'token': token},
    ))
    send_mail(
        subject='Confirma tu correo de LogisFlow',
        message=(
            f'Hola {cliente.nombre},\n\n'
            'Para activar tu cuenta cliente y confirmar que este correo te pertenece, '
            'abre este enlace temporal:\n\n'
            f'{enlace}\n\n'
            'Si no creaste una cuenta en LogisFlow, puedes ignorar este correo. '
            'No compartas este enlace con nadie.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[cliente.email],
        fail_silently=False,
    )


def _enviar_confirmacion_pedido(pedido_id):
    # Envía el comprobante solo después de confirmar el pedido
    try:
        pedido = (
            Pedido.objects.select_related('cliente', 'pago')
            .prefetch_related('detallepedido_set__variante__producto')
            .get(pk=pedido_id)
        )
    except Pedido.DoesNotExist:
        logger.warning('No se encontró el pedido %s para enviar su confirmación.', pedido_id)
        return

    correo = (pedido.cliente.email or '').strip()
    if not correo:
        logger.info('El pedido %s no tiene correo para enviar confirmación.', pedido_id)
        return

    lineas_productos = []
    for detalle in pedido.detallepedido_set.all():
        if detalle.variante_id:
            lineas_productos.append(
                f'- {detalle.variante.producto.nombre} · talla {detalle.variante.talla} '
                f'· {detalle.cantidad} unidad(es) · ${detalle.precio_unitario * detalle.cantidad}'
            )

    flete = (
        'Flete Starken: por pagar al recibir el despacho.'
        if pedido.tipo_entrega == 'Delivery' and not pedido.costo_despacho
        else f'Despacho: ${pedido.costo_despacho}.' if pedido.costo_despacho
        else 'Despacho: sin cargo.'
    )
    mensaje = (
        f'Hola {pedido.cliente.nombre},\n\n'
        'Tu pago fue aprobado y tu pedido quedó confirmado en LogisFlow.\n\n'
        f'Código de seguimiento: {pedido.codigo_seguimiento}\n'
        f'Modalidad de entrega: {pedido.get_tipo_entrega_display()}\n'
        f'Plazo estimado: {pedido.plazo_entrega}\n'
        f'{flete}\n'
        f'Total pagado hoy: ${pedido.pago.total}\n\n'
        'Productos:\n'
        f'{chr(10).join(lineas_productos) or "Sin detalle disponible."}\n\n'
        f'Seguimiento: {pedido.mecanismo_seguimiento}\n\n'
        'Guarda el código de seguimiento. Para consultar tu pedido en la tienda '
        'necesitarás ese código junto con el RUT usado en la compra.\n\n'
        'Equipo LogisFlow'
    )
    try:
        send_mail(
            subject=f'LogisFlow · Pedido confirmado {pedido.codigo_seguimiento}',
            message=mensaje,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[correo],
            fail_silently=False,
        )
    except Exception:
        logger.exception('No fue posible enviar la confirmación del pedido %s.', pedido.pk)


def _firma_webhook_mercadopago_valida(request):
    # Comprueba que la notificación fue firmada por Mercado Pago
    secreto = os.getenv('MP_WEBHOOK_SECRET', '').strip()
    firma = request.headers.get('x-signature', '')
    request_id = request.headers.get('x-request-id', '')
    data_id = request.GET.get('data.id', '')
    if not secreto or not firma:
        logger.error('Webhook de Mercado Pago rechazado por falta de firma o secreto configurado.')
        return False

    partes = {}
    for segmento in firma.split(','):
        clave, separador, valor = segmento.strip().partition('=')
        if separador and clave.strip().lower() in {'ts', 'v1'}:
            partes[clave.strip().lower()] = valor.strip()

    timestamp = partes.get('ts', '')
    firma_recibida = partes.get('v1', '')
    if not timestamp or not firma_recibida:
        logger.warning('Webhook de Mercado Pago rechazado por formato de firma inválido.')
        return False

    manifest = ''
    if data_id:
        manifest += f'id:{data_id};'
    if request_id:
        manifest += f'request-id:{request_id};'
    manifest += f'ts:{timestamp};'
    firma_esperada = hmac.new(
        secreto.encode('utf-8'),
        manifest.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(firma_esperada, firma_recibida)


def _mostrar_acceso_cliente(
    request,
    *,
    formulario_ingreso=None,
    formulario_registro=None,
    modo='ingreso',
):
    # Muestra el panel de acceso de clientes
    return render(request, 'cliente_login.html', {
        'formulario_ingreso': formulario_ingreso or AccesoClienteForm(prefix='ingreso'),
        'formulario_registro': formulario_registro or RegistroClienteForm(prefix='registro'),
        'modo_auth': modo,
        'next': request.POST.get('next') or request.GET.get('next', ''),
        'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
    })


def _validar_recaptcha(request):
    # Valida reCAPTCHA antes de continuar
    token = request.POST.get('g-recaptcha-response', '').strip()
    if not token:
        return False, 'Confirma que no eres un robot para continuar.'
    if not settings.RECAPTCHA_PUBLIC_KEY or not settings.RECAPTCHA_PRIVATE_KEY:
        logger.error('reCAPTCHA no está configurado en el entorno.')
        return False, 'La verificación de seguridad no está disponible. Inténtalo más tarde.'
    try:
        datos = urlencode({
            'secret': settings.RECAPTCHA_PRIVATE_KEY,
            'response': token,
        }).encode('utf-8')
        peticion = Request('https://www.google.com/recaptcha/api/siteverify', data=datos)
        with urlopen(peticion, timeout=8) as respuesta:
            resultado = json.loads(respuesta.read().decode('utf-8'))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        logger.warning('No fue posible validar reCAPTCHA.', exc_info=True)
        return False, 'No pudimos validar la verificación de seguridad. Inténtalo nuevamente.'
    if resultado.get('success') is True:
        return True, ''
    return False, 'La verificación de seguridad no fue válida. Inténtalo nuevamente.'


def _cantidad_valida(valor):
    try:
        cantidad = int(str(valor))
    except (TypeError, ValueError):
        raise CarritoInvalido('La cantidad debe ser un número entero.')
    if not 1 <= cantidad <= MAX_CANTIDAD_POR_LINEA:
        raise CarritoInvalido('La cantidad debe estar entre 1 y 1000.')
    return cantidad


def _normalizar_carrito(carrito):
    # Vuelve a calcular el carrito con datos de la base de datos
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
    # Revisa la vigencia del carrito en el servidor
    carrito = request.session.get('carrito', {})
    expiracion = request.session.get('carrito_expira_en')
    duracion_guardada = request.session.get('carrito_duracion_minutos')
    if not isinstance(carrito, dict):
        carrito = {}

    try:
        expiracion = int(expiracion) if expiracion is not None else None
    except (TypeError, ValueError):
        expiracion = None

    ahora = int(timezone.now().timestamp())
    # Los carritos antiguos también vencen a los 15 minutos
    if carrito and (
        duracion_guardada != CARRITO_DURACION_MINUTOS
        or expiracion is None
        or expiracion <= ahora
    ):
        request.session.pop('carrito', None)
        request.session.pop('carrito_expira_en', None)
        request.session.pop('carrito_duracion_minutos', None)
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
        request.session['carrito_duracion_minutos'] = CARRITO_DURACION_MINUTOS
    else:
        request.session.pop('carrito_expira_en', None)
        request.session.pop('carrito_duracion_minutos', None)
    request.session.modified = True


def _items_de_pago(pago):
    # Lee las prendas y tallas guardadas para este pago
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
    # Libera el stock reservado dentro de una transacción
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
    # Devuelve el stock de reservas vencidas
    with transaction.atomic():
        pagos = list(
            PagoPendiente.objects.select_for_update().filter(
                reserva_activa=True,
                reserva_expira_en__lte=timezone.now(),
            )
        )
        for pago in pagos:
            _liberar_reserva_bloqueada(pago, 'EXPIRADO')
    anonimizar_datos_vencidos()


def _crear_pago_con_reserva(form, items, subtotal, entrega):
    # Reserva stock por 15 minutos antes de ir a pagar
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
            email=form.cleaned_data['email'],
            telefono=form.cleaned_data['telefono'],
            direccion=form.cleaned_data['direccion'],
            tipo_entrega=form.cleaned_data['tipo_entrega'],
            guardar_direccion_predeterminada=form.cleaned_data.get(
                'guardar_direccion_predeterminada', False,
            ),
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
    # Confirma el pago y crea el pedido en una transacción
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
        # Bloquea solo el pago pendiente; pedido es una relación opcional y PostgreSQL
        # no permite bloquearla junto con un LEFT OUTER JOIN
        pago = PagoPendiente.objects.select_for_update().get(referencia=referencia)
        if pago.pedido_id:
            if pago.mercadopago_payment_id != str(payment_id):
                raise PagoNoConfirmado('El pago no corresponde a esta compra.')
            return Pedido.objects.get(id=pago.pedido_id)
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

        direccion_inicial = pago.direccion if pago.tipo_entrega == 'Delivery' else ''
        cliente, creado = Cliente.objects.get_or_create(
            rut=pago.rut,
            defaults={
                'nombre': pago.nombre,
                'email': pago.email,
                'telefono': pago.telefono,
                'direccion': direccion_inicial,
            },
        )
        if not creado:
            cliente.nombre = pago.nombre
            cliente.email = pago.email
            cliente.telefono = pago.telefono
            campos_actualizados = ['nombre', 'email', 'telefono']
            if (
                pago.tipo_entrega == 'Delivery'
                and pago.guardar_direccion_predeterminada
            ):
                cliente.direccion = pago.direccion
                campos_actualizados.append('direccion')
            cliente.save(update_fields=campos_actualizados)
            if cliente.usuario_id and cliente.usuario.email != pago.email:
                cliente.usuario.email = pago.email
                cliente.usuario.save(update_fields=['email'])

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
        transaction.on_commit(
            lambda pedido_id=pedido.id: _enviar_confirmacion_pedido(pedido_id)
        )
        return pedido


def catalogo(request):
    _liberar_reservas_expiradas()
    query = (request.GET.get('q') or '').strip()
    categoria = request.GET.get('categoria', '')
    categorias = [(codigo, nombre) for codigo, nombre, _ in CATEGORIAS_CATALOGO]
    categorias_por_codigo = {codigo: (nombre, incluidas) for codigo, nombre, incluidas in CATEGORIAS_CATALOGO}
    categorias_validas = set(categorias_por_codigo)
    if categoria not in categorias_validas:
        categoria = ''

    productos = Producto.objects.all()
    if query:
        productos = productos.filter(nombre__icontains=query)
    if categoria:
        productos = productos.filter(categoria__in=categorias_por_codigo[categoria][1])
    productos = list(productos.order_by('nombre').prefetch_related('variantes'))
    categorias_publicas = {
        categoria_interna: nombre
        for _, nombre, categorias_internas in CATEGORIAS_CATALOGO
        for categoria_interna in categorias_internas
    }
    for producto in productos:
        producto.categoria_publica = categorias_publicas.get(
            producto.categoria,
            producto.get_categoria_display(),
        )
    return render(request, 'index.html', {
        'productos': productos,
        'query': query,
        'categorias': categorias,
        'categoria_actual': categoria,
        'categoria_actual_nombre': categorias_por_codigo.get(categoria, ('', ()))[0],
    })


def registro_cliente(request):
    if request.user.is_authenticated:
        return redirect('mi_cuenta' if _perfil_cliente(request) else 'catalogo')

    form = RegistroClienteForm(request.POST or None, prefix='registro')
    if request.method == 'POST' and form.is_valid():
        recaptcha_valido, mensaje_recaptcha = _validar_recaptcha(request)
        if not recaptcha_valido:
            form.add_error(None, mensaje_recaptcha)
        else:
            usuario_modelo = get_user_model()
            try:
                with transaction.atomic():
                    usuario = usuario_modelo.objects.create_user(
                        username=_nombre_usuario_cliente(),
                        email=form.cleaned_data['email'],
                        password=form.cleaned_data['password1'],
                        is_active=False,
                    )
                    cliente = form.cliente_existente
                    if cliente:
                        cliente.nombre = form.cleaned_data['nombre']
                        cliente.email = form.cleaned_data['email']
                        cliente.telefono = form.cleaned_data['telefono']
                        cliente.usuario = usuario
                        cliente.save(update_fields=['nombre', 'email', 'telefono', 'usuario'])
                    else:
                        cliente = Cliente.objects.create(
                            usuario=usuario,
                            rut=form.cleaned_data['rut'],
                            nombre=form.cleaned_data['nombre'],
                            email=form.cleaned_data['email'],
                            telefono=form.cleaned_data['telefono'],
                            direccion='',
                        )
                    _enviar_verificacion_correo_cliente(request, cliente)
            except Exception:
                logger.exception('No fue posible enviar el correo de verificación de cuenta.')
                form.add_error(None, 'No pudimos enviar el correo de verificación. Revisa tu dirección e inténtalo nuevamente.')
            else:
                return render(request, 'verificar_correo.html', {
                    'form': ReenviarVerificacionCorreoForm(initial={'email': cliente.email}),
                    'correo_enviado': True,
                    'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
                })

    return _mostrar_acceso_cliente(
        request,
        formulario_registro=form,
        modo='registro',
    )


def iniciar_sesion_cliente(request):
    if request.user.is_authenticated:
        return redirect('mi_cuenta' if _perfil_cliente(request) else 'catalogo')

    form = AccesoClienteForm(request.POST or None, prefix='ingreso')
    if request.method == 'POST' and form.is_valid():
        identificador = form.cleaned_data['identificador']
        filtro = {'email__iexact': identificador} if '@' in identificador else {'telefono': identificador}
        cliente = Cliente.objects.select_related('usuario').filter(
            anonimizado_en__isnull=True,
            **filtro,
        ).first()
        usuario = None
        if cliente and cliente.usuario_id and not cliente.usuario.is_active:
            form.add_error(None, 'Confirma primero tu correo para activar la cuenta. Puedes solicitar un nuevo enlace más abajo.')
        elif cliente and cliente.usuario_id:
            usuario = authenticate(
                request,
                username=cliente.usuario.username,
                password=form.cleaned_data['password'],
            )
        if usuario and not usuario.is_staff:
            login(request, usuario)
            return redirect(_destino_seguro(request, 'catalogo'))
        if not (cliente and cliente.usuario_id and not cliente.usuario.is_active):
            form.add_error(None, 'Correo/celular o contraseña incorrectos.')
    return _mostrar_acceso_cliente(
        request,
        formulario_ingreso=form,
        modo='ingreso',
    )


def verificar_correo_cliente(request, uidb64, token):
    # Activa la cuenta al abrir el enlace del correo
    cliente = None
    try:
        usuario_id = force_str(urlsafe_base64_decode(uidb64))
        cliente = Cliente.objects.select_related('usuario').filter(
            usuario_id=usuario_id,
            anonimizado_en__isnull=True,
            usuario__is_active=False,
            usuario__is_staff=False,
        ).first()
    except (TypeError, ValueError, OverflowError):
        pass

    if not cliente or not default_token_generator.check_token(cliente.usuario, token):
        return render(request, 'verificar_correo.html', {
            'form': ReenviarVerificacionCorreoForm(),
            'enlace_invalido': True,
            'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
        }, status=400)

    cliente.usuario.is_active = True
    cliente.usuario.save(update_fields=['is_active'])
    messages.success(request, 'Tu correo fue confirmado. Ya puedes iniciar sesión en tu cuenta cliente.')
    return redirect('iniciar_sesion_cliente')


def reenviar_verificacion_correo_cliente(request):
    # Reenvía el enlace sin revelar si el correo existe
    if request.user.is_authenticated:
        return redirect('mi_cuenta' if _perfil_cliente(request) else 'catalogo')

    form = ReenviarVerificacionCorreoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        recaptcha_valido, mensaje_recaptcha = _validar_recaptcha(request)
        if not recaptcha_valido:
            form.add_error(None, mensaje_recaptcha)
        else:
            cliente = Cliente.objects.select_related('usuario').filter(
                email__iexact=form.cleaned_data['email'],
                anonimizado_en__isnull=True,
                usuario__is_active=False,
                usuario__is_staff=False,
            ).first()
            if cliente and cliente.usuario_id:
                try:
                    _enviar_verificacion_correo_cliente(request, cliente)
                except Exception:
                    logger.exception('No fue posible reenviar el correo de verificación de cuenta.')
                    messages.error(request, 'No pudimos enviar el correo en este momento. Inténtalo nuevamente más tarde.')
                    return render(request, 'verificar_correo.html', {
                        'form': form,
                        'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
                    })
            return render(request, 'verificar_correo.html', {
                'form': ReenviarVerificacionCorreoForm(initial={'email': form.cleaned_data['email']}),
                'correo_enviado': True,
                'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
            })

    return render(request, 'verificar_correo.html', {
        'form': form,
        'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
    })


def solicitar_recuperacion_clave_cliente(request):
    # Envía un enlace temporal para recuperar la clave
    if request.user.is_authenticated:
        return redirect('mi_cuenta' if _perfil_cliente(request) else 'catalogo')

    form = SolicitudRecuperacionClaveClienteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        recaptcha_valido, mensaje_recaptcha = _validar_recaptcha(request)
        if not recaptcha_valido:
            form.add_error(None, mensaje_recaptcha)
        else:
            cliente = Cliente.objects.select_related('usuario').filter(
                email__iexact=form.cleaned_data['email'],
                anonimizado_en__isnull=True,
                usuario__is_active=True,
                usuario__is_staff=False,
            ).first()
            if cliente and cliente.usuario_id:
                usuario = cliente.usuario
                uidb64 = urlsafe_base64_encode(force_bytes(usuario.pk))
                token = default_token_generator.make_token(usuario)
                enlace = request.build_absolute_uri(reverse(
                    'restablecer_clave_cliente',
                    kwargs={'uidb64': uidb64, 'token': token},
                ))
                try:
                    send_mail(
                        subject='Restablece tu contraseña de LogisFlow',
                        message=(
                            f'Hola {cliente.nombre},\n\n'
                            'Recibimos una solicitud para restablecer la contraseña de tu cuenta cliente. '
                            'Usa este enlace temporal para crear una nueva contraseña:\n\n'
                            f'{enlace}\n\n'
                            'Si no solicitaste este cambio, puedes ignorar este correo. '
                            'Tu contraseña actual seguirá protegida.'
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[cliente.email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception('No fue posible enviar el correo de recuperación de contraseña.')
                    messages.error(request, 'No pudimos enviar el correo en este momento. Inténtalo nuevamente más tarde.')
                    return render(request, 'recuperar_clave.html', {
                        'form': form,
                        'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
                    })

            # La respuesta no revela si el correo tiene una cuenta
            messages.success(request, 'Si el correo corresponde a una cuenta cliente, recibirás un enlace temporal para restablecer tu contraseña.')
            return redirect('iniciar_sesion_cliente')

    return render(request, 'recuperar_clave.html', {
        'form': form,
        'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
    })


def restablecer_clave_cliente(request, uidb64, token):
    # Valida el enlace antes de cambiar la contraseña
    cliente = None
    try:
        usuario_id = force_str(urlsafe_base64_decode(uidb64))
        cliente = Cliente.objects.select_related('usuario').filter(
            usuario_id=usuario_id,
            anonimizado_en__isnull=True,
            usuario__is_active=True,
            usuario__is_staff=False,
        ).first()
    except (TypeError, ValueError, OverflowError):
        pass

    if not cliente or not default_token_generator.check_token(cliente.usuario, token):
        return render(request, 'restablecer_clave.html', {'enlace_invalido': True}, status=400)

    form = RestablecerClaveClienteForm(request.POST or None, usuario=cliente.usuario)
    if request.method == 'POST' and form.is_valid():
        cliente.usuario.set_password(form.cleaned_data['password1'])
        cliente.usuario.save(update_fields=['password'])
        messages.success(request, 'Tu contraseña fue actualizada. Ya puedes iniciar sesión.')
        return redirect('iniciar_sesion_cliente')

    return render(request, 'restablecer_clave.html', {'form': form})


@require_POST
@login_required(login_url='iniciar_sesion_cliente')
def cerrar_sesion_cliente(request):
    logout(request)
    return redirect('catalogo')


@login_required(login_url='iniciar_sesion_cliente')
def mi_cuenta(request):
    cliente = _perfil_cliente(request)
    if not cliente:
        return HttpResponseForbidden('Esta sesión no corresponde a una cuenta de cliente.')
    pedidos = Pedido.objects.filter(cliente=cliente).prefetch_related(
        'detallepedido_set__variante__producto',
    ).order_by('-fecha')
    return render(request, 'mi_cuenta.html', {
        'cliente': cliente,
        'direccion_form': DireccionClienteForm(instance=cliente),
        'pedidos': pedidos,
        'solicitudes': cliente.solicitudes_privacidad.all()[:5],
    })


@require_POST
@login_required(login_url='iniciar_sesion_cliente')
def actualizar_direccion_cliente(request):
    cliente = _perfil_cliente(request)
    if not cliente:
        return HttpResponseForbidden('Esta sesión no corresponde a una cuenta de cliente.')
    form = DireccionClienteForm(request.POST, instance=cliente)
    if form.is_valid():
        form.save()
        messages.success(request, 'Tu dirección predeterminada para despacho fue actualizada.')
        return redirect('mi_cuenta')

    messages.error(request, 'No se actualizó la dirección. Revisa el dato indicado.')
    pedidos = Pedido.objects.filter(cliente=cliente).prefetch_related(
        'detallepedido_set__variante__producto',
    ).order_by('-fecha')
    return render(request, 'mi_cuenta.html', {
        'cliente': cliente,
        'direccion_form': form,
        'pedidos': pedidos,
        'solicitudes': cliente.solicitudes_privacidad.all()[:5],
    }, status=400)


def _datos_exportables_cliente(cliente):
    pedidos = Pedido.objects.filter(cliente=cliente).prefetch_related(
        'detallepedido_set__variante__producto',
    ).order_by('-fecha')
    datos_pedidos = []
    for pedido in pedidos:
        productos = []
        for detalle in pedido.detallepedido_set.all():
            if detalle.variante_id:
                productos.append({
                    'producto': detalle.variante.producto.nombre,
                    'talla': detalle.variante.talla,
                    'cantidad': detalle.cantidad,
                    'precio_unitario': detalle.precio_unitario,
                })
        datos_pedidos.append({
            'codigo_seguimiento': pedido.codigo_seguimiento,
            'fecha': pedido.fecha,
            'estado': pedido.estado,
            'tipo_entrega': pedido.get_tipo_entrega_display(),
            'costo_despacho': pedido.costo_despacho,
            'plazo_entrega': pedido.plazo_entrega,
            'productos': productos,
        })
    return datos_pedidos


@login_required(login_url='iniciar_sesion_cliente')
def descargar_resumen_mis_datos(request):
    cliente = _perfil_cliente(request)
    if not cliente:
        return HttpResponseForbidden('Esta sesión no corresponde a una cuenta de cliente.')
    datos_pedidos = _datos_exportables_cliente(cliente)
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        logger.exception('No está disponible la dependencia para generar el resumen PDF.')
        return HttpResponse(
            'El resumen PDF no está disponible en este momento. Inténtalo más tarde.',
            status=503,
        )

    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.7 * cm,
        leftMargin=1.7 * cm,
        topMargin=1.7 * cm,
        bottomMargin=1.7 * cm,
        title='Resumen de datos personales - LogisFlow',
    )
    estilos = getSampleStyleSheet()
    estilos.add(ParagraphStyle(
        name='LogisFlowTitulo',
        parent=estilos['Title'],
        textColor=colors.HexColor('#B4487A'),
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        alignment=TA_LEFT,
        spaceAfter=4,
    ))
    estilos.add(ParagraphStyle(
        name='LogisFlowSeccion',
        parent=estilos['Heading2'],
        textColor=colors.HexColor('#34233D'),
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        spaceBefore=16,
        spaceAfter=8,
    ))
    estilos.add(ParagraphStyle(
        name='LogisFlowTexto',
        parent=estilos['BodyText'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#39313D'),
    ))

    historia = [
        Paragraph('LogisFlow', estilos['LogisFlowTitulo']),
        Paragraph('Resumen de datos personales', estilos['Heading2']),
        Paragraph(
            f'Generado el {timezone.localtime().strftime("%d/%m/%Y a las %H:%M")}. '
            'Este documento reúne los datos asociados a tu cuenta y tus pedidos. '
            'No contiene contraseñas ni datos de tarjetas.',
            estilos['LogisFlowTexto'],
        ),
        Spacer(1, 0.35 * cm),
        Paragraph('Datos de la cuenta', estilos['LogisFlowSeccion']),
    ]
    filas_perfil = [
        ['Nombre', cliente.nombre],
        ['RUT', cliente.rut],
        ['Correo', cliente.email or 'No registrado'],
        ['Teléfono', cliente.telefono or 'No registrado'],
        ['Dirección predeterminada para despacho', cliente.direccion or 'No definida'],
    ]
    tabla_perfil = Table(filas_perfil, colWidths=[5.7 * cm, 11.0 * cm])
    tabla_perfil.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F8EAF0')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#34233D')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LEADING', (0, 0), (-1, -1), 12),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#E1D6DD')),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    historia.append(tabla_perfil)
    historia.append(Paragraph('Pedidos registrados', estilos['LogisFlowSeccion']))
    if not datos_pedidos:
        historia.append(Paragraph('No existen pedidos confirmados asociados a esta cuenta.', estilos['LogisFlowTexto']))
    for pedido in datos_pedidos:
        productos = '<br/>'.join(
            f"{escape(str(item['producto']))} · talla {escape(str(item['talla']))} · {item['cantidad']} un."
            for item in pedido['productos']
        ) or 'Sin productos registrados'
        historia.extend([
            Paragraph(
                f"<b>{escape(str(pedido['codigo_seguimiento']))}</b> · "
                f"{pedido['fecha'].strftime('%d/%m/%Y')} · {escape(str(pedido['estado']))}",
                estilos['LogisFlowTexto'],
            ),
            Paragraph(
                f"Entrega: {escape(str(pedido['tipo_entrega']))} · "
                f"Plazo: {escape(str(pedido['plazo_entrega']))}<br/>"
                f"Productos: {productos}",
                estilos['LogisFlowTexto'],
            ),
            Spacer(1, 0.2 * cm),
        ])

    documento.build(historia)
    respuesta = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    respuesta['Content-Disposition'] = 'attachment; filename="resumen-mis-datos-logisflow.pdf"'
    return respuesta


@login_required(login_url='iniciar_sesion_cliente')
def descargar_mis_datos(request):
    cliente = _perfil_cliente(request)
    if not cliente:
        return HttpResponseForbidden('Esta sesión no corresponde a una cuenta de cliente.')
    datos_pedidos = _datos_exportables_cliente(cliente)
    respuesta = HttpResponse(
        json.dumps({
            'perfil': {
                'rut': cliente.rut,
                'nombre': cliente.nombre,
                'email': cliente.email,
                'telefono': cliente.telefono,
                'direccion': cliente.direccion,
            },
            'pedidos': [
                {
                    **pedido,
                    'fecha': pedido['fecha'].isoformat(),
                }
                for pedido in datos_pedidos
            ],
        }, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8',
    )
    respuesta['Content-Disposition'] = 'attachment; filename="mis-datos-logisflow.json"'
    return respuesta


def solicitar_privacidad(request):
    # Registra consultas de soporte para revisión
    cliente = _perfil_cliente(request)
    if request.user.is_authenticated and not cliente:
        return HttpResponseForbidden('Esta sesión no corresponde a una cuenta de cliente.')
    es_invitada = cliente is None
    tipo_solicitado = request.GET.get('tipo', '')
    formulario_clase = SoporteInvitadoForm if es_invitada else SolicitudPrivacidadForm
    tipos_disponibles = [
        codigo for codigo, _ in formulario_clase.base_fields['tipo'].choices
    ]
    form = formulario_clase(
        request.POST or None,
        initial={'tipo': tipo_solicitado} if tipo_solicitado in tipos_disponibles else None,
    )
    modo_consulta = request.GET.get('modo') == 'consultar'
    datos_consulta = request.GET if modo_consulta and (
        'consulta-rut' in request.GET or 'consulta-codigo_consulta' in request.GET
    ) else None
    consulta_form = ConsultaRespuestaSoporteForm(
        datos_consulta,
        prefix='consulta',
    )
    consulta_resultado = None
    if datos_consulta and consulta_form.is_valid():
        consulta_resultado = SolicitudPrivacidad.objects.filter(
            cliente__rut=consulta_form.cleaned_data['rut'],
            codigo_consulta=consulta_form.cleaned_data['codigo_consulta'],
        ).first()
        if not consulta_resultado:
            consulta_form.add_error(None, 'No encontramos una consulta con esos datos. Revisa el RUT y el código.')

    if request.method == 'POST' and form.is_valid():
        recaptcha_valido, mensaje_recaptcha = _validar_recaptcha(request)
        if not recaptcha_valido:
            form.add_error(None, mensaje_recaptcha)
        else:
            cliente_solicitud = cliente
            if es_invitada:
                pedido = Pedido.objects.select_related('cliente').filter(
                    cliente__rut=form.cleaned_data['rut'],
                    codigo_seguimiento=form.cleaned_data['codigo_seguimiento'],
                ).first()
                if not pedido:
                    form.add_error(None, 'No encontramos una compra con ese RUT y código de seguimiento.')
                else:
                    cliente_solicitud = pedido.cliente
            if cliente_solicitud:
                solicitud = SolicitudPrivacidad.objects.create(
                    cliente=cliente_solicitud,
                    tipo=form.cleaned_data['tipo'],
                    detalle=form.cleaned_data['detalle'],
                )
                if es_invitada:
                    messages.success(
                        request,
                        f'Recibimos tu consulta. Guarda este código para revisar la respuesta: {solicitud.codigo_consulta}',
                    )
                    return redirect(f'{reverse("solicitar_privacidad")}?modo=consultar')
                messages.success(request, 'Recibimos tu consulta. Podrás ver la respuesta desde Mi cuenta.')
                return redirect('mi_cuenta')
    return render(request, 'solicitar_privacidad.html', {
        'form': form,
        'consulta_form': consulta_form,
        'consulta_resultado': consulta_resultado,
        'modo_consulta': modo_consulta,
        'recaptcha_site_key': settings.RECAPTCHA_PUBLIC_KEY,
        'es_invitada': es_invitada,
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


@require_POST
def agregar_outfit_carrito(request):
    # Agrega el outfit completo sin reemplazar lo que ya existe en el carrito
    try:
        variantes_ids = [int(valor) for valor in request.POST.getlist('variante_id')]
    except (TypeError, ValueError):
        return HttpResponseBadRequest('El outfit recomendado contiene tallas inválidas.')

    if len(variantes_ids) != 2 or len(set(variantes_ids)) != 2 or any(valor < 1 for valor in variantes_ids):
        return HttpResponseBadRequest('El outfit recomendado debe contener dos prendas distintas.')

    filtros_recomendador = request.POST.get('filtros_recomendador', '')
    destino_recomendador = reverse('recomendador')
    if filtros_recomendador:
        destino_recomendador = f'{destino_recomendador}?{filtros_recomendador}'

    variantes_encontradas = VarianteProducto.objects.select_related('producto').filter(id__in=variantes_ids)
    variantes_por_id = {variante.id: variante for variante in variantes_encontradas}
    if len(variantes_por_id) != len(variantes_ids):
        return HttpResponseBadRequest('Una prenda recomendada ya no está disponible.')
    variantes = [variantes_por_id[variante_id] for variante_id in variantes_ids]

    carrito_original, carrito_expirado, _ = _obtener_carrito_vigente(request)
    if carrito_original:
        try:
            carrito, _, _ = _normalizar_carrito(carrito_original)
        except CarritoInvalido:
            return HttpResponseBadRequest(
                'El carrito contiene datos inválidos. Revisa sus productos antes de agregar el outfit.'
            )
    else:
        carrito = {}

    sin_stock = []
    for variante in variantes:
        item_id = f'{variante.producto_id}_{variante.id}'
        cantidad_existente = carrito.get(item_id, {}).get('cantidad', 0)
        if cantidad_existente + 1 > variante.stock_disponible:
            sin_stock.append(f'{variante.producto.nombre} en talla {variante.talla}')
    if sin_stock:
        messages.error(
            request,
            f"No agregamos el outfit porque ya no hay stock suficiente de {', '.join(sin_stock)}.",
        )
        return redirect(destino_recomendador)

    for variante in variantes:
        producto = variante.producto
        item_id = f'{producto.id}_{variante.id}'
        cantidad_existente = carrito.get(item_id, {}).get('cantidad', 0)
        carrito[item_id] = {
            'producto_id': producto.id,
            'variante_id': variante.id,
            'nombre': producto.nombre,
            'talla': variante.talla,
            'precio': producto.precio,
            'cantidad': cantidad_existente + 1,
            'imagen_url': producto.imagen.url if producto.imagen else '',
        }

    try:
        carrito, _, _ = _normalizar_carrito(carrito)
    except CarritoInvalido as error:
        messages.error(request, f'No agregamos el outfit: {error}')
        return redirect(destino_recomendador)

    _guardar_carrito(request, carrito, reiniciar_expiracion=True)
    if carrito_expirado:
        messages.info(request, 'Tu carrito anterior había vencido. Agregamos este outfit a un nuevo carrito.')
    messages.success(request, 'Agregamos las dos prendas recomendadas a tu carrito.')
    return redirect('ver_carrito')


def ver_carrito(request):
    _liberar_reservas_expiradas()
    carrito_original, carrito_expirado, expiracion = _obtener_carrito_vigente(request)
    if not carrito_original:
        return render(request, 'carrito.html', {
            'carrito': {}, 'total': 0, 'carrito_expirado': carrito_expirado,
            'carrito_duracion_minutos': CARRITO_DURACION_MINUTOS,
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
        'carrito_duracion_minutos': CARRITO_DURACION_MINUTOS,
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
        messages.info(request, 'Tu carrito expiró después de 15 minutos sin cambios.')
        return redirect('ver_carrito')
    try:
        carrito, items, total = _normalizar_carrito(carrito_original)
    except CarritoInvalido:
        _guardar_carrito(request, {})
        return redirect('ver_carrito')
    _guardar_carrito(request, carrito)

    cliente_sesion = _perfil_cliente(request)
    datos_iniciales = {}
    if cliente_sesion:
        datos_iniciales = {
            'rut': cliente_sesion.rut,
            'nombre': cliente_sesion.nombre,
            'email': cliente_sesion.email,
            'telefono': cliente_sesion.telefono,
            'direccion': cliente_sesion.direccion,
        }
    form = CheckoutForm(request.POST or None, initial=datos_iniciales)
    if cliente_sesion:
        for campo in ('rut', 'nombre', 'email', 'telefono'):
            form.fields[campo].widget.attrs['readonly'] = 'readonly'
    tipo_entrega = request.POST.get('tipo_entrega') if request.method == 'POST' else None
    estacion_metro = request.POST.get('estacion_metro') if request.method == 'POST' else None
    tarifa_metro = request.POST.get('tarifa_metro') if request.method == 'POST' else None
    try:
        entrega_previsualizada = (
            informacion_entrega(tipo_entrega, estacion_metro, tarifa_metro)
            if tipo_entrega in OPCIONES_ENTREGA
            and (tipo_entrega != 'Metro' or (estacion_metro in ESTACIONES_METRO and tarifa_metro))
            else None
        )
    except ValueError:
        entrega_previsualizada = None
    if request.method == 'POST' and form.is_valid() and cliente_sesion:
        campos_protegidos = {
            'rut': cliente_sesion.rut,
            'nombre': cliente_sesion.nombre,
            'email': cliente_sesion.email or '',
            'telefono': cliente_sesion.telefono or '',
        }
        for campo, valor_registrado in campos_protegidos.items():
            if form.cleaned_data[campo] != valor_registrado:
                form.add_error(
                    campo,
                    'Este dato está protegido en tu cuenta. Solicita una rectificación si necesitas cambiarlo.',
                )

    if request.method == 'POST' and form.is_valid():
        entrega = informacion_entrega(
            form.cleaned_data['tipo_entrega'],
            form.cleaned_data['estacion_metro'],
            form.cleaned_data['tarifa_metro'],
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
        'tarifas_metro': tarifas_metro_para_checkout(),
        'carrito_expira_en_timestamp': expiracion or request.session.get('carrito_expira_en'),
        'carrito_duracion_minutos': CARRITO_DURACION_MINUTOS,
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


def terminos_condiciones(request):
    return render(request, 'terminos_condiciones.html')


def politica_privacidad(request):
    return render(request, 'politica_privacidad.html')


def iniciar_sesion_administracion(request):
    # El acceso administrativo admite únicamente cuentas marcadas como staff
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect(_destino_seguro(request, reverse('dashboard')))
        return render(request, '404.html', status=404)

    error = None
    if request.method == 'POST':
        usuario = authenticate(
            request,
            username=request.POST.get('username', '').strip(),
            password=request.POST.get('password', ''),
        )
        if usuario and usuario.is_staff:
            login(request, usuario)
            return redirect(_destino_seguro(request, reverse('dashboard')))
        error = 'Usuario o contraseña incorrectos.'

    return render(request, 'login.html', {'error': error})


@staff_member_required(login_url='login')
@require_POST
def actualizar_estado_pedido(request):
    pedido = get_object_or_404(Pedido, id=request.POST.get('pedido_id'))
    nuevo_estado = request.POST.get('estado')
    if pedido.estado != 'Entregado' and nuevo_estado in dict(Pedido.ESTADOS) and nuevo_estado != 'Pendiente':
        pedido.estado = nuevo_estado
        campos_actualizados = ['estado']
        if nuevo_estado == 'Entregado':
            pedido.fecha_entregado = timezone.now()
            campos_actualizados.append('fecha_entregado')
        pedido.save(update_fields=campos_actualizados)
    return redirect('dashboard')


@staff_member_required(login_url='login')
@require_POST
def responder_solicitud_soporte(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudPrivacidad.objects.select_related('cliente'), id=solicitud_id)
    form = RespuestaSoporteForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'No se envió la respuesta. Escribe al menos 10 caracteres para la clienta.')
        return redirect('dashboard')

    solicitud.respuesta = form.cleaned_data['respuesta']
    solicitud.estado = 'RESPONDIDA'
    solicitud.respondido_en = timezone.now()
    solicitud.respondido_por = request.user
    solicitud.save(update_fields=['respuesta', 'estado', 'respondido_en', 'respondido_por'])

    correo_cliente = (solicitud.cliente.email or '').strip() if solicitud.cliente else ''
    if correo_cliente:
        mensaje = (
            'Hola,\n\n'
            'Respondimos tu consulta de soporte en LogisFlow.\n\n'
            f'Respuesta:\n{solicitud.respuesta}\n\n'
            f'Código de consulta: {solicitud.codigo_consulta}\n'
            'También puedes revisar esta respuesta desde Mi cuenta o, si compraste como invitada, '
            'en Ayuda y soporte usando tu RUT y código de consulta.\n'
        )
        try:
            send_mail(
                subject=f'[LogisFlow] Respuesta a tu consulta {solicitud.codigo_consulta}',
                message=mensaje,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[correo_cliente],
                fail_silently=False,
            )
        except Exception:
            logger.exception('No fue posible enviar la respuesta de soporte #%s.', solicitud.pk)
            messages.warning(
                request,
                'La respuesta quedó registrada, pero no se pudo enviar el correo. La clienta podrá verla con su código de consulta.',
            )
        else:
            messages.success(request, 'Respuesta enviada. La clienta fue notificada por correo y puede revisarla en la tienda.')
    else:
        messages.success(request, 'Respuesta registrada. La clienta puede revisarla con su código de consulta.')
    return redirect('dashboard')


@staff_member_required(login_url='login')
@require_POST
def reportar_incidente_tecnico(request):
    form = IncidenteTecnicoForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'No se envió el incidente. Revisa el asunto y describe el problema con más detalle.')
        return redirect('dashboard')

    incidente = IncidenteTecnico.objects.create(
        asunto=form.cleaned_data['asunto'],
        descripcion=form.cleaned_data['descripcion'],
        reportado_por=request.user,
    )
    destinatarios = _destinatarios_incidente_tecnico()
    if not destinatarios:
        messages.warning(
            request,
            f'El incidente #{incidente.pk} quedó registrado, pero no hay correos técnicos configurados para enviarlo.',
        )
        return redirect('dashboard')

    mensaje = (
        f'Ticket de incidente técnico #{incidente.pk}\n\n'
        f'Asunto: {incidente.asunto}\n'
        f'Reportado por: {request.user.get_username()}\n'
        f'Fecha: {timezone.localtime(incidente.creado_en).strftime("%d/%m/%Y %H:%M")}\n\n'
        f'Descripción:\n{incidente.descripcion}\n'
    )
    try:
        send_mail(
            subject=f'[LogisFlow] Incidente técnico #{incidente.pk}: {incidente.asunto}',
            message=mensaje,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=destinatarios,
            fail_silently=False,
        )
    except Exception:
        logger.exception('No fue posible enviar el incidente técnico #%s.', incidente.pk)
        messages.warning(
            request,
            f'El incidente #{incidente.pk} quedó registrado, pero el correo no pudo enviarse. Intenta nuevamente o contáctanos por el canal acordado.',
        )
    else:
        incidente.estado = 'ENVIADO'
        incidente.correo_enviado_en = timezone.now()
        incidente.save(update_fields=['estado', 'correo_enviado_en'])
        messages.success(request, f'Incidente #{incidente.pk} enviado al equipo técnico.')
    return redirect('dashboard')


@staff_member_required(login_url='login')
def dashboard(request):
    pedidos = list(Pedido.objects.exclude(estado='Pendiente').select_related('cliente').order_by('-fecha'))
    _preparar_indicadores_sla(pedidos)
    abandonados = PagoPendiente.objects.filter(estado='PENDIENTE').order_by('-creado_en')
    detalles = DetallePedido.objects.filter(pedido__in=pedidos)
    ingresos_totales = (
        sum(detalle.precio_unitario * detalle.cantidad for detalle in detalles)
        + sum(pedido.costo_despacho for pedido in pedidos)
    )
    pedidos_pagados = len(pedidos)
    context = {
        'pedidos': pedidos, 'abandonados': abandonados, 'productos': Producto.objects.all().order_by('nombre'),
        'estados': Pedido.ESTADOS, 'total_pedidos': pedidos_pagados, 'ingresos_totales': ingresos_totales,
        'stock_critico': VarianteProducto.objects.filter(stock__lte=F('stock_reservado') + 3).count(), 'pagos_sin_confirmar': abandonados.count(),
        'ticket_promedio': int(ingresos_totales / pedidos_pagados) if pedidos_pagados else 0,
        'clientes_totales': Cliente.objects.count(),
        'form_incidente_tecnico': IncidenteTecnicoForm(),
        'solicitudes_privacidad': SolicitudPrivacidad.objects.select_related('cliente').filter(
            estado__in=['PENDIENTE', 'EN_REVISION'],
        ),
    }
    return render(request, 'dashboard.html', context)


@staff_member_required(login_url='login')
@require_POST
def guardar_producto(request):
    producto_id = request.POST.get('id')
    producto = get_object_or_404(Producto, id=producto_id) if producto_id else None
    form = ProductoForm(request.POST, request.FILES, instance=producto)
    if not form.is_valid():
        messages.error(request, 'No se guardó el producto: corrige los datos ingresados.')
        return redirect('dashboard')

    tallas_iniciales = []
    if not producto:
        tallas_seleccionadas = request.POST.getlist('tallas')
        tallas_validas = {codigo for codigo, _ in VarianteProducto.TALLAS_CHOICES}
        if len(tallas_seleccionadas) != len(set(tallas_seleccionadas)) or any(
            talla not in tallas_validas for talla in tallas_seleccionadas
        ):
            messages.error(request, 'No se guardó el producto: las tallas seleccionadas no son válidas.')
            return redirect('dashboard')
        try:
            for talla in tallas_seleccionadas:
                stock = int(request.POST.get(f'stock_{talla}', ''))
                if stock < 0 or stock > 100000:
                    raise ValueError
                tallas_iniciales.append((talla, stock))
        except (TypeError, ValueError):
            messages.error(request, 'No se guardó el producto: revisa el stock inicial de cada talla seleccionada.')
            return redirect('dashboard')

    with transaction.atomic():
        producto_guardado = form.save()
        for talla, stock in tallas_iniciales:
            VarianteProducto.objects.create(producto=producto_guardado, talla=talla, stock=stock)
    if tallas_iniciales:
        messages.success(request, 'Producto y tallas iniciales guardados correctamente.')
    else:
        messages.success(request, 'Producto guardado correctamente.')
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
    writer.writerow([
        'N° Ticket', 'Código seguimiento', 'Fecha', 'Cliente', 'RUT', 'Estado',
        'Modo Entrega', 'Total pagado en Mercado Pago ($)', 'Flete externo', 'Límite SLA', 'Fecha de entrega', 'Resultado SLA',
    ])
    pedidos = list(Pedido.objects.select_related('cliente').all().order_by('-fecha'))
    _preparar_indicadores_sla(pedidos)
    for pedido in pedidos:
        total = (
            sum(detalle.precio_unitario * detalle.cantidad for detalle in DetallePedido.objects.filter(pedido=pedido))
            + pedido.costo_despacho
        )
        writer.writerow([
            pedido.id, pedido.codigo_seguimiento, pedido.fecha.strftime('%d/%m/%Y %H:%M'), pedido.cliente.nombre,
            pedido.cliente.rut, pedido.estado, pedido.get_tipo_entrega_display(), total,
            (
                'Por pagar a Starken' if pedido.tipo_entrega == 'Delivery' and not pedido.costo_despacho
                else 'Incluido en Mercado Pago' if pedido.costo_despacho else 'No aplica'
            ),
            pedido.sla_limite or 'No aplica',
            pedido.fecha_entregado.strftime('%d/%m/%Y %H:%M') if pedido.fecha_entregado else 'Sin registro',
            pedido.sla_etiqueta,
        ])
    return response


@csrf_exempt
@require_POST
def webhook_mercadopago(request):
    if not _firma_webhook_mercadopago_valida(request):
        return HttpResponse(status=401)
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
