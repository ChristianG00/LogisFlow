# Reglas de despacho visibles y centralizadas para el checkout

TARIFA_METRO_POR_KILOMETRO = 200

# Recorrido habilitado para la entrega personal: Línea 1, desde Universidad de
# Chile hasta Tobalaba cada tramo/kilómetro siguiente añade otros $200.
TRAMO_METRO = (
    ('Universidad de Chile', 1),
    ('Santa Lucía', 2),
    ('Universidad Católica', 3),
    ('Baquedano', 4),
    ('Salvador', 5),
    ('Manuel Montt', 6),
    ('Pedro de Valdivia', 7),
    ('Los Leones', 8),
    ('Tobalaba', 9),
)

ESTACIONES_METRO = {
    f'L1_{numero:02d}': {
        'nombre': nombre,
        'linea': 'Línea 1',
        'kilometros': kilometros,
        'costo': kilometros * TARIFA_METRO_POR_KILOMETRO,
        'es_tramo_inicial': kilometros == 1,
    }
    for numero, (nombre, kilometros) in enumerate(TRAMO_METRO, start=1)
}
ESTACIONES_METRO_CHOICES = [
    (
        'Línea 1 · Universidad de Chile a Tobalaba',
        [
            (
                f'L1_{numero:02d}',
                f'{nombre} · {kilometros} km · ${kilometros * TARIFA_METRO_POR_KILOMETRO}',
            )
            for numero, (nombre, kilometros) in enumerate(TRAMO_METRO, start=1)
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
        'tarifa': '$200 por kilómetro · Universidad de Chile: $200',
        'plazo': '1 a 2 días hábiles tras la confirmación del pago.',
        'seguimiento': 'La vendedora coordina la estación y hora por WhatsApp; el estado también se consulta con tu RUT y código de seguimiento en LogisFlow.',
    },
    'Delivery': {
        'nombre': 'Despacho a domicilio por empresa de transporte',
        'costo': 4990,
        'plazo': '2 a 5 días hábiles tras la confirmación del pago.',
        'seguimiento': 'El despacho se envía mediante courier (por ejemplo, Starken) y recibirás actualizaciones por WhatsApp; también puedes consultar el estado con tu RUT y código de seguimiento en LogisFlow.',
    },
}


def datos_estacion_metro(codigo):
    try:
        return ESTACIONES_METRO[codigo].copy()
    except KeyError:
        raise ValueError('La estación de Metro seleccionada no es válida.')


def estaciones_metro_para_checkout():
    # Datos serializables para que la interfaz muestre estación y tarifa exactas.
    return {codigo: datos.copy() for codigo, datos in ESTACIONES_METRO.items()}


def informacion_entrega(tipo_entrega, estacion_metro=None):
    # Devuelve una copia para que los datos del pedido queden inmutables
    entrega = OPCIONES_ENTREGA[tipo_entrega].copy()
    if tipo_entrega == 'Metro':
        estacion = datos_estacion_metro(estacion_metro)
        entrega['nombre'] = f"Entrega personal en {estacion['nombre']} ({estacion['linea']})"
        entrega['costo'] = estacion['costo']
        entrega['tarifa'] = (
            'Universidad de Chile: $200 (primer tramo cobrable).'
            if estacion['es_tramo_inicial']
            else f"${estacion['costo']} ({estacion['kilometros']} km × $200 desde Universidad de Chile)."
        )
        detalle_tramo = (
            'Universidad de Chile: primer tramo cobrable.'
            if estacion['es_tramo_inicial']
            else f"{estacion['kilometros']} km cobrados desde Universidad de Chile."
        )
        entrega['plazo'] = f"1 a 2 días hábiles tras la confirmación del pago · {detalle_tramo}"
        entrega['seguimiento'] = (
            f"La vendedora coordina estación y hora en {estacion['nombre']} por WhatsApp; "
            'el estado también se consulta con tu RUT y código de seguimiento en LogisFlow.'
        )
    return entrega
