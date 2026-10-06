from django.urls import path
from . import views

app_name = 'vendors'

urlpatterns = [
    path('onboarding/', views.onboarding_view, name='onboarding'),
]
