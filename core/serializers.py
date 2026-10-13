from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers
from .models import Product, Dealer, Order, OrderItem


def format_money(val) -> str:
    if val is None:
        return "0.00"
    if isinstance(val, (int, float, str)):
        val = Decimal(str(val))
    return f"{val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


class ProductSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "stock",
            "description",
        ]

    def get_price(self, obj) -> str:
        return format_money(obj.price)


class ProductCreateSerializer(serializers.ModelSerializer):
    price = serializers.CharField()

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "description",
        ]

    def validate_price(self, value):
        try:
            d = Decimal(str(value))
        except Exception:
            raise serializers.ValidationError("Price must be a valid number.")
        if d <= Decimal("0"):
            raise serializers.ValidationError("Price must be greater than zero.")
        # Check decimals
        str_val = str(value)
        if "." in str_val and len(str_val.split(".")[1]) > 2:
            raise serializers.ValidationError("Price can have at most 2 decimal places.")
        return d


class DealerSerializer(serializers.ModelSerializer):
    credit_limit = serializers.SerializerMethodField()

    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]

    def get_credit_limit(self, obj) -> str:
        return format_money(obj.credit_limit)


class DealerCreateSerializer(serializers.ModelSerializer):
    dealer_code = serializers.CharField(required=True)
    name = serializers.CharField(required=True)
    credit_limit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=Decimal("0.00"))

    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]


class OrderItemSerializer(serializers.ModelSerializer):
    unit_price = serializers.SerializerMethodField()
    line_subtotal = serializers.SerializerMethodField()
    line_tax = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "sku",
            "name",
            "quantity",
            "unit_price",
            "line_subtotal",
            "line_tax",
            "line_total",
        ]

    def get_unit_price(self, obj) -> str:
        return format_money(obj.unit_price)

    def get_line_subtotal(self, obj) -> str:
        return format_money(obj.line_subtotal)

    def get_line_tax(self, obj) -> str:
        return format_money(obj.line_tax)

    def get_line_total(self, obj) -> str:
        return format_money(obj.line_total)


class OrderSerializer(serializers.ModelSerializer):
    dealer_id = serializers.IntegerField(source="dealer.id")
    items = OrderItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    tax_total = serializers.SerializerMethodField()
    grand_total = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number",
            "dealer_id",
            "status",
            "items",
            "subtotal",
            "tax_total",
            "grand_total",
            "created_at",
            "updated_at",
        ]

    def get_subtotal(self, obj) -> str:
        return format_money(obj.subtotal)

    def get_tax_total(self, obj) -> str:
        return format_money(obj.tax_total)

    def get_grand_total(self, obj) -> str:
        return format_money(obj.grand_total)

    def get_created_at(self, obj) -> str:
        return obj.created_at.isoformat()

    def get_updated_at(self, obj) -> str:
        return obj.updated_at.isoformat()
