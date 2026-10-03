from django.db import models


class Household(models.Model):
    household_id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=255)
    total_members = models.IntegerField(default=0)
    no_of_earners = models.IntegerField(default=0)
    no_of_dependents = models.IntegerField(default=0)  # = no_of_children + no_of_seniors
    no_of_children = models.IntegerField(default=0)
    no_of_seniors = models.IntegerField(default=0)
    housing_type = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100, default='Cagayan de Oro')
    daily_food_expense_min = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_food_expense_max = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_transport_expense_min = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    daily_transport_expense_max = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    survival_threshold_min = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    survival_threshold_max = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)
    setup_completed = models.BooleanField(default=False)

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
    next_payday = models.DateField()  # last computed value; the schedule below is the source of truth
    payday_weekday = models.PositiveSmallIntegerField(null=True, blank=True)  # weekly: 0=Mon..6=Sun
    payday_day_1 = models.PositiveSmallIntegerField(null=True, blank=True)  # monthly / twice a month (31 = end of month)
    payday_day_2 = models.PositiveSmallIntegerField(null=True, blank=True)  # twice a month only

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


class Biller(models.Model):
    """A company the household pays. Holds the late-payment rules so users never have to enter them."""
    biller_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=100)  # an ItemCategory description, e.g. 'Electricity'
    city = models.CharField(max_length=100, default='Cagayan de Oro')  # or 'Nationwide'
    keywords = models.CharField(max_length=255, blank=True, default='')  # comma separated, used to detect it on a scanned bill
    grace_period_days = models.IntegerField(default=0)
    has_penalty = models.BooleanField(default=True)
    rules_verified = models.BooleanField(default=False)  # True once the rules were checked on the biller's website

    class Meta:
        db_table = 'biller'

    def __str__(self):
        return self.name


class BudgetItem(models.Model):
    item_id = models.AutoField(primary_key=True)
    category = models.ForeignKey(
        ItemCategory,
        on_delete=models.SET_NULL,
        null=True,
        db_column='category_id'
    )
    item_desc = models.CharField(max_length=255)
    due_day = models.IntegerField(null=True, blank=True)
    grace_period_days = models.IntegerField(default=0)
    penalty_classification = models.BooleanField(default=False)
    biller = models.ForeignKey(
        Biller,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='biller_id'
    )
    reminder_day = models.PositiveSmallIntegerField(null=True, blank=True)  # "remind me every month on day N" (31 = end of month)
    is_daily = models.BooleanField(default=False)  # amounts are per day; stored as a monthly equivalent (x30)

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

    actual_due_date = models.DateField(
        null=True,
        blank=True
    )

    image_path = models.CharField(
        max_length=500,
        null=True,
        blank=True
    )

    scan_date = models.DateField(
        null=True,
        blank=True
    )

    is_confirmed = models.BooleanField(
        default=False
    )

    budget_amount_range = models.CharField(
        max_length=100
    )

    budget_start_date = models.DateField(null=True, blank=True)
    budget_end_date = models.DateField(null=True, blank=True)

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

    # ---- Rule engine output ----
    # Which rule (Rule 1, Rule 2, Rule 3, Rule 4a, Rule 4b) classified this bill.
    # Used by the frontend to display the exact reason text.
    rule_applied = models.CharField(
        max_length=50,
        null=True,
        blank=True
    )

    period_half = models.CharField(
        max_length=20,
        choices=PERIOD_CHOICES,
        null=True,
        blank=True
    )

    bill_reminder = models.BooleanField(
        default=False
    )

    # ---- Payment status ----
    is_paid = models.BooleanField(
        default=False
    )

    paid_date = models.DateField(
        null=True,
        blank=True
    )

    class Meta:
        db_table = 'budget_allocation'

    def __str__(self):
        return f"Allocation for {self.item} - {self.priority_level}"