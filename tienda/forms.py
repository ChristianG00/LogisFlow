import re
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .logistica import TARIFAS_METRO_CHOICES, ESTACIONES_METRO_CHOICES, datos_estacion_metro, datos_tarifa_metro
from .models import Cliente, Producto, SolicitudPrivacidad, VarianteProducto


def normalizar_rut(rut):
    # Valida y deja el RUT en formato 12345678-9
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


def normalizar_telefono(telefono):
    telefono_limpio = re.sub(r'[\s()-]', '', str(telefono).strip())
    coincidencia = re.fullmatch(r'(?:\+?56)?(9\d{8})', telefono_limpio)
    if not coincidencia:
        raise ValidationError('Ingresa un celular chileno válido de 9 dígitos (Ej: 912345678).')
    return f"+56 {coincidencia.group(1)}"


def normalizar_email(email):
    return str(email).strip().lower()


class CheckoutForm(forms.Form):
    rut = forms.CharField(
        max_length=12,
        error_messages={'required': 'Ingresa tu RUT para identificar la compra.'},
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 12.345.678-9'})
    )
    nombre = forms.CharField(
        max_length=100,
        error_messages={'required': 'Ingresa tu nombre completo.'},
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nombre y Apellido'})
    )
    email = forms.EmailField(
        max_length=254,
        error_messages={
            'required': 'Ingresa un correo para enviarte información de tu compra.',
            'invalid': 'Ingresa un correo electrónico válido, por ejemplo nombre@correo.cl.',
        },
        widget=forms.EmailInput(attrs={
            'class': 'form-control', 'placeholder': 'nombre@correo.cl', 'autocomplete': 'email',
        }),
    )
    telefono = forms.CharField(
        max_length=15,
        error_messages={'required': 'Ingresa un celular para coordinar la entrega.'},
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 912345678'})
    )
    
    ENTREGA_CHOICES = [
        ('', '--- Selecciona un método ---'),
        ('Retiro', 'Retiro por el cliente en domicilio del vendedor'),
        ('Metro', 'Entrega personal en estación de Metro'),
        ('Delivery', 'Despacho a domicilio por empresa de transporte'),
    ]
    tipo_entrega = forms.ChoiceField(
        choices=ENTREGA_CHOICES,
        error_messages={'required': 'Selecciona una modalidad de entrega.'},
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'selector_entrega'})
    )

    estacion_metro = forms.ChoiceField(
        choices=[('', '--- Selecciona una estación ---')] + ESTACIONES_METRO_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'selector_estacion_metro'}),
    )

    tarifa_metro = forms.ChoiceField(
        choices=[('', '--- Selecciona un horario de coordinación ---')] + TARIFAS_METRO_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'selector_tarifa_metro'}),
    )
    
    direccion = forms.CharField(
        max_length=200, 
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'campo_direccion', 'placeholder': 'Ej: Estación Tobalaba o Calle Falsa 123'})
    )

    aceptar_terminos = forms.BooleanField(
        required=True,
        label="Acepto los Términos y condiciones de compra.",
        error_messages={'required': 'Debes aceptar los términos, despacho y política de privacidad para continuar.'},
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean_rut(self):
        try:
            return normalizar_rut(self.cleaned_data.get('rut', ''))
        except ValueError as error:
            raise ValidationError(str(error))

    def clean_telefono(self):
        return normalizar_telefono(self.cleaned_data.get('telefono', ''))

    def clean_email(self):
        return normalizar_email(self.cleaned_data.get('email', ''))

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
        tarifa_metro = cleaned_data.get('tarifa_metro')
        rut = cleaned_data.get('rut')
        telefono = cleaned_data.get('telefono')
        email = cleaned_data.get('email')

        if rut and telefono and Cliente.objects.filter(
            telefono=telefono,
            anonimizado_en__isnull=True,
        ).exclude(rut=rut).exists():
            self.add_error('telefono', 'Este teléfono ya está asociado a otro RUT. Usa un número personal distinto.')
        if rut and email and Cliente.objects.filter(
            email__iexact=email,
            anonimizado_en__isnull=True,
        ).exclude(rut=rut).exists():
            self.add_error('email', 'Este correo ya está asociado a otro RUT. Usa un correo personal distinto.')

        # El servidor decide la dirección según la entrega elegida
        if tipo_entrega == 'Retiro':
            cleaned_data['direccion'] = 'Retiro en domicilio (La Granja)'
            return cleaned_data

        if tipo_entrega == 'Metro':
            if not estacion_metro:
                self.add_error('estacion_metro', 'Selecciona la estación de Metro para coordinar la entrega.')
            if not tarifa_metro:
                self.add_error('tarifa_metro', 'Selecciona el horario para aplicar el valor correcto del pasaje.')
            if estacion_metro and tarifa_metro:
                try:
                    estacion = datos_estacion_metro(estacion_metro)
                    tarifa = datos_tarifa_metro(tarifa_metro)
                    cleaned_data['direccion'] = (
                        f"Metro: {estacion['nombre']} ({estacion['linea']}) · "
                        f"{tarifa['nombre']} (${tarifa['costo']})"
                    )
                except ValueError as error:
                    self.add_error('tarifa_metro', str(error))
            return cleaned_data

        if tipo_entrega == 'Delivery' and not direccion:
            self.add_error('direccion', 'Debes ingresar la dirección de envío.')
        elif tipo_entrega == 'Delivery':
            if len(direccion) < 4:
                self.add_error('direccion', 'Ingresa una dirección más específica.')
            else:
                cleaned_data['direccion'] = direccion
            
        return cleaned_data


# Valida los datos del producto antes de guardarlo
class ProductoForm(forms.ModelForm):

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


class RegistroClienteForm(forms.Form):
    rut = forms.CharField(
        max_length=12,
        error_messages={'required': 'Ingresa tu RUT para crear la cuenta.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Ej: 12.345.678-9', 'autocomplete': 'username',
        }),
    )
    nombre = forms.CharField(
        max_length=100,
        error_messages={'required': 'Ingresa tu nombre completo.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Nombre y apellido', 'autocomplete': 'name',
        }),
    )
    email = forms.EmailField(
        max_length=254,
        error_messages={
            'required': 'Ingresa tu correo electrónico.',
            'invalid': 'Ingresa un correo electrónico válido, por ejemplo nombre@correo.cl.',
        },
        widget=forms.EmailInput(attrs={
            'class': 'form-control', 'placeholder': 'nombre@correo.cl', 'autocomplete': 'email',
        }),
    )
    telefono = forms.CharField(
        max_length=20,
        error_messages={'required': 'Ingresa un celular chileno para crear la cuenta.'},
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Ej: 912345678', 'autocomplete': 'tel',
        }),
    )
    password1 = forms.CharField(
        error_messages={'required': 'Crea una contraseña para proteger tu cuenta.'},
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'new-password', 'placeholder': 'Mínimo 10 caracteres',
        }),
    )
    password2 = forms.CharField(
        error_messages={'required': 'Repite tu contraseña para confirmarla.'},
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'new-password', 'placeholder': 'Repite tu contraseña',
        }),
    )
    aceptar_privacidad = forms.BooleanField(
        required=True,
        error_messages={'required': 'Debes aceptar la política de privacidad para crear tu cuenta.'},
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente_existente = None

    def clean_rut(self):
        try:
            return normalizar_rut(self.cleaned_data.get('rut', ''))
        except ValueError as error:
            raise ValidationError(str(error))

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise ValidationError('Ingresa tu nombre completo.')
        return nombre

    def clean_telefono(self):
        return normalizar_telefono(self.cleaned_data.get('telefono', ''))

    def clean_email(self):
        return normalizar_email(self.cleaned_data.get('email', ''))

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get('password1'), cleaned.get('password2')
        if password1 and password2:
            if password1 != password2:
                self.add_error('password2', 'Las contraseñas no coinciden.')
            elif len(password1) < 10:
                self.add_error('password1', 'La contraseña debe tener al menos 10 caracteres.')
            else:
                try:
                    validate_password(password1)
                except ValidationError as error:
                    self.add_error('password1', error)

        rut, telefono, email = cleaned.get('rut'), cleaned.get('telefono'), cleaned.get('email')
        if rut and telefono:
            self.cliente_existente = Cliente.objects.filter(
                rut=rut,
                anonimizado_en__isnull=True,
            ).select_related('usuario').first()
            telefono_ya_usado = Cliente.objects.filter(
                telefono=telefono,
                anonimizado_en__isnull=True,
            ).exclude(rut=rut).exists()
            correo_ya_usado = email and Cliente.objects.filter(
                email__iexact=email,
                anonimizado_en__isnull=True,
            ).exclude(rut=rut).exists()
            usuario_ya_usado = email and get_user_model().objects.filter(
                email__iexact=email,
            ).exclude(pk=self.cliente_existente.usuario_id if self.cliente_existente else None).exists()
            if self.cliente_existente and self.cliente_existente.usuario_id:
                self.add_error('rut', 'Ya existe una cuenta para este RUT. Inicia sesión.')
            elif self.cliente_existente and self.cliente_existente.telefono != telefono:
                self.add_error('telefono', 'El teléfono no coincide con una compra anterior. Solicita una rectificación por el canal de atención de la tienda.')
            elif telefono_ya_usado:
                self.add_error('telefono', 'Este teléfono ya está asociado a otra cuenta o registro de cliente.')
            if correo_ya_usado or usuario_ya_usado:
                self.add_error('email', 'Este correo ya está registrado. Ingresa otro correo o inicia sesión.')
        return cleaned


class AccesoClienteForm(forms.Form):
    identificador = forms.CharField(max_length=254, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'nombre@correo.cl o 912345678', 'autocomplete': 'username',
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'placeholder': 'Tu contraseña', 'autocomplete': 'current-password',
    }))

    def clean_identificador(self):
        identificador = str(self.cleaned_data.get('identificador', '')).strip()
        if '@' in identificador:
            try:
                forms.EmailField().clean(identificador)
            except ValidationError:
                raise ValidationError('Ingresa un correo electrónico válido o un celular chileno de 9 dígitos.')
            return normalizar_email(identificador)
        try:
            return normalizar_telefono(identificador)
        except ValidationError:
            raise ValidationError('Ingresa un correo electrónico válido o un celular chileno de 9 dígitos.')


