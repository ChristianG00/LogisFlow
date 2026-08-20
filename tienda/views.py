import csv
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from .models import Producto, VarianteProducto, Cliente, Pedido, DetallePedido
from .forms import CheckoutForm
import mercadopago
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

# Vistas públicas
def catalogo(request):
    query = request.GET.get('q') 
    if query:
        productos = Producto.objects.filter(nombre__icontains=query)
    else:
        productos = Producto.objects.all()
    return render(request, 'index.html', {'productos': productos, 'query': query})

def producto_detalle(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)
    return render(request, 'detalle.html', {'producto': producto})

# Carrito de compras
def agregar_carrito(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)
    carrito = request.session.get('carrito', {})
    
    if request.method == 'POST':
        cantidad_agregada = int(request.POST.get('cantidad', 1))
        talla_id = request.POST.get('variante_id')
        
        if not talla_id:
            return redirect('producto_detalle', producto_id=producto.id)
            
        variante = get_object_or_404(VarianteProducto, id=talla_id)
        item_id = f"{producto.id}_{variante.id}"

        if item_id in carrito:
            nueva_cantidad = carrito[item_id]['cantidad'] + cantidad_agregada
            if nueva_cantidad <= variante.stock:
                carrito[item_id]['cantidad'] = nueva_cantidad
            else:
                carrito[item_id]['cantidad'] = variante.stock
        else:
            cantidad_final = min(cantidad_agregada, variante.stock)
            carrito[item_id] = {
                'producto_id': producto.id,
                'variante_id': variante.id,
                'nombre': producto.nombre,
                'talla': variante.talla,
                'precio': producto.precio,
                'cantidad': cantidad_final,
                'sku': producto.sku,
                'imagen_url': producto.imagen.url if producto.imagen else ''
            }
        
        request.session['carrito'] = carrito
        return redirect('ver_carrito')
    return redirect('catalogo')

def ver_carrito(request):
    carrito = request.session.get('carrito', {})
    total = sum(item['precio'] * item['cantidad'] for item in carrito.values())
    return render(request, 'carrito.html', {'carrito': carrito, 'total': total})

def sumar_carrito(request, item_id):
    carrito = request.session.get('carrito', {})
    item_id = str(item_id)
    if item_id in carrito:
        variante_id = carrito[item_id]['variante_id']
        variante = get_object_or_404(VarianteProducto, id=variante_id)
        if carrito[item_id]['cantidad'] < variante.stock:
            carrito[item_id]['cantidad'] += 1
        request.session['carrito'] = carrito
    return redirect('ver_carrito')

def restar_carrito(request, item_id):
    carrito = request.session.get('carrito', {})
    item_id = str(item_id)
    if item_id in carrito:
        if carrito[item_id]['cantidad'] > 1:
            carrito[item_id]['cantidad'] -= 1
        else:
            del carrito[item_id]
        request.session['carrito'] = carrito
    return redirect('ver_carrito')

def eliminar_del_carrito(request, item_id):
    carrito = request.session.get('carrito', {})
    item_id = str(item_id)
    if item_id in carrito:
        del carrito[item_id]
        request.session['carrito'] = carrito
    return redirect('ver_carrito')

# Checkout, exito y seguimiento
def crear_pedido(request):
    carrito = request.session.get('carrito', {})
    if not carrito: return redirect('catalogo')
    total = sum(item['precio'] * item['cantidad'] for item in carrito.values())
    
    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            cliente, created = Cliente.objects.get_or_create(
                rut=form.cleaned_data['rut'],
                defaults={'nombre': form.cleaned_data['nombre'], 'telefono': form.cleaned_data['telefono'], 'direccion': form.cleaned_data['direccion']}
            )
            pedido = Pedido.objects.create(cliente=cliente, tipo_entrega=form.cleaned_data['tipo_entrega'])
            
            for key, item in carrito.items():
                variante_obj = VarianteProducto.objects.get(id=item['variante_id'])
                DetallePedido.objects.create(pedido=pedido, variante=variante_obj, cantidad=item['cantidad'], precio_unitario=item['precio'])
            
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
            items_mp = [{"title": item['nombre'], "quantity": item['cantidad'], "unit_price": float(item['precio']), "currency_id": "CLP"} for key, item in carrito.items()]
            preference_data = {
                "items": items_mp, "external_reference": str(pedido.id), 
                "back_urls": {"success": f"https://logisflow.alwaysdata.net/exito/{pedido.id}/", "failure": "https://logisflow.alwaysdata.net/checkout/", "pending": f"https://logisflow.alwaysdata.net/exito/{pedido.id}/"},
                "auto_return": "approved", "notification_url": "https://logisflow.alwaysdata.net/webhook/"
            }
            preference_response = sdk.preference().create(preference_data)
            return redirect(preference_response["response"]['init_point'])
    else:
        form = CheckoutForm()
    return render(request, 'checkout.html', {'form': form, 'carrito': carrito, 'total': total})

def pedido_exitoso(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id)
    if 'carrito' in request.session: request.session['carrito'] = {}
    return render(request, 'exito.html', {'pedido': pedido})

