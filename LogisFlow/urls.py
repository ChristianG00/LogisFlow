import os

from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    re_path(
        r'^media/productos/(?P<path>[^/]+)$',
        serve,
        {'document_root': os.path.join(settings.MEDIA_ROOT, 'productos')},
    ),
    path('', include('tienda.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
