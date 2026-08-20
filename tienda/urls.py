from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from recomendador.views import motor_recomendacion

urlpatterns = [
    # Rutas públicas
    path('', views.catalogo, name='catalogo'),
    path('producto/<int:producto_id>/', views.producto_detalle, name='producto_detalle'),
    path('recomendador/', motor_recomendacion, name='recomendador'),
    
    
    # Rutas del Carrito
    path('carrito/', views.ver_carrito, name='ver_carrito'),
    path('carrito/agregar/<int:producto_id>/', views.agregar_carrito, name='agregar_carrito'),
    path('carrito/sumar/<str:producto_id>/', views.sumar_carrito, name='sumar_carrito'),
    path('carrito/restar/<str:producto_id>/', views.restar_carrito, name='restar_carrito'),
    path('carrito/eliminar/<str:producto_id>/', views.eliminar_del_carrito, name='eliminar_del_carrito'),
    
    # Rutas de Compra y Seguimiento
    path('checkout/', views.crear_pedido, name='crear_pedido'), 
    path('exito/<int:pedido_id>/', views.pedido_exitoso, name='pedido_exitoso'),
    path('seguimiento/', views.seguimiento, name='seguimiento'),
    path('webhook/', views.webhook_mercadopago, name='webhook_mercadopago'),
    
    # Rutas administrativas (Aquí están todas las de la dueña)
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/producto/guardar/', views.guardar_producto, name='guardar_producto'),
    path('dashboard/producto/eliminar/<int:producto_id>/', views.eliminar_producto, name='eliminar_producto'),
    path('dashboard/producto/talla/guardar/', views.guardar_talla, name='guardar_talla'),
    
    # --- LA RUTA QUE FALTABA PARA EL EXCEL ---
    path('dashboard/exportar/', views.exportar_excel, name='exportar_excel'), 
    
    # Login / Logout
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]