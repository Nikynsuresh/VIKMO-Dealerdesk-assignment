from django.db import models
from django.utils import timezone


class Product(models.Model):
    sku = models.CharField(max_length=64, unique=True, primary_key=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=128)
    brand = models.CharField(max_length=128)
    vehicle_fitment = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField(default=0)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        ordering = ["sku"]

    def __str__(self):
        return f"{self.sku} - {self.name}"


class Dealer(models.Model):
    dealer_code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=128, blank=True, default="")
    phone = models.CharField(max_length=64, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    gstin = models.CharField(max_length=64, blank=True, default="")
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dealers"
        ordering = ["id"]

    def __str__(self):
        return f"{self.dealer_code} - {self.name}"


class Order(models.Model):
    STATUS_DRAFT = "DRAFT"
    STATUS_CONFIRMED = "CONFIRMED"
    STATUS_DISPATCHED = "DISPATCHED"
    STATUS_DELIVERED = "DELIVERED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "DRAFT"),
        (STATUS_CONFIRMED, "CONFIRMED"),
        (STATUS_DISPATCHED, "DISPATCHED"),
        (STATUS_DELIVERED, "DELIVERED"),
        (STATUS_CANCELLED, "CANCELLED"),
    ]

    order_number = models.CharField(max_length=64, unique=True, primary_key=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    line_tax = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        db_table = "order_items"
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.order_number} - {self.sku} x {self.quantity}"


class OrderDailySequence(models.Model):
    date_str = models.CharField(max_length=8, unique=True)  # YYYYMMDD in Asia/Kolkata
    last_seq = models.IntegerField(default=0)

    class Meta:
        db_table = "order_daily_sequences"


class IdempotencyRecord(models.Model):
    key = models.CharField(max_length=255, unique=True)
    request_hash = models.CharField(max_length=64)
    order_number = models.CharField(max_length=64)
    response_data = models.JSONField()
    status_code = models.IntegerField(default=201)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_records"


class InventoryAdjustment(models.Model):
    sku = models.CharField(max_length=64)
    quantity = models.IntegerField()
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_adjustments"


class AssistantSession(models.Model):
    session_id = models.CharField(max_length=128, unique=True, primary_key=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.CASCADE, related_name="assistant_sessions")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assistant_sessions"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Session {self.session_id} (Dealer: {self.dealer_id})"


class AssistantTurn(models.Model):
    session = models.ForeignKey(AssistantSession, on_delete=models.CASCADE, related_name="turns")
    user_message = models.TextField()
    reply = models.TextField()
    cited_skus = models.JSONField(default=list)
    tool_calls = models.JSONField(default=list)
    actions = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "assistant_turns"
        ordering = ["created_at"]

    def __str__(self):
        return f"Turn in {self.session_id} at {self.created_at}"
