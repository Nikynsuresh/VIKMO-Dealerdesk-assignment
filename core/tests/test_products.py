from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import Product


class ProductAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.p1 = Product.objects.create(
            sku="BRK-9001",
            name="Endurance Brake Pad Pulsar",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1450.00"),
            stock=40,
            description="High quality brake pads",
        )
        self.p2 = Product.objects.create(
            sku="FIL-9002",
            name="Bosch Oil Filter Duke",
            category="Filters",
            brand="Bosch",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("450.50"),
            stock=15,
            description="Premium oil filter",
        )

    def test_list_products_pagination(self):
        resp = self.client.get("/api/products/?page_size=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["results"]), 1)
        self.assertIn("next", data)

    def test_search_and_filters(self):
        # Search by brand
        resp = self.client.get("/api/products/?search=Bosch")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sku"], "FIL-9002")

        # Filter by category
        resp = self.client.get("/api/products/?category=Brakes")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sku"], "BRK-9001")

        # Filter by vehicle
        resp = self.client.get("/api/products/?vehicle=Pulsar")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sku"], "BRK-9001")

    def test_create_product_success(self):
        payload = {
            "sku": "IGN-9003",
            "name": "NGK Spark Plug",
            "category": "Electrical",
            "brand": "NGK",
            "vehicle_fitment": "Universal",
            "price": "320.00",
            "description": "Standard spark plug",
        }
        resp = self.client.post("/api/products/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["sku"], "IGN-9003")
        self.assertEqual(data["stock"], 0)  # Initialized to stock 0
        self.assertEqual(data["price"], "320.00")

    def test_create_duplicate_sku_returns_409(self):
        payload = {
            "sku": "BRK-9001",
            "name": "Duplicate Item",
            "category": "Brakes",
            "brand": "Endurance",
            "vehicle_fitment": "Universal",
            "price": "100.00",
        }
        resp = self.client.post("/api/products/", payload, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "DUPLICATE")

    def test_create_invalid_price_returns_400(self):
        payload = {
            "sku": "IGN-9004",
            "name": "Invalid Price Item",
            "category": "Electrical",
            "brand": "NGK",
            "vehicle_fitment": "Universal",
            "price": "-50.00",  # Negative
        }
        resp = self.client.post("/api/products/", payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_get_product_detail_and_404(self):
        resp = self.client.get("/api/products/BRK-9001/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sku"], "BRK-9001")

        resp404 = self.client.get("/api/products/NON-EXISTENT/")
        self.assertEqual(resp404.status_code, 404)
        self.assertEqual(resp404.json()["error"]["code"], "NOT_FOUND")

    def test_patch_product(self):
        resp = self.client.patch("/api/products/BRK-9001/", {"price": "1599.99"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["price"], "1599.99")

        # Disallow SKU change
        resp_bad = self.client.patch("/api/products/BRK-9001/", {"sku": "CHANGED"}, format="json")
        self.assertEqual(resp_bad.status_code, 400)
        self.assertEqual(resp_bad.json()["error"]["code"], "VALIDATION_ERROR")
