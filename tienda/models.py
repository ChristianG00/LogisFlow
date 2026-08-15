from django.db import models

class Producto(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    precio = models.IntegerField()
    imagen = models.ImageField(upload_to='productos/', null=True, blank=True)

    def __str__(self):
        return self.nombre

    # Función rápida para sumar todo el stock de sus tallas
    def stock_total(self):
        return sum(variante.stock for variante in self.variantes.all())

# === NUEVA TABLA: Tallas y Stock ===
class VarianteProducto(models.Model):
    TALLAS_CHOICES = [
        ('XS', 'Extra Small'),
        ('S', 'Small'),
        ('M', 'Medium'),
        ('L', 'Large'),
        ('XL', 'Extra Large'),
        ('XXL', 'Doble Extra Large'),
        ('UNICA', 'Talla Única'),
    ]
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='variantes')
    talla = models.CharField(max_length=10, choices=TALLAS_CHOICES)
    stock = models.IntegerField(default=0)

    class Meta:
        # Evita que un mismo producto tenga dos veces la misma talla
        unique_together = ('producto', 'talla')

    def __str__(self):
        return f"{self.producto.nombre} - Talla {self.talla}"

class Cliente(models.Model):
    rut = models.CharField(max_length=12, unique=True)
    nombre = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20)
    direccion = models.CharField(max_length=200)

    def __str__(self):
        return self.nombre

class Pedido(models.Model):
    ESTADOS = [
        ('Pendiente', 'Pendiente'),
        ('Preparando', 'Preparando'),
        ('En Ruta', 'En Ruta'),
        ('Entregado', 'Entregado'),
    ]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True)
    tipo_entrega = models.CharField(max_length=50)
    estado = models.CharField(max_length=50, choices=ESTADOS, default='Pendiente')

    def __str__(self):
        return f"Pedido #{self.id} - {self.cliente.nombre}"

class DetallePedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE)
    # Compramos una variante específica (ej: Polera Roja - Talla M)
    variante = models.ForeignKey(VarianteProducto, on_delete=models.CASCADE, null=True) 
    cantidad = models.IntegerField(default=1)
    precio_unitario = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.cantidad} x {self.variante.producto.nombre} (Talla {self.variante.talla})"