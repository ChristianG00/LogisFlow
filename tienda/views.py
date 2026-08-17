import csv
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from .models import Producto, VarianteProducto, Cliente, Pedido, DetallePedido
from .forms import CheckoutForm
import mercadopago
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt



# VISTAS DE LA CLIENTA (CATALOGO Y DETALLES)

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


# LOGICA DEL CARRITO DE COMPRAS (CON TALLAS)

def agregar_carrito(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)
    carrito = request.session.get('carrito', {})
    
    if request.method == 'POST':
        cantidad_agregada = int(request.POST.get('cantidad', 1))
        talla_id = request.POST.get('variante_id')
        
        # Si alguien intenta forzar el form sin seleccionar talla, lo devuelve
        if not talla_id:
            return redirect('producto_detalle', producto_id=producto.id)
            
        variante = get_object_or_404(VarianteProducto, id=talla_id)
        
        # Llave única: (Producto 5, Variante 12)
        id_str = f"{producto.id}_{variante.id}"

        if id_str in carrito:
            nueva_cantidad = carrito[id_str]['cantidad'] + cantidad_agregada
            if nueva_cantidad <= variante.stock:
                carrito[id_str]['cantidad'] = nueva_cantidad
            else:
                carrito[id_str]['cantidad'] = variante.stock
        else:
            cantidad_final = min(cantidad_agregada, variante.stock)
            carrito[id_str] = {
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

def sumar_carrito(request, producto_id):
    # producto_id aquí recibe la llave compuesta (ej: "5_12")
    carrito = request.session.get('carrito', {})
    id_str = str(producto_id)

    if id_str in carrito:
        variante_id = carrito[id_str]['variante_id']
        variante = get_object_or_404(VarianteProducto, id=variante_id)
        if carrito[id_str]['cantidad'] < variante.stock:
            carrito[id_str]['cantidad'] += 1
        request.session['carrito'] = carrito
        
    return redirect('ver_carrito')

def restar_carrito(request, producto_id):
    carrito = request.session.get('carrito', {})
    id_str = str(producto_id)

    if id_str in carrito:
        if carrito[id_str]['cantidad'] > 1:
            carrito[id_str]['cantidad'] -= 1
        else:
            del carrito[id_str]
            
        request.session['carrito'] = carrito
        
    return redirect('ver_carrito')

def eliminar_del_carrito(request, producto_id):
    carrito = request.session.get('carrito', {})
    if str(producto_id) in carrito:
        del carrito[str(producto_id)]
        request.session['carrito'] = carrito
    return redirect('ver_carrito')


# CHECKOUT, ÉXITO Y SEGUIMIENTO
def crear_pedido(request):
    carrito = request.session.get('carrito', {})
    
    if not carrito:
        return redirect('catalogo')
        
    total = sum(item['precio'] * item['cantidad'] for item in carrito.values())
    
    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            # 1. Guardamos al cliente
            cliente, created = Cliente.objects.get_or_create(
                rut=form.cleaned_data['rut'],
                defaults={
                    'nombre': form.cleaned_data['nombre'],
                    'telefono': form.cleaned_data['telefono'],
                    'direccion': form.cleaned_data['direccion']
                }
            )
            # 2. Creamos la orden en estado 'Pendiente' por defecto
            pedido = Pedido.objects.create(
                cliente=cliente,
                tipo_entrega=form.cleaned_data['tipo_entrega']
            )
            
            # 3. Recorremos el carrito y creamos el detalle (SIN descontar stock)
            for key, item in carrito.items():
                variante_obj = VarianteProducto.objects.get(id=item['variante_id'])
                DetallePedido.objects.create(
                    pedido=pedido,
                    variante=variante_obj,
                    cantidad=item['cantidad'],
                    precio_unitario=item['precio']
                )
                # ELIMINAMOS la línea que restaba el stock aquí
            
            # MERCADO PAGO
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)

            items_mp = []
            for key, item in carrito.items():
                items_mp.append({
                    "title": item['nombre'],
                    "quantity": item['cantidad'],
                    "unit_price": float(item['precio']),
                    "currency_id": "CLP"
                })

            preference_data = {
                "items": items_mp,
                "external_reference": str(pedido.id), 
                "back_urls": {
                    "success": f"https://logisflow.alwaysdata.net/exito/{pedido.id}/",
                    "failure": "https://logisflow.alwaysdata.net/checkout/",
                    "pending": f"https://logisflow.alwaysdata.net/exito/{pedido.id}/"
                },
                "auto_return": "approved",
                "notification_url": "https://logisflow.alwaysdata.net/webhook/"
            }

            preference_response = sdk.preference().create(preference_data)
            preference = preference_response["response"]

            # ELIMINAMOS el vaciado del carrito aquí. El cliente aún no paga.
            
            return redirect(preference['init_point'])
    else:
        form = CheckoutForm()
        
    return render(request, 'checkout.html', {'form': form, 'carrito': carrito, 'total': total})


