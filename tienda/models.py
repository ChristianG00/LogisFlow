import re
import secrets
import uuid

from django.core.validators import MinValueValidator
from django.db import models


def generar_codigo_seguimiento():
    """Código público no secuencial para que el cliente rastree un pedido."""
    return f'LF-{secrets.token_hex(5).upper()}'

class Producto(models.Model):
    CATEGORIAS = [
        ('SUPERIOR', 'Tops y poleras (general)'),
        ('POLERAS', 'Poleras y tops'),
        ('BLUSAS', 'Blusas'),
        ('INFERIOR', 'Pantalones y faldas (general)'),
        ('PANTALONES', 'Pantalones y jeans'),
        ('FALDAS', 'Faldas'),
        ('VESTIDOS', 'Vestidos'),
        ('CHAQUETAS', 'Chaquetas y abrigos'),
        ('CALZADO', 'Calzado'),
        ('ACCESORIO', 'Accesorios'),
    ]
    COLORES = [
        ('NEGRO', 'Negro'), ('BLANCO', 'Blanco'), ('BEIGE', 'Beige / Café'),
        ('JEANS', 'Denim / Jeans'), ('ROSADO', 'Rosado'), ('FUCSIA', 'Fucsia'),
        ('LILA', 'Lila'), ('MORADO', 'Morado'), ('CORAL', 'Coral'),
        ('BURDEO', 'Burdeo'), ('ROJO', 'Rojo'), ('AZUL', 'Azul'),
        ('VERDE', 'Verde'), ('OTRO', 'Otro / Estampado'),
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

    nombre = models.CharField(max_length=100)
    precio = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    imagen = models.ImageField(upload_to='productos/', null=True, blank=True)
    
    # Campos del motor heuristico
    categoria = models.CharField(max_length=15, choices=CATEGORIAS, default='SUPERIOR')
    color_base = models.CharField(max_length=15, choices=COLORES, default='NEGRO')
    estilo = models.CharField(max_length=15, choices=ESTILOS, default='CASUAL')
    temporada = models.CharField(max_length=15, choices=TEMPORADAS, default='ATEMPORAL')

    def __str__(self):
        return self.nombre

    def stock_total(self):
        return sum(variante.stock_disponible for variante in self.variantes.all())


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
    stock = models.PositiveIntegerField(default=0)
    stock_reservado = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('producto', 'talla')

    def __str__(self):
        return f"{self.producto.nombre} - Talla {self.talla}"

    @property
    def stock_disponible(self):
        return max(0, self.stock - self.stock_reservado)


class Cliente(models.Model):
    rut = models.CharField(max_length=12, unique=True)
    nombre = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20)
    direccion = models.CharField(max_length=200)

    def __str__(self):
        return self.nombre

    @property
    def telefono_whatsapp(self):
        """Número E.164 sin símbolos, apto para los enlaces wa.me."""
        numero = ''.join(re.findall(r'\d', self.telefono))
        return f'56{numero}' if re.fullmatch(r'9\d{8}', numero) else numero


class Pedido(models.Model):
    ESTADOS = [
        ('Pendiente', 'Pendiente'),
        ('Preparando', 'Preparando'),
        ('En Ruta', 'En Ruta'),
        ('Entregado', 'Entregado'),
    ]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    codigo_seguimiento = models.CharField(
        max_length=13,
        unique=True,
        default=generar_codigo_seguimiento,
        editable=False,
    )
    fecha = models.DateTimeField(auto_now_add=True)
    tipo_entrega = models.CharField(max_length=50)
    estado = models.CharField(max_length=50, choices=ESTADOS, default='Pendiente')
    costo_despacho = models.PositiveIntegerField(default=0)
    plazo_entrega = models.CharField(max_length=150, default='No informado')
    mecanismo_seguimiento = models.CharField(
        max_length=200,
        default='Consulta con tu RUT y código de seguimiento en LogisFlow.',
    )

    TIPOS_ENTREGA = {
        'Retiro': 'Retiro en domicilio del vendedor',
        'Metro': 'Entrega personal en estación de Metro',
        'Delivery': 'Despacho a domicilio por empresa de transporte',
    }

    def __str__(self):
        return f"Pedido #{self.id} - {self.cliente.nombre}"

    def save(self, *args, **kwargs):
        """Evita una colisión extremadamente improbable al crear un código público."""
        if self._state.adding:
            while not self.codigo_seguimiento or type(self).objects.filter(
                codigo_seguimiento=self.codigo_seguimiento,
            ).exists():
                self.codigo_seguimiento = generar_codigo_seguimiento()
        super().save(*args, **kwargs)

    def get_tipo_entrega_display(self):
        """Mantiene una etiqueta legible sin cambiar datos existentes de la BD."""
        return self.TIPOS_ENTREGA.get(self.tipo_entrega, self.tipo_entrega)


class DetallePedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE)
    variante = models.ForeignKey(VarianteProducto, on_delete=models.CASCADE, null=True) 
    cantidad = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    precio_unitario = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    def __str__(self):
        return f"{self.cantidad} x {self.variante.producto.nombre} (Talla {self.variante.talla})"


class PagoPendiente(models.Model):
    """Datos de un intento de pago. No representa una venta ni un pedido."""

    ESTADOS = [
        ('PENDIENTE', 'Pendiente de pago'),
        ('CONFIRMADO', 'Pago confirmado'),
        ('SIN_STOCK', 'No se pudo asignar stock'),
        ('EXPIRADO', 'Reserva expirada'),
        ('CANCELADO', 'Pago cancelado'),
    ]

    referencia = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    rut = models.CharField(max_length=12)
    nombre = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20)
    direccion = models.CharField(max_length=200)
    tipo_entrega = models.CharField(max_length=50)
    items = models.JSONField()
    total = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    subtotal_productos = models.PositiveIntegerField(default=0)
    costo_despacho = models.PositiveIntegerField(default=0)
    plazo_entrega = models.CharField(max_length=150, default='No informado')
    mecanismo_seguimiento = models.CharField(
        max_length=200,
        default='Consulta con tu RUT y código de seguimiento en LogisFlow.',
    )
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    reserva_activa = models.BooleanField(default=False)
    reserva_expira_en = models.DateTimeField(null=True, blank=True)
    mercadopago_payment_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    pedido = models.OneToOneField(Pedido, on_delete=models.SET_NULL, null=True, blank=True, related_name='pago')
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Pago {self.referencia} ({self.estado})"

    @property
    def telefono_whatsapp(self):
        numero = ''.join(re.findall(r'\d', self.telefono))
        return f'56{numero}' if re.fullmatch(r'9\d{8}', numero) else numero
