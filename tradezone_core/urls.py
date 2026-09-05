from django.contrib import admin
from django.urls import path
from products import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home_view, name='home'),  # Home page
    path('list/', views.list_item_view, name='list_item'),  # List items page
    path('product/<int:id>/', views.product_detail_view, name='product_detail'),  # Product detail page
    path('checkout/', views.checkout_view, name='checkout'),  # Checkout page
]