def pedido_exitoso(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id)
    
    # ¡Recién aquí vaciamos el carrito porque ya pagó!
    if 'carrito' in request.session:
        request.session['carrito'] = {}
        
    return render(request, 'exito.html', {'pedido': pedido})


def seguimiento(request):
    pedido = None
    error = None
    if request.method == 'POST':
        rut = request.POST.get('rut')
        try:
            cliente = Cliente.objects.get(rut=rut)
            pedido = Pedido.objects.filter(cliente=cliente).latest('fecha')
        except Cliente.DoesNotExist:
            error = "No hay pedidos registrados con ese RUT."
        except Pedido.DoesNotExist:
            error = "Este cliente existe pero no tiene pedidos registrados."
            
    return render(request, 'seguimiento.html', {'pedido': pedido, 'error': error})



# VISTAS DE LA DUEÑA
@staff_member_required(login_url='login')
def dashboard(request):
    if request.method == 'POST':
        pedido_id = request.POST.get('pedido_id')
        nuevo_estado = request.POST.get('estado')
        pedido = Pedido.objects.get(id=pedido_id)
        if pedido.estado != 'Entregado': 
            pedido.estado = nuevo_estado
            pedido.save()
        return redirect('dashboard')

    # CAMBIO AQUÍ: Separamos los pedidos
    # 'pedidos' ahora SOLO tiene los que ya se pagaron (Preparando, En Ruta, Entregado)
    pedidos = Pedido.objects.exclude(estado='Pendiente').order_by('-fecha')
    
    # 'abandonados' tiene solo los que el cliente no pagó
    abandonados = Pedido.objects.filter(estado='Pendiente').order_by('-fecha')

    productos = Producto.objects.all().order_by('nombre')
    clientes = Cliente.objects.count()

    # El KPI de total de pedidos solo debería contar ventas reales
    total_pedidos = pedidos.count() 
    
    # KPI de abandonos
    pedidos_pendientes = abandonados.count()
    
    stock_critico = VarianteProducto.objects.filter(stock__lte=3).count()

    ingresos_totales = 0
    pedidos_pagados = 0
    for p in pedidos: # Recorremos solo los reales
        pedidos_pagados += 1
        detalles = DetallePedido.objects.filter(pedido=p)
        for d in detalles:
            ingresos_totales += (d.precio_unitario * d.cantidad)
                
    ticket_promedio = int(ingresos_totales / pedidos_pagados) if pedidos_pagados > 0 else 0

    # CAMBIO AQUÍ: Agregamos la variable 'abandonados' al context
    context = {
        'pedidos': pedidos,
        'abandonados': abandonados, # <--- NUEVO
        'productos': productos,
        'estados': Pedido.ESTADOS,
        'total_pedidos': total_pedidos,
        'ingresos_totales': ingresos_totales,
        'stock_critico': stock_critico,
        'pedidos_pendientes': pedidos_pendientes,
        'ticket_promedio': ticket_promedio,
        'clientes_totales': clientes,
    }
    return render(request, 'dashboard.html', context)


