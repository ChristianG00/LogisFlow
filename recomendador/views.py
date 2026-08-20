import random
from django.shortcuts import render
from tienda.models import Producto 

def motor_recomendacion(request):
    presupuesto = request.GET.get('presupuesto')
    talla_elegida = request.GET.get('talla')
    estilo_elegido = request.GET.get('estilo')
    temporada_elegida = request.GET.get('temporada')
    
    combos_sugeridos = []
    error = None

    if presupuesto and talla_elegida and estilo_elegido and temporada_elegida:
        try:
            monto_maximo = int(presupuesto)
            
            # Filtro estricto: Todo debe estar en presupuesto y con stock en la talla
            prendas_validas = Producto.objects.filter(
                precio__lte=monto_maximo,
                variantes__talla=talla_elegida,
                variantes__stock__gt=0
            ).distinct()

            superiores = list(prendas_validas.filter(categoria='SUPERIOR'))
            inferiores = list(prendas_validas.filter(categoria='INFERIOR'))

            # Generar TODAS las combinaciones posibles para evaluarlas
            combinaciones_evaluadas = []
            
            for arriba in superiores:
                for abajo in inferiores:
                    total_combo = arriba.precio + abajo.precio
                    
                    if total_combo <= monto_maximo:
                        # === MOTOR HEURÍSTICO (SISTEMA DE PUNTOS) ===
                        puntaje = 0
                        
                        # A. Regla de Presupuesto (+30 puntos)
                        puntaje += 30
                        
                        # B. Regla de Estilo (+20 puntos max)
                        if arriba.estilo == estilo_elegido: puntaje += 10
                        if abajo.estilo == estilo_elegido: puntaje += 10
                        
                        # C. Regla de Temporada (+10 puntos max)
                        if arriba.temporada in [temporada_elegida, 'ATEMPORAL']: puntaje += 5
                        if abajo.temporada in [temporada_elegida, 'ATEMPORAL']: puntaje += 5
                        
                        # D. Bono de Compatibilidad de Color (+15 puntos)
                        colores_comodines = ['NEGRO', 'BLANCO', 'JEANS']
                        if arriba.color_base in colores_comodines or abajo.color_base in colores_comodines:
                            puntaje += 15
                        elif arriba.color_base == abajo.color_base:
                            puntaje += 10
                            
                        combinaciones_evaluadas.append({
                            'productos': [arriba, abajo],
                            'total': total_combo,
                            'sobrante': monto_maximo - total_combo,
                            'puntaje': puntaje
                        })
            
            # ORDENAR Y SELECCIONAR A LOS GANADORES
            combinaciones_evaluadas.sort(key=lambda x: x['puntaje'], reverse=True)
            combos_sugeridos = combinaciones_evaluadas[:3] # Muestra solo el Top 3
                
            if not combos_sugeridos:
                error = "Lo sentimos, no pudimos armar un conjunto con esas características. ¡Intenta flexibilizar el presupuesto o cambiar el estilo!"
                
        except ValueError:
            error = "Por favor, ingresa un presupuesto válido."
            
    return render(request, 'recomendador_ui.html', {
        'combos': combos_sugeridos, 
        'presupuesto': presupuesto,
        'talla': talla_elegida,
        'estilo': estilo_elegido,
        'temporada': temporada_elegida,
        'error': error
    })