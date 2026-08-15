from django.contrib import admin
from .models import Cliente, Producto, Pedido, DetallePedido

# Aquí le decimos a Django que muestre estas tablas en el panel web
admin.site.register(Cliente)
admin.site.register(Producto)
admin.site.register(Pedido)
admin.site.register(DetallePedido)