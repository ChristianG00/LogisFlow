import re
from django import forms
from django.core.exceptions import ValidationError

from .logistica import ESTACIONES_METRO_CHOICES, datos_estacion_metro
from .models import Producto, VarianteProducto


def normalizar_rut(rut):
    """Valida el dígito verificador y devuelve el formato canónico 12345678-9."""
    rut_limpio = re.sub(r'[.\-\s]', '', str(rut)).upper()
    if not re.fullmatch(r'\d{7,8}[0-9K]', rut_limpio):
        raise ValueError('El formato del RUT no es válido.')

    cuerpo, dv = rut_limpio[:-1], rut_limpio[-1]
    if int(cuerpo) == 0:
        raise ValueError('El RUT no es válido.')

    suma, multiplo = 0, 2
    for digito in reversed(cuerpo):
        suma += int(digito) * multiplo
        multiplo = 2 if multiplo == 7 else multiplo + 1
    resultado = 11 - (suma % 11)
    esperado = '0' if resultado == 11 else 'K' if resultado == 10 else str(resultado)
    if dv != esperado:
        raise ValueError('El RUT ingresado no existe o es inválido.')
    return f'{cuerpo}-{dv}'


class CheckoutForm(forms.Form):
    rut = forms.CharField(
        max_length=12, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 12.345.678-9'})
    )
    nombre = forms.CharField(
        max_length=100, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nombre y Apellido'})
    )
    telefono = forms.CharField(
        max_length=15, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 912345678'})
    )
    
    # Hemos separado los tipos de entrega estratégicamente
    ENTREGA_CHOICES = [
        ('', '--- Selecciona un método ---'),
        ('Retiro', 'Retiro por el cliente en domicilio del vendedor'),
        ('Metro', 'Entrega personal en estación de Metro'),
        ('Delivery', 'Despacho a domicilio por empresa de transporte'),
    ]
    tipo_entrega = forms.ChoiceField(
        choices=ENTREGA_CHOICES, 
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'selector_entrega'})
    )

    estacion_metro = forms.ChoiceField(
        choices=[('', '--- Selecciona una estación ---')] + ESTACIONES_METRO_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'selector_estacion_metro'}),
    )
    
    direccion = forms.CharField(
        max_length=200, 
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'campo_direccion', 'placeholder': 'Ej: Estación Tobalaba o Calle Falsa 123'})
    )

    aceptar_terminos = forms.BooleanField(
        required=True,
        label="Acepto que mis datos se usen solo para gestionar la compra, el despacho y el seguimiento.",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    # ==========================================
    # VALIDACIONES ESTRICTAS (NIVEL TESIS)
    # ==========================================

    def clean_rut(self):
        try:
            return normalizar_rut(self.cleaned_data.get('rut', ''))
        except ValueError as error:
            raise ValidationError(str(error))

    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono', '').strip()
        # Permite escribir 912345678, 56912345678 o +56 9 1234 5678.
        tel_limpio = re.sub(r'[\s()-]', '', telefono)
        coincidencia = re.fullmatch(r'(?:\+?56)?(9\d{8})', tel_limpio)
        if not coincidencia:
            raise ValidationError("Ingresa un celular chileno válido de 9 dígitos (Ej: 912345678).")
            
        return f"+56 {coincidencia.group(1)}"

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise ValidationError("Ingresa tu nombre completo.")
        return nombre

    def clean(self):
        cleaned_data = super().clean()
        tipo_entrega = cleaned_data.get('tipo_entrega')
        direccion = ' '.join((cleaned_data.get('direccion') or '').split())
        estacion_metro = cleaned_data.get('estacion_metro')

        # La dirección se determina en el servidor, nunca solo en JavaScript.
        if tipo_entrega == 'Retiro':
            cleaned_data['direccion'] = 'Retiro en domicilio (La Granja)'
            return cleaned_data

        if tipo_entrega == 'Metro':
            if not estacion_metro:
                self.add_error('estacion_metro', 'Selecciona la estación de Metro para coordinar la entrega.')
            else:
                try:
                    estacion = datos_estacion_metro(estacion_metro)
                    cleaned_data['direccion'] = f"Metro: {estacion['nombre']} ({estacion['linea']})"
                except ValueError as error:
                    self.add_error('estacion_metro', str(error))
            return cleaned_data

        if tipo_entrega == 'Delivery' and not direccion:
            self.add_error('direccion', 'Debes ingresar la dirección de envío.')
        elif tipo_entrega == 'Delivery':
            if len(direccion) < 4:
                self.add_error('direccion', 'Ingresa una dirección más específica.')
            else:
                cleaned_data['direccion'] = direccion
            
        return cleaned_data


class ProductoForm(forms.ModelForm):
    """Valida el inventario antes de guardarlo; no confía en los campos HTML."""

    class Meta:
        model = Producto
        fields = ['nombre', 'precio', 'imagen', 'categoria', 'color_base', 'estilo', 'temporada']

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 2:
            raise ValidationError('El nombre del producto es demasiado corto.')
        return nombre

    def clean_precio(self):
        precio = self.cleaned_data['precio']
        if precio < 1:
            raise ValidationError('El precio debe ser mayor que cero.')
        return precio


class TallaForm(forms.Form):
    producto_id = forms.IntegerField(min_value=1)
    talla = forms.ChoiceField(choices=VarianteProducto.TALLAS_CHOICES)
    stock = forms.IntegerField(min_value=0, max_value=100000)
