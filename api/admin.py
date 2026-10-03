from django.contrib import admin

from .models import Biller


@admin.register(Biller)
class BillerAdmin(admin.ModelAdmin):
    """Edit the biller directory and its late-payment rules in the browser (/admin/)."""
    list_display = ('name', 'category', 'city', 'grace_period_days', 'has_penalty', 'rules_verified')
    list_filter = ('category', 'city', 'rules_verified')
    search_fields = ('name', 'keywords')
    list_editable = ('grace_period_days', 'has_penalty', 'rules_verified')
    ordering = ('category', 'name')
