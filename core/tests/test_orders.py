from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import Product, Dealer, Order, OrderItem


class OrderAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-099",
            name="Apex Motors",
            credit_limit=Decimal("50000.00"),
        )
        self.p1 = Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50"),
            stock=10,
        )
        self.p2 = Product.objects.create(
            sku="FIL-1012",
            name="Oil Filter",
            category="Filters",
            brand="Bosch",
            vehicle_fitment="Universal",
            price=Decimal("250.00"),
            stock=5,
        )

    def test_create_draft_order_pricing_and_gst(self):
        # 3 units of BRK-1044:
        # unit_price = 1459.50
        # line_subtotal = 3 * 1459.50 = 4378.50
        # line_tax = 4378.50 * 0.18 = 788.13 (rounded half up)
        # line_total = 4378.50 + 788.13 = 5166.63
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 3}],
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()

        self.assertEqual(data["status"], "DRAFT")
        self.assertEqual(data["subtotal"], "4378.50")
        self.assertEqual(data["tax_total"], "788.13")
        self.assertEqual(data["grand_total"], "5166.63")
        self.assertTrue(data["order_number"].startswith("ORD-"))

        # DRAFT has no effect on live stock
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.stock, 10)

    def test_idempotency_key_behavior(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 1}],
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "idem-key-12345"}

        # First request creates order
        resp1 = self.client.post("/api/orders/", payload, format="json", **headers)
        self.assertEqual(resp1.status_code, 201)
        ord_num = resp1.json()["order_number"]

        # Duplicate request with same payload returns original order
        resp2 = self.client.post("/api/orders/", payload, format="json", **headers)
        self.assertIn(resp2.status_code, [200, 201])
        self.assertEqual(resp2.json()["order_number"], ord_num)

        # Reusing same key with DIFFERENT payload must return 409 IDEMPOTENCY_CONFLICT
        different_payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 2}],
        }
        resp3 = self.client.post("/api/orders/", different_payload, format="json", **headers)
        self.assertEqual(resp3.status_code, 409)
        self.assertEqual(resp3.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_order_lifecycle_confirm_cancel(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 4}],
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        ord_num = resp.json()["order_number"]

        # Confirm order: deducts stock from 10 to 6
        resp_conf = self.client.post(f"/api/orders/{ord_num}/confirm/")
        self.assertEqual(resp_conf.status_code, 200)
        self.assertEqual(resp_conf.json()["status"], "CONFIRMED")

        self.p1.refresh_from_db()
        self.assertEqual(self.p1.stock, 6)

        # Cancel confirmed order: restores stock back to 10 exactly once
        resp_canc = self.client.post(f"/api/orders/{ord_num}/cancel/")
        self.assertEqual(resp_canc.status_code, 200)
        self.assertEqual(resp_canc.json()["status"], "CANCELLED")

        self.p1.refresh_from_db()
        self.assertEqual(self.p1.stock, 10)

        # Cannot confirm a cancelled order (invalid transition)
        resp_invalid = self.client.post(f"/api/orders/{ord_num}/confirm/")
        self.assertEqual(resp_invalid.status_code, 409)
        self.assertEqual(resp_invalid.json()["error"]["code"], "INVALID_TRANSITION")

    def test_insufficient_stock_error_on_confirm(self):
        # Order more than available stock (request 15 when only 10 available)
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 15}],
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        ord_num = resp.json()["order_number"]

        resp_conf = self.client.post(f"/api/orders/{ord_num}/confirm/")
        self.assertEqual(resp_conf.status_code, 409)
        err = resp_conf.json()["error"]
        self.assertEqual(err["code"], "INSUFFICIENT_STOCK")
        self.assertEqual(len(err["details"]), 1)
        self.assertEqual(err["details"][0]["requested"], 15)
        self.assertEqual(err["details"][0]["available"], 10)
        self.assertIn("Insufficient stock", err["message"])

        # Stock remains untouched
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.stock, 10)
