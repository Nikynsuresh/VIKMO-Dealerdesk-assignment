import os
import csv
import math
import numpy as np
import pandas as pd
from django.conf import settings
from .models import Product
from .exceptions import NotFoundError

_forecast_cache = None
_lead_times_cache = None
_sales_std_cache = None


def load_forecast_data():
    global _forecast_cache
    if _forecast_cache is not None:
        return _forecast_cache

    base_dir = settings.BASE_DIR
    forecast_file = base_dir / "ml" / "forecast.csv"
    data = {}
    if os.path.exists(forecast_file):
        with open(forecast_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                sku = r["sku"].strip()
                if sku not in data:
                    data[sku] = []
                data[sku].append({
                    "week_start": r["week_start"],
                    "forecast_units": float(r["forecast_units"]),
                })
    _forecast_cache = data
    return _forecast_cache


def load_lead_times():
    global _lead_times_cache
    if _lead_times_cache is not None:
        return _lead_times_cache

    base_dir = settings.BASE_DIR
    lead_file = base_dir / "data" / "lead_times.csv"
    data = {}
    if os.path.exists(lead_file):
        with open(lead_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                sku = r["sku"].strip()
                data[sku] = int(r["lead_time_weeks"])
    _lead_times_cache = data
    return _lead_times_cache


def get_sales_sigmas():
    """Calculates sample standard deviation (ddof=1) over the most recent 12 weeks of sales."""
    global _sales_std_cache
    if _sales_std_cache is not None:
        return _sales_std_cache

    base_dir = settings.BASE_DIR
    sales_file = base_dir / "data" / "sales_history.csv"
    data = {}
    if os.path.exists(sales_file):
        df_sales = pd.read_csv(sales_file)
        for sku, group in df_sales.groupby("sku"):
            recent = group.sort_values("week_start").tail(12)
            vals = recent["units_sold"].values
            if len(vals) > 1:
                sigma = float(np.std(vals, ddof=1))
            elif len(vals) == 1:
                sigma = 0.0
            else:
                sigma = 0.0
            data[sku] = sigma

    _sales_std_cache = data
    return _sales_std_cache


def get_sku_forecast(sku: str) -> dict:
    forecast_map = load_forecast_data()
    if sku not in forecast_map:
        raise NotFoundError(f"Forecast for SKU '{sku}' not found.")
    return {
        "sku": sku,
        "forecast": forecast_map[sku],
    }


def get_reorder_suggestions() -> list:
    forecast_map = load_forecast_data()
    lead_times = load_lead_times()
    sigmas = get_sales_sigmas()

    # Get live stock from database
    live_products = {p.sku: p.stock for p in Product.objects.filter(sku__in=lead_times.keys())}

    results = []
    # Retain lead_times ordering
    for sku, L in lead_times.items():
        current_stock = live_products.get(sku, 0)
        sku_forecast = forecast_map.get(sku, [])

        # lead_time_demand = sum of forecast_units for the first L forecast weeks
        first_l_weeks = sku_forecast[:L]
        lead_time_demand = round(sum(w["forecast_units"] for w in first_l_weeks), 1)

        # sigma: sample stddev (ddof=1)
        sigma = sigmas.get(sku, 0.0)

        # safety_stock = ceil(1.65 * sigma * sqrt(L))
        safety_stock = math.ceil(1.65 * sigma * math.sqrt(L))

        # reorder_point = lead_time_demand + safety_stock
        reorder_point = round(lead_time_demand + safety_stock, 1)

        # reorder_qty = max(0, ceil(reorder_point - current_stock))
        reorder_qty = max(0, math.ceil(reorder_point - current_stock))

        results.append({
            "sku": sku,
            "current_stock": current_stock,
            "lead_time_weeks": L,
            "lead_time_demand": lead_time_demand,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "reorder_qty": reorder_qty,
        })

    return results