class SolicitudRecuperacionClaveClienteForm(forms.Form):
    email = forms.EmailField(
        max_length=254,
        error_messages={
            'required': 'Ingresa el correo con el que creaste tu cuenta.',
            'invalid': 'Ingresa un correo electrónico válido, por ejemplo nombre@correo.cl.',
        },
        widget=forms.EmailInput(attrs={
            'class': 'form-control', 'placeholder': 'nombre@correo.cl', 'autocomplete': 'email',
        }),
    )

    def clean_email(self):
        return normalizar_email(self.cleaned_data.get('email', ''))


class ReenviarVerificacionCorreoForm(forms.Form):
    email = forms.EmailField(
        max_length=254,
        error_messages={
            'required': 'Ingresa el correo con el que creaste tu cuenta.',
            'invalid': 'Ingresa un correo electrónico válido, por ejemplo nombre@correo.cl.',
        },
        widget=forms.EmailInput(attrs={
            'class': 'form-control', 'placeholder': 'nombre@correo.cl', 'autocomplete': 'email',
        }),
    )

    def clean_email(self):
        return normalizar_email(self.cleaned_data.get('email', ''))


class RestablecerClaveClienteForm(forms.Form):
    password1 = forms.CharField(
        error_messages={'required': 'Crea una nueva contraseña.'},
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'new-password', 'placeholder': 'Mínimo 10 caracteres',
        }),
    )
    password2 = forms.CharField(
        error_messages={'required': 'Repite la nueva contraseña.'},
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'new-password', 'placeholder': 'Repite tu contraseña',
        }),
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get('password1'), cleaned.get('password2')
        if password1 and password2:
            if password1 != password2:
                self.add_error('password2', 'Las contraseñas no coinciden.')
            else:
                try:
                    validate_password(password1, user=self.usuario)
                except ValidationError as error:
                    self.add_error('password1', error)
        return cleaned


