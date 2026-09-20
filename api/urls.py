from django.urls import path
from . import auth_views
from . import views
from . import app_views
from . import setup_views


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

    # Token refresh - keeps users logged in
    path('auth/refresh/', app_views.refresh_token, name='token-refresh'),

    # ============================================================
    # SETUP
    # ============================================================
    path('setup/submit/', setup_views.submit_setup, name='submit-setup'),

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
    path(
        'earners/<int:earner_id>/update/',
        views.update_earner,
        name='update-earner',
    ),
    path(
        'earners/<int:earner_id>/delete/',
        views.delete_earner,
        name='delete-earner',
    ),

    # ============================================================
    # INCOME FREQUENCIES (reference table)
    # ============================================================
    path(
        'income-frequencies/',
        views.list_income_frequencies,
        name='list-income-frequencies',
    ),

    # ============================================================
    # INCOME
    # ============================================================
    path('income/', views.list_income, name='list-income'),
    path('income/create/', views.create_income, name='create-income'),
    path(
        'income/<int:income_id>/',
        views.get_income,
        name='get-income',
    ),
    path(
        'income/<int:income_id>/update/',
        views.update_income,
        name='update-income',
    ),
    path(
        'income/<int:income_id>/delete/',
        views.delete_income,
        name='delete-income',
    ),

    # ============================================================
    # CATEGORIES
    # ============================================================
    path(
        'categories/',
        views.list_categories,
        name='list-categories',
    ),
    path(
        'categories/create/',
        views.create_category,
        name='create-category',
    ),

    # ============================================================
    # BUDGET ITEMS
    # ============================================================
    path(
        'budget-items/',
        views.list_budget_items,
        name='list-budget-items',
    ),
    path(
        'budget-items/create/',
        views.create_budget_item,
        name='create-budget-item',
    ),
    path(
        'budget-items/<int:item_id>/update/',
        views.update_budget_item,
        name='update-budget-item',
    ),
    path(
        'budget-items/<int:item_id>/delete/',
        views.delete_budget_item,
        name='delete-budget-item',
    ),

    # ============================================================
    # BILLS
    # ============================================================
    path(
        'bills/create/',
        views.create_bill,
        name='create-bill',
    ),
    path(
        'bills/',
        views.list_bills,
        name='list-bills',
    ),
    path(
        'bills/prioritize/',
        views.prioritize_bills,
        name='prioritize-bills',
    ),
    path(
        'bills/prioritized/',
        views.prioritized_bills,
        name='prioritized-bills',
    ),
    path(
        'bills/scan/',
        views.scan_bill,
        name='scan-bill',
    ),
    path(
        'bills/<int:allocation_id>/',
        views.bill_detail,
        name='bill-detail',
    ),
    path(
        'bills/<int:allocation_id>/confirm/',
        views.confirm_bill,
        name='confirm-bill',
    ),

    # ============================================================
    # PRIORITY 6: RISK ASSESSMENT & RECOMMENDATIONS
    # ============================================================
    path(
        'risk/assess/',
        views.assess_risk,
        name='assess-risk',
    ),
    path(
        'recommendations/',
        views.get_recommendations,
        name='recommendations',
    ),

    # ============================================================
    # PRIORITY 7: BUDGET ALLOCATION SUMMARY
    # ============================================================
    path(
        'budget/allocation/',
        views.budget_allocation_summary,
        name='budget-allocation',
    ),

    # ============================================================
    # PRIORITY 8: NOTIFICATIONS / DUE REMINDERS
    # ============================================================
    path(
        'notifications/',
        views.list_notifications,
        name='list-notifications',
    ),
]