import os

from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', include('tienda.urls')),
]

# Fotos públicas de productos en AlwaysData
urlpatterns += static(
    f'{settings.MEDIA_URL}productos/',
    document_root=os.path.join(settings.MEDIA_ROOT, 'productos'),
)

# Archivos multimedia adicionales durante el desarrollo local
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
