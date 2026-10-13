from decimal import Decimal
from django.test import TestCase
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from core.models import Product, Dealer, Order, OrderItem, AssistantSession, AssistantTurn, OrderDailySequence, IdempotencyRecord


class ModelTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-TEST-001",
            name="Apex Motors Bangalore",
            city="Bengaluru",
            credit_limit=Decimal("50000.00"),
        )
        self.product = Product.objects.create(
            sku="TST-1001",
            name="Performance Brake Pads",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("1250.50"),
            stock=25,
            description="High friction brake pads",
        )

    def test_product_constraints(self):
        self.assertEqual(str(self.product), "TST-1001 - Performance Brake Pads")
        # Duplicate SKU must fail
        with self.assertRaises(IntegrityError):
            Product.objects.create(
                sku="TST-1001",
                name="Duplicate SKU product",
                category="Brakes",
                brand="Brembo",
                vehicle_fitment="Universal",
                price=Decimal("999.00"),
                stock=10,
            )

    def test_dealer_constraints(self):
        self.assertEqual(str(self.dealer), "DLR-TEST-001 - Apex Motors Bangalore")
        # Duplicate dealer_code must fail
        with self.assertRaises(IntegrityError):
            Dealer.objects.create(
                dealer_code="DLR-TEST-001",
                name="Duplicate Dealer Code",
            )

    def test_order_and_items_integrity(self):
        order = Order.objects.create(
            order_number="ORD-20261013-0001",
            dealer=self.dealer,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal("2501.00"),
            tax_total=Decimal("450.18"),
            grand_total=Decimal("2951.18"),
        )
        item = OrderItem.objects.create(
            order=order,
            product=self.product,
            sku=self.product.sku,
            name=self.product.name,
            quantity=2,
            unit_price=self.product.price,
            line_subtotal=Decimal("2501.00"),
            line_tax=Decimal("450.18"),
            line_total=Decimal("2951.18"),
        )
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(item.quantity, 2)

        # Deleting product with existing order items must be PROTECTED
        with self.assertRaises(ProtectedError):
            self.product.delete()

    def test_assistant_session_and_turn(self):
        session = AssistantSession.objects.create(
            session_id="sess-xyz-789",
            dealer=self.dealer,
        )
        turn = AssistantTurn.objects.create(
            session=session,
            user_message="Do you have brake pads?",
            reply="Yes, we have TST-1001 in stock.",
            cited_skus=["TST-1001"],
            tool_calls=[{"name": "search_parts", "arguments": {"query": "brake pads"}}],
            actions=[],
        )
        self.assertEqual(session.turns.count(), 1)
        self.assertEqual(session.turns.first().user_message, "Do you have brake pads?")
