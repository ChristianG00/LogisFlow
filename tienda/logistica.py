# Tarifas y plazos de entrega

# Tarifa adulta Red Movilidad vigente desde el 22 de febrero de 2026
TARIFAS_METRO = {
    'BAJA': {
        'nombre': 'Horario bajo',
        'costo': 735,
        'horario': 'Lunes a viernes: 06:00–06:59 y 20:45–23:00',
    },
    'VALLE': {
        'nombre': 'Horario valle',
        'costo': 815,
        'horario': 'Lunes a viernes: 09:00–17:59 y 20:00–20:44 · sábados, domingos y festivos',
    },
    'PUNTA': {
        'nombre': 'Horario punta',
        'costo': 895,
        'horario': 'Lunes a viernes: 07:00–08:59 y 18:00–19:59',
    },
}
TARIFAS_METRO_CHOICES = [
    (codigo, f"{datos['nombre']} · ${datos['costo']} · {datos['horario']}")
    for codigo, datos in TARIFAS_METRO.items()
]

# Entrega en Metro: Universidad de Chile a Tobalaba
TRAMO_METRO = (
    'Universidad de Chile',
    'Santa Lucía',
    'Universidad Católica',
    'Baquedano',
    'Salvador',
    'Manuel Montt',
    'Pedro de Valdivia',
    'Los Leones',
    'Tobalaba',
)

ESTACIONES_METRO = {
    f'L1_{numero:02d}': {
        'nombre': nombre,
        'linea': 'Línea 1',
    }
    for numero, nombre in enumerate(TRAMO_METRO, start=1)
}
ESTACIONES_METRO_CHOICES = [
    (
        'Línea 1 · Universidad de Chile a Tobalaba',
        [
            (
                f'L1_{numero:02d}',
                nombre,
            )
            for numero, nombre in enumerate(TRAMO_METRO, start=1)
        ],
    ),
]

OPCIONES_ENTREGA = {
    'Retiro': {
        'nombre': 'Retiro en domicilio del vendedor',
        'costo': 0,
        'plazo': 'Disponible para coordinar tras la confirmación del pago.',
        'seguimiento': 'Coordinación y actualización por WhatsApp. Consulta el estado con tu RUT y código de seguimiento en LogisFlow.',
    },
    'Metro': {
        'nombre': 'Entrega personal en estación de Metro',
        'costo': 0,
        'tarifa': 'Pasaje Metro adulto según horario: $735 bajo · $815 valle · $895 punta',
        'plazo': '1 a 2 días hábiles tras la confirmación del pago.',
        'seguimiento': 'La vendedora coordina la estación y hora por WhatsApp; el estado también se consulta con tu RUT y código de seguimiento en LogisFlow.',
    },
    'Delivery': {
        'nombre': 'Despacho a domicilio por Starken',
        'costo': 0,
        'por_pagar': True,
        'tarifa': 'Flete Starken por pagar al recibir el despacho. No se suma a Mercado Pago.',
        'plazo': '2 a 5 días hábiles tras la confirmación del pago.',
        'seguimiento': 'Starken calcula el flete según destino, peso y volumen. Lo pagas al recibir el despacho. Recibirás actualizaciones por WhatsApp y podrás consultar el estado con RUT y código.',
    },
}


def datos_estacion_metro(codigo):
    try:
        return ESTACIONES_METRO[codigo].copy()
    except KeyError:
        raise ValueError('La estación de Metro seleccionada no es válida.')


def datos_tarifa_metro(codigo):
    try:
        return TARIFAS_METRO[codigo].copy()
    except KeyError:
        raise ValueError('Selecciona una franja horaria de Metro válida.')


def estaciones_metro_para_checkout():
    # Datos usados por el checkout
    return {codigo: datos.copy() for codigo, datos in ESTACIONES_METRO.items()}


def tarifas_metro_para_checkout():
    return {codigo: datos.copy() for codigo, datos in TARIFAS_METRO.items()}


def informacion_entrega(tipo_entrega, estacion_metro=None, tarifa_metro=None):
    # Copia los datos de entrega para guardarlos en el pedido
    entrega = OPCIONES_ENTREGA[tipo_entrega].copy()
    if tipo_entrega == 'Metro':
        estacion = datos_estacion_metro(estacion_metro)
        tarifa = datos_tarifa_metro(tarifa_metro)
        entrega['nombre'] = f"Entrega personal en {estacion['nombre']} ({estacion['linea']})"
        entrega['costo'] = tarifa['costo']
        entrega['tarifa'] = f"{tarifa['nombre']}: ${tarifa['costo']} · {tarifa['horario']}"
        entrega['plazo'] = '1 a 2 días hábiles tras la confirmación del pago.'
        entrega['seguimiento'] = (
            f"La vendedora coordina estación y hora en {estacion['nombre']} por WhatsApp. "
            'el estado también se consulta con tu RUT y código de seguimiento en LogisFlow.'
        )
    return entrega
