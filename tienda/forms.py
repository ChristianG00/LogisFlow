import re
from django import forms
from django.core.exceptions import ValidationError

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
        ('Retiro', 'Retiro en Domicilio del Vendedor'),
        ('Metro', 'Entrega en Estación de Metro'),
        ('Delivery', 'Despacho a Domicilio (Starken/Chilexpress)'),
    ]
    tipo_entrega = forms.ChoiceField(
        choices=ENTREGA_CHOICES, 
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'selector_entrega'})
    )
    
    direccion = forms.CharField(
        max_length=200, 
        required=False, # Ahora es falso, porque si elige "Metro" no necesita dirección
        widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'campo_direccion', 'placeholder': 'Ej: Estación Tobalaba o Calle Falsa 123'})
    )

    aceptar_terminos = forms.BooleanField(
        required=True,
        label="Acepto el tratamiento de mis datos personales según la Ley N° 19.628.",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    # ==========================================
    # VALIDACIONES ESTRICTAS (NIVEL TESIS)
    # ==========================================

    def clean_rut(self):
        rut = self.cleaned_data.get('rut', '')
        # 1. Limpiar puntos y guiones
        rut_limpio = rut.replace('.', '').replace('-', '').upper()
        
        # 2. Validar formato con Expresión Regular
        if not re.match(r'^\d{7,8}[0-9K]$', rut_limpio):
            raise ValidationError("El formato del RUT no es válido.")

        # 3. Algoritmo Módulo 11 (Matemática real del Registro Civil)
        cuerpo = rut_limpio[:-1]
        dv = rut_limpio[-1]
        
        suma = 0
        multiplo = 2
        
        for c in reversed(cuerpo):
            suma += int(c) * multiplo
            multiplo += 1
            if multiplo == 8:
                multiplo = 2
                
        esperado = 11 - (suma % 11)
        
        if esperado == 11:
            dv_esperado = '0'
        elif esperado == 10:
            dv_esperado = 'K'
        else:
            dv_esperado = str(esperado)
            
        if dv != dv_esperado:
            raise ValidationError("El RUT ingresado no existe o es inválido.")
            
        # Si pasa todas las pruebas, retorna el RUT formateado bonito
        return f"{cuerpo}-{dv}"

    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono', '')
        # Eliminar espacios o el +56
        tel_limpio = telefono.replace(' ', '').replace('+56', '')
        
        # Un celular chileno válido tiene 9 dígitos y empieza con 9
        if not re.match(r'^9\d{8}$', tel_limpio):
            raise ValidationError("Ingresa un celular válido de 9 dígitos (Ej: 912345678).")
            
        return f"+56 {tel_limpio}"

    def clean(self):
        cleaned_data = super().clean()
        tipo_entrega = cleaned_data.get('tipo_entrega')
        direccion = cleaned_data.get('direccion')

        # Validación cruzada: Si elige Metro o Delivery, DEBE escribir algo en el campo
        if tipo_entrega in ['Metro', 'Delivery'] and not direccion:
            self.add_error('direccion', 'Debes especificar la estación de Metro o la dirección de envío.')
            
        return cleaned_data