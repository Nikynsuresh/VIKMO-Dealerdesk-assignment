import math
import csv
import os
from decimal import Decimal
from django.test import TestCase
from django.conf import settings
from rest_framework.test import APIClient
from core.models import Product


class ForecastAndReorderTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_forecast_file_integrity(self):
        forecast_path = settings.BASE_DIR / "ml" / "forecast.csv"
        self.assertTrue(os.path.exists(forecast_path), "ml/forecast.csv must exist")

        with open(forecast_path, encoding="utf-8") as f:
            reader = list(csv.DictReader(f))

        self.assertEqual(len(reader), 160, "ml/forecast.csv must contain exactly 160 predictions")
        weeks = {"2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"}
        for r in reader:
            self.assertIn(r["week_start"], weeks)
            units = float(r["forecast_units"])
            self.assertGreaterEqual(units, 0.0, "forecast_units must be non-negative")

    def test_forecast_api_endpoint(self):
        # Pick first forecast SKU from lead_times.csv
        lead_file = settings.DATA_DIR / "lead_times.csv"
        with open(lead_file, encoding="utf-8") as f:
            first_sku = next(csv.DictReader(f))["sku"].strip()

        resp = self.client.get(f"/api/forecast/{first_sku}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["sku"], first_sku)
        self.assertEqual(len(data["forecast"]), 4)

        # 404 on non-existent SKU
        resp404 = self.client.get("/api/forecast/UNKNOWN-SKU/")
        self.assertEqual(resp404.status_code, 404)
        self.assertEqual(resp404.json()["error"]["code"], "NOT_FOUND")

    def test_reorder_suggestions_exact_formula(self):
        resp = self.client.get("/api/reorder-suggestions/")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 40)

        for entry in results:
            L = entry["lead_time_weeks"]
            ltd = entry["lead_time_demand"]
            ss = entry["safety_stock"]
            rp = entry["reorder_point"]
            rq = entry["reorder_qty"]
            stock = entry["current_stock"]

            # reorder_point = lead_time_demand + safety_stock
            self.assertAlmostEqual(rp, round(ltd + ss, 1), places=1)

            # reorder_qty = max(0, ceil(reorder_point - current_stock))
            expected_rq = max(0, math.ceil(rp - stock))
            self.assertEqual(rq, expected_rq)