# La clienta solo puede editar su dirección desde la cuenta
class DireccionClienteForm(forms.ModelForm):

    class Meta:
        model = Cliente
        fields = ['direccion']
        widgets = {
            'direccion': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'street-address',
                'placeholder': 'Calle, número, comuna y referencia',
            }),
        }

    def clean_direccion(self):
        direccion = ' '.join(self.cleaned_data.get('direccion', '').split())
        if len(direccion) < 4:
            raise ValidationError('Ingresa una dirección más específica.')
        return direccion


class SolicitudPrivacidadForm(forms.Form):
    tipo = forms.ChoiceField(
        choices=SolicitudPrivacidad.TIPOS_SOPORTE,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    detalle = forms.CharField(
        min_length=10,
        max_length=1000,
        error_messages={
            'required': 'Describe brevemente en qué podemos ayudarte.',
            'min_length': 'Entrega al menos 10 caracteres para poder revisar tu caso.',
        },
        widget=forms.Textarea(attrs={
            'class': 'form-control', 'rows': 4,
            'placeholder': 'Describe tu consulta. No incluyas contraseñas, datos de tarjeta ni códigos de pago.',
        }),
    )

    def clean_detalle(self):
        detalle = ' '.join(self.cleaned_data.get('detalle', '').split())
        if len(detalle) < 10:
            raise ValidationError('Entrega al menos 10 caracteres para poder revisar tu caso.')
        return detalle


# Para compras invitadas se pide RUT y código de seguimiento
class SoporteInvitadoForm(SolicitudPrivacidadForm):
    rut = forms.CharField(
        max_length=12,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Ej: 12.345.678-9', 'autocomplete': 'off',
        }),
    )
    codigo_seguimiento = forms.CharField(
        max_length=13,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Ej: LF-A1B2C3D4E5', 'autocomplete': 'off',
        }),
    )
    tipo = forms.ChoiceField(
        choices=[opcion for opcion in SolicitudPrivacidad.TIPOS_SOPORTE if opcion[0] != 'CUENTA'],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean_rut(self):
        try:
            return normalizar_rut(self.cleaned_data.get('rut', ''))
        except ValueError as error:
            raise ValidationError(str(error))

    def clean_codigo_seguimiento(self):
        codigo = str(self.cleaned_data.get('codigo_seguimiento', '')).strip().upper()
        if not re.fullmatch(r'LF-[A-F0-9]{10}', codigo):
            raise ValidationError('Ingresa el código de seguimiento con formato LF-XXXXXXXXXX.')
        return codigo


class ConsultaRespuestaSoporteForm(forms.Form):
    rut = forms.CharField(
        max_length=12,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'RUT de la compra',
            'autocomplete': 'off',
        }),
    )
    codigo_consulta = forms.CharField(
        max_length=14,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: SUP-A1B2C3D4E5',
            'autocomplete': 'off',
            'autocapitalize': 'characters',
        }),
    )

    def clean_rut(self):
        try:
            return normalizar_rut(self.cleaned_data.get('rut', ''))
        except ValueError as error:
            raise ValidationError(str(error))

    def clean_codigo_consulta(self):
        codigo = str(self.cleaned_data.get('codigo_consulta', '')).strip().upper()
        if not re.fullmatch(r'SUP-[A-F0-9]{10}', codigo):
            raise ValidationError('Ingresa el código de consulta con formato SUP-XXXXXXXXXX.')
        return codigo


