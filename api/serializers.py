from rest_framework import serializers
from .models import (
    Household,
    Earner,
    Income,
    IncomeFrequency,
    ItemCategory,
    BudgetItem,
)


# ============================================================
# HOUSEHOLD
# ============================================================

class HouseholdSerializer(serializers.ModelSerializer):
    class Meta:
        model = Household
        fields = [
            'household_id',
            'first_name',
            'last_name',
            'email',
            'total_members',
            'no_of_earners',
            'no_of_dependents',
            'housing_type',
            'daily_food_expense_min',
            'daily_food_expense_max',
            'daily_transport_expense_min',
            'daily_transport_expense_max',
            'survival_threshold_min',
            'survival_threshold_max',
            'created_at',
            'last_login',
        ]
        read_only_fields = ['household_id', 'email', 'created_at', 'last_login']


# ============================================================
# EARNER
# ============================================================

class EarnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Earner
        fields = [
            'earner_id',
            'household',
            'earner_fname',
            'earner_lname',
        ]
        read_only_fields = ['earner_id', 'household']


# ============================================================
# INCOME FREQUENCY
# ============================================================

class IncomeFrequencySerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomeFrequency
        fields = ['frequency_id', 'frequency_desc']
        read_only_fields = ['frequency_id']


# ============================================================
# INCOME
# ============================================================

class IncomeSerializer(serializers.ModelSerializer):
    # Read-only nested fields for display in the mobile app
    earner_name = serializers.SerializerMethodField()
    frequency_desc = serializers.SerializerMethodField()

    class Meta:
        model = Income
        fields = [
            'income_id',
            'earner',
            'earner_name',
            'income_frequency',
            'frequency_desc',
            'range_amount',
            'income_startdate',
            'next_payday',
        ]
        read_only_fields = ['income_id', 'earner_name', 'frequency_desc']

    def get_earner_name(self, obj):
        return f"{obj.earner.earner_fname} {obj.earner.earner_lname}"

    def get_frequency_desc(self, obj):
        return obj.income_frequency.frequency_desc if obj.income_frequency else None


# ============================================================
# ITEM CATEGORY
# ============================================================

class ItemCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemCategory
        fields = [
            'category_id',
            'category_desc',
        ]
        read_only_fields = ['category_id']


# ============================================================
# BUDGET ITEM
# ============================================================

class BudgetItemSerializer(serializers.ModelSerializer):
    category_desc = serializers.CharField(
        source='category.category_desc',
        read_only=True
    )

    class Meta:
        model = BudgetItem
        fields = [
            'item_id',
            'category',
            'category_desc',
            'item_desc',
            'due_day',
            'grace_period_days',
            'penalty_classification',
        ]
        read_only_fields = [
            'item_id',
            'category_desc',
        ]

    def validate_due_day(self, value):
        if value < 1 or value > 31:
            raise serializers.ValidationError(
                'due_day must be between 1 and 31.'
            )
        return value

    def validate_grace_period_days(self, value):
        if value < 0:
            raise serializers.ValidationError(
                'grace_period_days cannot be negative.'
            )
        return value