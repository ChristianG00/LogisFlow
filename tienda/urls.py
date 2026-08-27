from django.urls import path
from . import views
from recomendador.views import motor_recomendacion

urlpatterns = [
    # Tienda y cuenta cliente
    path('', views.catalogo, name='catalogo'),
    path('producto/<int:producto_id>/', views.producto_detalle, name='producto_detalle'),
    path('recomendador/', motor_recomendacion, name='recomendador'),
    path('cuenta/registro/', views.registro_cliente, name='registro_cliente'),
    path('cuenta/ingresar/', views.iniciar_sesion_cliente, name='iniciar_sesion_cliente'),
    path('cuenta/verificar-correo/<uidb64>/<token>/', views.verificar_correo_cliente, name='verificar_correo_cliente'),
    path('cuenta/reenviar-verificacion/', views.reenviar_verificacion_correo_cliente, name='reenviar_verificacion_correo_cliente'),
    path('cuenta/recuperar-clave/', views.solicitar_recuperacion_clave_cliente, name='recuperar_clave_cliente'),
    path('cuenta/restablecer-clave/<uidb64>/<token>/', views.restablecer_clave_cliente, name='restablecer_clave_cliente'),
    path('cuenta/salir/', views.cerrar_sesion_cliente, name='cerrar_sesion_cliente'),
    path('mi-cuenta/', views.mi_cuenta, name='mi_cuenta'),
    path('mi-cuenta/direccion/', views.actualizar_direccion_cliente, name='actualizar_direccion_cliente'),
    path('mi-cuenta/mis-datos/', views.descargar_mis_datos, name='descargar_mis_datos'),
    path('mi-cuenta/privacidad/', views.solicitar_privacidad, name='solicitar_privacidad'),
    
    
    # Carrito
    path('carrito/', views.ver_carrito, name='ver_carrito'),
    path('carrito/agregar/<int:producto_id>/', views.agregar_carrito, name='agregar_carrito'),
    path('carrito/sumar/<str:item_id>/', views.sumar_carrito, name='sumar_carrito'),
    path('carrito/restar/<str:item_id>/', views.restar_carrito, name='restar_carrito'),
    path('carrito/eliminar/<str:item_id>/', views.eliminar_del_carrito, name='eliminar_del_carrito'),
    
    # Compra y seguimiento
    path('checkout/', views.crear_pedido, name='crear_pedido'), 
    path('exito/<uuid:referencia>/', views.pedido_exitoso, name='pedido_exitoso'),
    path('seguimiento/', views.seguimiento, name='seguimiento'),
    path('despacho-y-boleta/', views.politicas_despacho, name='politicas_despacho'),
    path('terminos-y-condiciones/', views.terminos_condiciones, name='terminos_condiciones'),
    path('politica-de-privacidad/', views.politica_privacidad, name='politica_privacidad'),
    path('webhook/', views.webhook_mercadopago, name='webhook_mercadopago'),
    
    # Administración interna: un único acceso privado para la dueña
    path('administracion-interna-8b741e/', views.iniciar_sesion_administracion, name='login'),
    path('administracion-interna-8b741e/panel/', views.dashboard, name='dashboard'),
    path('administracion-interna-8b741e/panel/pedido/actualizar/', views.actualizar_estado_pedido, name='actualizar_estado_pedido'),
    path('administracion-interna-8b741e/panel/soporte/<int:solicitud_id>/responder/', views.responder_solicitud_soporte, name='responder_solicitud_soporte'),
    path('administracion-interna-8b741e/panel/incidente-tecnico/', views.reportar_incidente_tecnico, name='reportar_incidente_tecnico'),
    path('administracion-interna-8b741e/panel/producto/guardar/', views.guardar_producto, name='guardar_producto'),
    path('administracion-interna-8b741e/panel/producto/eliminar/<int:producto_id>/', views.eliminar_producto, name='eliminar_producto'),
    path('administracion-interna-8b741e/panel/producto/talla/guardar/', views.guardar_talla, name='guardar_talla'),
    path('administracion-interna-8b741e/panel/exportar/', views.exportar_excel, name='exportar_excel'),
    path('administracion-interna-8b741e/salir/', views.cerrar_sesion_cliente, name='logout'),
]