class RespuestaSoporteForm(forms.Form):
    respuesta = forms.CharField(
        min_length=10,
        max_length=2000,
        error_messages={
            'required': 'Escribe una respuesta antes de enviarla.',
            'min_length': 'La respuesta debe tener al menos 10 caracteres.',
        },
        widget=forms.Textarea(attrs={
            'class': 'form-control form-control-sm',
            'rows': 3,
            'placeholder': 'Escribe una respuesta clara para la clienta.',
        }),
    )

    def clean_respuesta(self):
        respuesta = ' '.join(self.cleaned_data.get('respuesta', '').split())
        if len(respuesta) < 10:
            raise ValidationError('La respuesta debe tener al menos 10 caracteres.')
        return respuesta


class IncidenteTecnicoForm(forms.Form):
    asunto = forms.CharField(
        min_length=5,
        max_length=150,
        error_messages={
            'required': 'Indica un asunto para el incidente.',
            'min_length': 'El asunto debe tener al menos 5 caracteres.',
        },
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: Mercado Pago rechaza todas las tarjetas',
            'autocomplete': 'off',
        }),
    )
    descripcion = forms.CharField(
        min_length=15,
        max_length=2000,
        error_messages={
            'required': 'Describe qué ocurre para que el equipo técnico pueda revisarlo.',
            'min_length': 'Describe el problema con al menos 15 caracteres.',
        },
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 7,
            'placeholder': 'Indica desde cuándo ocurre, qué estabas haciendo y qué mensaje viste. No incluyas contraseñas, RUT, datos de tarjeta ni códigos de pago.',
        }),
    )

    def clean_asunto(self):
        return ' '.join(self.cleaned_data['asunto'].split())

    def clean_descripcion(self):
        return ' '.join(self.cleaned_data['descripcion'].split())
