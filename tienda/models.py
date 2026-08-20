from django.db import models

class Producto(models.Model):
    CATEGORIAS = [
        ('SUPERIOR', 'Prenda Superior (Poleras, Blusas)'),
        ('INFERIOR', 'Prenda Inferior (Pantalones, Faldas)'),
        ('CALZADO', 'Zapatillas, Zapatos'),
        ('ACCESORIO', 'Gorros, Bolsos, Lentes'),
    ]
    COLORES = [
        ('NEGRO', 'Negro'), ('BLANCO', 'Blanco'), ('JEANS', 'Denim / Jeans'),
        ('ROJO', 'Rojo'), ('AZUL', 'Azul'), ('VERDE', 'Verde'), 
        ('BEIGE', 'Beige/Café'), ('OTRO', 'Otro / Estampado')
    ]
    ESTILOS = [
        ('CASUAL', 'Casual / Diario'),
        ('FORMAL', 'Formal / Oficina'),
        ('FIESTA', 'Noche / Fiesta'),
        ('SPORT', 'Deportivo / Comodidad')
    ]
    TEMPORADAS = [
        ('VERANO', 'Primavera / Verano'),
        ('INVIERNO', 'Otoño / Invierno'),
        ('ATEMPORAL', 'Atemporal (Todo el año)')
    ]

    sku = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    precio = models.IntegerField()
    imagen = models.ImageField(upload_to='productos/', null=True, blank=True)
    
    # Campos del motor heuristico
    categoria = models.CharField(max_length=15, choices=CATEGORIAS, default='SUPERIOR')
    color_base = models.CharField(max_length=15, choices=COLORES, default='NEGRO')
    estilo = models.CharField(max_length=15, choices=ESTILOS, default='CASUAL')
    temporada = models.CharField(max_length=15, choices=TEMPORADAS, default='ATEMPORAL')

    def __str__(self):
        return self.nombre

    def stock_total(self):
        return sum(variante.stock for variante in self.variantes.all())


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
    variante = models.ForeignKey(VarianteProducto, on_delete=models.CASCADE, null=True) 
    cantidad = models.IntegerField(default=1)
    precio_unitario = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.cantidad} x {self.variante.producto.nombre} (Talla {self.variante.talla})"