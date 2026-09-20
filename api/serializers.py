from rest_framework import serializers

from .models import (
    Household,
    Earner,
    Income,
    IncomeFrequency,
    ItemCategory,
    BudgetItem,
    BudgetAllocation,
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
            'setup_completed',
            'created_at',
            'last_login',
        ]
        read_only_fields = [
            'household_id',
            'email',
            'setup_completed',
            'created_at',
            'last_login',
        ]


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
        read_only_fields = [
            'earner_id',
            'household',
        ]


# ============================================================
# INCOME FREQUENCY
# ============================================================

class IncomeFrequencySerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomeFrequency
        fields = [
            'frequency_id',
            'frequency_desc',
        ]
        read_only_fields = [
            'frequency_id',
        ]


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
        read_only_fields = [
            'income_id',
            'earner_name',
            'frequency_desc',
        ]

    def get_earner_name(self, obj):
        return f"{obj.earner.earner_fname} {obj.earner.earner_lname}"

    def get_frequency_desc(self, obj):
        return (
            obj.income_frequency.frequency_desc
            if obj.income_frequency
            else None
        )


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
        read_only_fields = [
            'category_id',
        ]


# ============================================================
# BUDGET ITEM
# ============================================================

class BudgetItemSerializer(serializers.ModelSerializer):
    category_desc = serializers.CharField(
        source='category.category_desc',
        read_only=True,
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


# ============================================================
# BILL / BUDGET ALLOCATION
# ============================================================

class BudgetAllocationSerializer(serializers.ModelSerializer):
    item_desc = serializers.CharField(
        source='item.item_desc',
        read_only=True,
    )

    category_desc = serializers.CharField(
        source='item.category.category_desc',
        read_only=True,
    )

    # Read-only BudgetItem fields needed by the Budget tab
    grace_period_days = serializers.IntegerField(
        source='item.grace_period_days',
        read_only=True,
    )

    penalty_classification = serializers.BooleanField(
        source='item.penalty_classification',
        read_only=True,
    )

    due_day = serializers.IntegerField(
        source='item.due_day',
        read_only=True,
    )

    class Meta:
        model = BudgetAllocation

        fields = [
            'budget_allocation_id',
            'income',
            'item',
            'item_desc',
            'category_desc',

            # BudgetItem fields
            'grace_period_days',
            'penalty_classification',
            'due_day',

            'amount',
            'actual_due_date',
            'image_path',
            'scan_date',
            'is_confirmed',
            'budget_amount_range',
            'budget_start_date',
            'budget_end_date',
            'budget_classification',
            'priority_level',
            'period_half',
            'bill_reminder',

            # Payment status
            'is_paid',
            'paid_date',
        ]

        read_only_fields = [
            'budget_allocation_id',
            'item_desc',
            'category_desc',

            # BudgetItem fields are read-only because they come from item
            'grace_period_days',
            'penalty_classification',
            'due_day',

            # Automatically set when the bill is marked as paid
            'paid_date',
        ]


# ============================================================
# RISK ASSESSMENT RESPONSE
# ============================================================

class RiskAssessmentSerializer(serializers.Serializer):
    """Response shape for GET /api/risk/assess/"""

    combined_income = serializers.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    total_bill_allocations = serializers.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    remaining_budget_min = serializers.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    remaining_budget_max = serializers.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    total_daily_expense_min = serializers.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    total_daily_expense_max = serializers.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    days_until_next_payday = serializers.IntegerField()

    total_daily_need_min = serializers.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    total_daily_need_max = serializers.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    risk_level = serializers.CharField()
    # LOW RISK / MODERATE RISK / HIGH RISK

    color_indicator = serializers.CharField()
    # GREEN / AMBER / RED

    label = serializers.CharField()
    # STABLE / AT RISK / CRITICAL

    next_payday = serializers.DateField(
        allow_null=True
    )