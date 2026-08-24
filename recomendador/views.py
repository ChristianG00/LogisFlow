from django.db.models import F, Prefetch
from django.shortcuts import render

from tienda.models import Producto, VarianteProducto

from .forms import RecomendadorForm


COLORES_NEUTROS = {'NEGRO', 'BLANCO', 'JEANS', 'BEIGE'}
COMBINACIONES_ARMONICAS = {
    frozenset(('AZUL', 'BLANCO')),
    frozenset(('AZUL', 'BEIGE')),
    frozenset(('ROJO', 'NEGRO')),
    frozenset(('ROJO', 'BLANCO')),
    frozenset(('VERDE', 'BEIGE')),
    frozenset(('VERDE', 'NEGRO')),
    frozenset(('ROSADO', 'BLANCO')),
    frozenset(('ROSADO', 'JEANS')),
    frozenset(('ROSADO', 'BEIGE')),
    frozenset(('FUCSIA', 'NEGRO')),
    frozenset(('FUCSIA', 'BLANCO')),
    frozenset(('LILA', 'BLANCO')),
    frozenset(('LILA', 'BEIGE')),
    frozenset(('MORADO', 'NEGRO')),
    frozenset(('MORADO', 'BEIGE')),
    frozenset(('CORAL', 'BLANCO')),
    frozenset(('CORAL', 'JEANS')),
    frozenset(('BURDEO', 'NEGRO')),
    frozenset(('BURDEO', 'BEIGE')),
}
CATEGORIAS_SUPERIORES = {'SUPERIOR', 'POLERAS', 'BLUSAS', 'CHAQUETAS'}
CATEGORIAS_INFERIORES = {'INFERIOR', 'PANTALONES', 'FALDAS'}


def _puntuar_outfit(arriba, abajo, presupuesto, estilo, temporada):
    # Puntaje explicable: preferencias, armonía, stock y uso del presupuesto
    puntaje = 20
    razones = ['Dentro de tu presupuesto']

    coincidencias_estilo = 0
    coincidencias_temporada = 0
    for prenda in (arriba, abajo):
        if prenda.estilo == estilo:
            puntaje += 12
            coincidencias_estilo += 1
        if prenda.temporada == temporada:
            puntaje += 8
            coincidencias_temporada += 1
        elif prenda.temporada == 'ATEMPORAL':
            puntaje += 6
            coincidencias_temporada += 1

    if coincidencias_estilo == 2:
        razones.append('Estilo elegido en ambas prendas')
    elif coincidencias_estilo:
        razones.append('Incluye tu estilo preferido')

    if coincidencias_temporada == 2:
        razones.append('Adecuado para la temporada')

    colores = frozenset((arriba.color_base, abajo.color_base))
    if arriba.color_base in COLORES_NEUTROS or abajo.color_base in COLORES_NEUTROS:
        puntaje += 15
        razones.append('Colores fáciles de combinar')
    elif arriba.color_base == abajo.color_base:
        puntaje += 10
        razones.append('Paleta de color consistente')
    elif colores in COMBINACIONES_ARMONICAS:
        puntaje += 12
        razones.append('Colores armónicos')

    total = arriba.precio + abajo.precio
    porcentaje_usado = total / presupuesto
    if porcentaje_usado >= 0.8:
        puntaje += 12
        razones.append('Aprovecha bien tu presupuesto')
    elif porcentaje_usado >= 0.5:
        puntaje += 7

    stock_minimo = min(arriba.stock_recomendado, abajo.stock_recomendado)
    if stock_minimo >= 3:
        puntaje += 5
        razones.append('Stock disponible')

    return puntaje, razones, stock_minimo


def _productos_disponibles(talla, presupuesto):
    variantes = VarianteProducto.objects.filter(talla=talla, stock__gt=F('stock_reservado'))
    productos = (
        Producto.objects.filter(
            precio__gte=1,
            precio__lte=presupuesto,
            variantes__talla=talla,
            variantes__stock__gt=F('variantes__stock_reservado'),
        )
        .distinct()
        .prefetch_related(Prefetch('variantes', queryset=variantes, to_attr='variantes_recomendadas'))
    )

    disponibles = []
    for producto in productos:
        # La combinación producto+talla es unica, se usa el stock real de esa talla
        if producto.variantes_recomendadas:
            producto.stock_recomendado = producto.variantes_recomendadas[0].stock_disponible
            producto.talla_recomendada = talla
            disponibles.append(producto)
    return disponibles


def motor_recomendacion(request):
    form = RecomendadorForm(request.GET or None)
    combos_sugeridos = []
    error = None

    if request.GET:
        if form.is_valid():
            presupuesto = form.cleaned_data['presupuesto']
            talla = form.cleaned_data['talla']
            estilo = form.cleaned_data['estilo']
            temporada = form.cleaned_data['temporada']

            prendas = _productos_disponibles(talla, presupuesto)
            superiores = [prenda for prenda in prendas if prenda.categoria in CATEGORIAS_SUPERIORES]
            inferiores = [prenda for prenda in prendas if prenda.categoria in CATEGORIAS_INFERIORES]

            if not superiores or not inferiores:
                faltantes = []
                if not superiores:
                    faltantes.append('prendas superiores')
                if not inferiores:
                    faltantes.append('prendas inferiores')
                error = f"No encontramos {' ni '.join(faltantes)} con stock en talla {talla} y dentro de tu presupuesto."
            else:
                evaluados = []
                for arriba in superiores:
                    for abajo in inferiores:
                        total = arriba.precio + abajo.precio
                        if total > presupuesto:
                            continue
                        puntaje, razones, stock_minimo = _puntuar_outfit(
                            arriba, abajo, presupuesto, estilo, temporada,
                        )
                        evaluados.append({
                            'productos': [arriba, abajo],
                            'total': total,
                            'sobrante': presupuesto - total,
                            'puntaje': puntaje,
                            'razones': razones[:4],
                            'stock_minimo': stock_minimo,
                        })

                evaluados.sort(
                    key=lambda combo: (
                        -combo['puntaje'],
                        combo['sobrante'],
                        -combo['stock_minimo'],
                        combo['productos'][0].nombre.lower(),
                        combo['productos'][1].nombre.lower(),
                    )
                )
                combos_sugeridos = evaluados[:3]
                if not combos_sugeridos:
                    error = 'No pudimos armar un outfit completo con ese presupuesto. Prueba con un monto mayor.'
        else:
            error = 'Revisa presupuesto, talla, estilo y temporada antes de buscar.'

    return render(request, 'recomendador_ui.html', {
        'form': form,
        'combos': combos_sugeridos,
        'error': error,
    })
