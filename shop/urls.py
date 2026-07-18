from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('cart/', views.cart_detail, name='cart_detail'),
    path('cart/add/', views.add_to_cart, name='add_to_cart'),
    path('cart/update/', views.update_cart_quantity, name='update_cart_quantity'),
    path('cart/remove/', views.remove_from_cart, name='remove_from_cart'),
    path('cart/badge/', views.cart_badge, name='cart_badge'),
    path('cart/drawer/', views.cart_drawer, name='cart_drawer'),
    path('checkout/', views.checkout, name='checkout'),
    path('checkout/calculate-delivery/', views.calculate_delivery, name='calculate_delivery'),
    path('order/<int:order_id>/', views.order_tracking, name='order_tracking'),
    path('order/<int:order_id>/status-api/', views.order_status_api, name='order_status_api'),
    path('payment/verify/', views.verify_payment, name='verify_payment'),
    path('payment/webhook/', views.razorpay_webhook, name='razorpay_webhook'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-dashboard/update-status/', views.admin_update_status, name='admin_update_status'),
    path('admin-dashboard/update-payment/', views.admin_update_payment, name='admin_update_payment'),
    path('admin-dashboard/notification/', views.admin_order_notification, name='admin_order_notification'),
    path('login/', views.customer_login, name='customer_login'),
    path('verify-otp/', views.verify_otp, name='verify_otp'),
    path('logout/', views.customer_logout, name='customer_logout'),
    path('my-orders/', views.my_orders, name='my_orders'),
]
