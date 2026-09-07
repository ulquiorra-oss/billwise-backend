from django.urls import path
from . import auth_views

urlpatterns = [
    # Authentication endpoints
    path('auth/register/', auth_views.register, name='register'),
    path('auth/login/', auth_views.login, name='login'),
    path('auth/google/', auth_views.google_login, name='google-login'),
    path('auth/logout/', auth_views.logout, name='logout'),
    path('auth/profile/', auth_views.get_profile, name='profile'),
]