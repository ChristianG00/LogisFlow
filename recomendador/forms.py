from django import forms

from tienda.models import Producto, VarianteProducto


class RecomendadorForm(forms.Form):
    presupuesto = forms.IntegerField(
        min_value=1,
        max_value=10_000_000,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: 50000',
            'min': '1',
            'step': '1',
        }),
    )
    talla = forms.ChoiceField(
        choices=[('', 'Elige...')] + VarianteProducto.TALLAS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    estilo = forms.ChoiceField(
        choices=[('', 'Elige...')] + Producto.ESTILOS,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    temporada = forms.ChoiceField(
        choices=[('', 'Elige...'), ('VERANO', 'Verano'), ('INVIERNO', 'Invierno')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
