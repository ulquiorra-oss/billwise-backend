from django.db import models


class Household(models.Model):
    household_id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=255)
    total_members = models.IntegerField(default=0)
    no_of_earners = models.IntegerField(default=0)
    no_of_dependents = models.IntegerField(default=0)
    housing_type = models.CharField(max_length=100, blank=True)
    daily_food_expense_min = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_food_expense_max = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_transport_expense_min = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_transport_expense_max = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    survival_threshold_min = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    survival_threshold_max = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    # ---- Required by DRF / SimpleJWT ----

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        """Required by SimpleJWT's RefreshToken.for_user()"""
        return True

    @property
    def pk(self):
        """Explicitly expose household_id as the primary key"""
        return self.household_id

    class Meta:
        db_table = 'household'

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Earner(models.Model):
    earner_id = models.AutoField(primary_key=True)
    household = models.ForeignKey(
        Household,
        on_delete=models.CASCADE,
        db_column='household_id'
    )
    earner_fname = models.CharField(max_length=100)
    earner_lname = models.CharField(max_length=100)

    class Meta:
        db_table = 'earner'

    def __str__(self):
        return f"{self.earner_fname} {self.earner_lname}"


class IncomeFrequency(models.Model):
    frequency_id = models.AutoField(primary_key=True)
    frequency_desc = models.CharField(max_length=100)

    class Meta:
        db_table = 'income_frequency'

    def __str__(self):
        return self.frequency_desc


class Income(models.Model):
    income_id = models.AutoField(primary_key=True)
    earner = models.ForeignKey(
        Earner,
        on_delete=models.CASCADE,
        db_column='earner_id'
    )
    income_frequency = models.ForeignKey(
        IncomeFrequency,
        on_delete=models.SET_NULL,
        null=True,
        db_column='income_frequency_id'
    )
    range_amount = models.CharField(max_length=100)
    income_startdate = models.DateField()
    next_payday = models.DateField()

    class Meta:
        db_table = 'income'

    def __str__(self):
        return f"Income of {self.earner}"


class ItemCategory(models.Model):
    category_id = models.AutoField(primary_key=True)
    category_desc = models.CharField(max_length=100)

    class Meta:
        db_table = 'item_category'

    def __str__(self):
        return self.category_desc


class BudgetItem(models.Model):
    item_id = models.AutoField(primary_key=True)
    category = models.ForeignKey(
        ItemCategory,
        on_delete=models.SET_NULL,
        null=True,
        db_column='category_id'
    )
    item_desc = models.CharField(max_length=255)
    due_day = models.IntegerField()
    grace_period_days = models.IntegerField(default=0)
    penalty_classification = models.BooleanField(default=False)

    class Meta:
        db_table = 'budget_item'

    def __str__(self):
        return self.item_desc


class BudgetAllocation(models.Model):
    PRIORITY_CHOICES = [
        ('High', 'High'),
        ('Medium', 'Medium'),
        ('Low', 'Low'),
    ]
    CLASSIFICATION_CHOICES = [
        ('Non-deferrable', 'Non-deferrable'),
        ('Deferrable', 'Deferrable'),
    ]
    RISK_CHOICES = [
        ('Stable', 'Stable'),
        ('At Risk', 'At Risk'),
        ('Critical', 'Critical'),
    ]
    PERIOD_CHOICES = [
        ('1st Half', '1st Half'),
        ('2nd Half', '2nd Half'),
        ('Full Month', 'Full Month'),
    ]

    budget_allocation_id = models.AutoField(primary_key=True)
    income = models.ForeignKey(
        Income,
        on_delete=models.CASCADE,
        db_column='income_id'
    )
    item = models.ForeignKey(
        BudgetItem,
        on_delete=models.CASCADE,
        db_column='item_id'
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    actual_due_date = models.DateField(null=True, blank=True)
    image_path = models.CharField(max_length=500, null=True, blank=True)
    scan_date = models.DateField(null=True, blank=True)
    is_confirmed = models.BooleanField(default=False)
    budget_amount_range = models.CharField(max_length=100)
    budget_start_date = models.DateField()
    budget_end_date = models.DateField()
    budget_classification = models.CharField(
        max_length=20,
        choices=CLASSIFICATION_CHOICES,
        null=True,
        blank=True
    )
    priority_level = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        null=True,
        blank=True
    )
    period_half = models.CharField(
        max_length=20,
        choices=PERIOD_CHOICES,
        null=True,
        blank=True
    )
    bill_reminder = models.BooleanField(default=False)

    class Meta:
        db_table = 'budget_allocation'

    def __str__(self):
        return f"Allocation for {self.item} - {self.priority_level}"