# --- CRUD INVENTARIO (CON TALLAS) ---

@staff_member_required(login_url='login')
def guardar_producto(request):
    """ Crea o edita la info básica del producto (nombre, precio, foto) """
    if request.method == 'POST':
        prod_id = request.POST.get('id')
        sku = request.POST.get('sku')
        nombre = request.POST.get('nombre')
        precio = request.POST.get('precio')
        imagen = request.FILES.get('imagen')

        if prod_id: 
            producto = Producto.objects.get(id=prod_id)
            producto.sku = sku
            producto.nombre = nombre
            producto.precio = precio
            if imagen: 
                producto.imagen = imagen
            producto.save()
        else: 
            Producto.objects.create(sku=sku, nombre=nombre, precio=precio, imagen=imagen)
            
    return redirect('dashboard')

@staff_member_required(login_url='login')
def guardar_talla(request):
    """ Agrega stock a una talla específica de un producto """
    if request.method == 'POST':
        producto_id = request.POST.get('producto_id')
        talla = request.POST.get('talla')
        stock = request.POST.get('stock')
        
        producto = get_object_or_404(Producto, id=producto_id)
        
        # Busca si esa talla ya existe. Si existe, actualiza el stock, si no, la crea.
        variante, created = VarianteProducto.objects.get_or_create(
            producto=producto, 
            talla=talla,
            defaults={'stock': stock}
        )
        if not created:
            variante.stock = stock
            variante.save()
            
    return redirect('dashboard')

@staff_member_required(login_url='login')
def eliminar_producto(request, producto_id):
    Producto.objects.filter(id=producto_id).delete()
    return redirect('dashboard')


# --- EXPORTAR A EXCEL ---

@staff_member_required(login_url='login')
def exportar_excel(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="Reporte_Ventas_LogisFlow.csv"'
    
    response.write(u'\ufeff'.encode('utf8')) 
    writer = csv.writer(response, delimiter=';')
    
    writer.writerow(['N° Ticket', 'Fecha', 'Cliente', 'RUT', 'Estado', 'Modo Entrega', 'Total Pagado ($)'])
    
    pedidos = Pedido.objects.all().order_by('-fecha')
    for p in pedidos:
        # Actualizado: Ahora calcula el total usando precio_unitario congelado
        total = sum(d.precio_unitario * d.cantidad for d in DetallePedido.objects.filter(pedido=p))
        fecha_str = p.fecha.strftime("%d/%m/%Y %H:%M") 
        writer.writerow([p.id, fecha_str, p.cliente.nombre, p.cliente.rut, p.estado, p.tipo_entrega, total])
        
    return response

@csrf_exempt
def webhook_mercadopago(request):
    topic = request.GET.get("topic") or request.GET.get("type")
    payment_id = request.GET.get("id") or request.GET.get("data.id")

    if topic == "payment" and payment_id:
        try:
            sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
            payment_info = sdk.payment().get(payment_id)
            
            if payment_info["status"] == 200:
                data = payment_info["response"]
                estado_pago = data.get("status")
                pedido_id = data.get("external_reference")

                if estado_pago == "approved" and pedido_id:
                    pedido = Pedido.objects.get(id=pedido_id)
                    
                    if pedido.estado == 'Pendiente':
                        # CORRECCIÓN: El estado exacto del modelo es 'Preparando'
                        pedido.estado = 'Preparando' 
                        pedido.save()
                        
                        # AQUÍ SE DESCUENTA EL STOCK REAL Y DE FORMA DEFINITIVA
                        detalles = DetallePedido.objects.filter(pedido=pedido)
                        for detalle in detalles:
                            variante = detalle.variante
                            variante.stock -= detalle.cantidad
                            variante.save()
                            
        except Exception as e:
            print(f"Error procesando webhook: {e}")
            
    return HttpResponse(status=200)