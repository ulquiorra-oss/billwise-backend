from django.urls import path
from . import auth_views
from . import views

urlpatterns = [
    # ============================================================
    # AUTHENTICATION
    # ============================================================
    path('auth/register/', auth_views.register, name='register'),
    path('auth/login/', auth_views.login, name='login'),
    path('auth/google/', auth_views.google_login, name='google-login'),
    path('auth/logout/', auth_views.logout, name='logout'),
    path('auth/profile/', auth_views.get_profile, name='profile'),
    path('auth/debug/', auth_views.debug_token, name='debug'),

    # ============================================================
    # HOUSEHOLD
    # ============================================================
    path('household/', views.get_household, name='get-household'),
    path('household/update/', views.update_household, name='update-household'),

    # ============================================================
    # EARNERS
    # ============================================================
    path('earners/', views.list_earners, name='list-earners'),
    path('earners/create/', views.create_earner, name='create-earner'),
    path('earners/<int:earner_id>/', views.get_earner, name='get-earner'),
    path('earners/<int:earner_id>/update/', views.update_earner, name='update-earner'),
    path('earners/<int:earner_id>/delete/', views.delete_earner, name='delete-earner'),

    # ============================================================
    # INCOME FREQUENCIES (reference table)
    # ============================================================
    path('income-frequencies/', views.list_income_frequencies, name='list-income-frequencies'),

    # ============================================================
    # INCOME
    # ============================================================
    path('income/', views.list_income, name='list-income'),
    path('income/create/', views.create_income, name='create-income'),
    path('income/<int:income_id>/', views.get_income, name='get-income'),
    path('income/<int:income_id>/update/', views.update_income, name='update-income'),
    path('income/<int:income_id>/delete/', views.delete_income, name='delete-income'),
]