def seguimiento(request):
    pedido, error = None, None
    if request.method == 'POST':
        rut = request.POST.get('rut')
        try:
            cliente = Cliente.objects.get(rut=rut)
            pedido = Pedido.objects.filter(cliente=cliente).latest('fecha')
        except Cliente.DoesNotExist: error = "No hay pedidos registrados con ese RUT."
        except Pedido.DoesNotExist: error = "Este cliente existe pero no tiene pedidos registrados."
    return render(request, 'seguimiento.html', {'pedido': pedido, 'error': error})

# Vista dueña
@staff_member_required(login_url='login')
def dashboard(request):
    if request.method == 'POST':
        pedido_id, nuevo_estado = request.POST.get('pedido_id'), request.POST.get('estado')
        pedido = Pedido.objects.get(id=pedido_id)
        if pedido.estado != 'Entregado': 
            pedido.estado = nuevo_estado
            pedido.save()
        return redirect('dashboard')

    pedidos = Pedido.objects.exclude(estado='Pendiente').order_by('-fecha')
    abandonados = Pedido.objects.filter(estado='Pendiente').order_by('-fecha')
    
    ingresos_totales = sum(d.precio_unitario * d.cantidad for p in pedidos for d in DetallePedido.objects.filter(pedido=p))
    pedidos_pagados = pedidos.count()
    ticket_promedio = int(ingresos_totales / pedidos_pagados) if pedidos_pagados > 0 else 0

    context = {
        'pedidos': pedidos, 'abandonados': abandonados, 'productos': Producto.objects.all().order_by('nombre'),
        'estados': Pedido.ESTADOS, 'total_pedidos': pedidos_pagados, 'ingresos_totales': ingresos_totales,
        'stock_critico': VarianteProducto.objects.filter(stock__lte=3).count(), 'pedidos_pendientes': abandonados.count(),
        'ticket_promedio': ticket_promedio, 'clientes_totales': Cliente.objects.count(),
    }
    return render(request, 'dashboard.html', context)

# Crud inventario
@staff_member_required(login_url='login')
def guardar_producto(request):
    if request.method == 'POST':
        prod_id = request.POST.get('id')
        data = {
            'sku': request.POST.get('sku'), 
            'nombre': request.POST.get('nombre'), 
            'precio': request.POST.get('precio'),
            'categoria': request.POST.get('categoria', 'SUPERIOR'), 
            'color_base': request.POST.get('color_base', 'NEGRO'), 
            'estilo': request.POST.get('estilo', 'CASUAL'), 
            'temporada': request.POST.get('temporada', 'ATEMPORAL')
        }
        imagen = request.FILES.get('imagen')
        
        if prod_id: 
            Producto.objects.filter(id=prod_id).update(**data)
            if imagen: 
                prod = Producto.objects.get(id=prod_id)
                prod.imagen = imagen
                prod.save()
        else: 
            Producto.objects.create(**data, imagen=imagen)
    return redirect('dashboard')

@staff_member_required(login_url='login')
def guardar_talla(request):
    if request.method == 'POST':
        VarianteProducto.objects.update_or_create(
            producto_id=request.POST.get('producto_id'), talla=request.POST.get('talla'),
            defaults={'stock': request.POST.get('stock')}
        )
    return redirect('dashboard')

@staff_member_required(login_url='login')
def eliminar_producto(request, producto_id):
    Producto.objects.filter(id=producto_id).delete()
    return redirect('dashboard')

@staff_member_required(login_url='login')
def exportar_excel(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="Reporte_Ventas.csv"'
    response.write(u'\ufeff'.encode('utf8')) 
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['N° Ticket', 'Fecha', 'Cliente', 'RUT', 'Estado', 'Modo Entrega', 'Total Pagado ($)'])
    
    for p in Pedido.objects.all().order_by('-fecha'):
        total = sum(d.precio_unitario * d.cantidad for d in DetallePedido.objects.filter(pedido=p))
        writer.writerow([p.id, p.fecha.strftime("%d/%m/%Y %H:%M"), p.cliente.nombre, p.cliente.rut, p.estado, p.tipo_entrega, total])
    return response

# Webhook Mercado Pago
@csrf_exempt
def webhook_mercadopago(request):
    topic = request.GET.get("topic") or request.GET.get("type")
    payment_id = request.GET.get("id") or request.GET.get("data.id")
    if topic == "payment" and payment_id:
        try:
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
            data = sdk.payment().get(payment_id).get("response", {})
            if data.get("status") == "approved" and data.get("external_reference"):
                pedido = Pedido.objects.get(id=data.get("external_reference"))
                if pedido.estado == 'Pendiente':
                    pedido.estado = 'Preparando' 
                    pedido.save()
                    for detalle in DetallePedido.objects.filter(pedido=pedido):
                        detalle.variante.stock -= detalle.cantidad
                        detalle.variante.save()
        except Exception as e: print(f"Error webhook: {e}")
    return HttpResponse(status=200)