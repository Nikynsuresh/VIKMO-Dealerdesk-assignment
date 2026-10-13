import time
import requests
from decimal import Decimal
from datetime import datetime
from django.conf import settings
from django.db import transaction
from .models import Product
from .exceptions import SupplierUnavailableError
from .mongodb import log_supplier_sync, mirror_product


def validate_supplier_item(item):
    """
    Check if item has non-empty sku and price is a positive amount with at most 2 decimals.
    Returns (is_valid, reason).
    """
    sku = item.get("sku")
    if not sku or not isinstance(sku, str) or not sku.strip():
        return False, "missing or empty sku"

    price_val = item.get("price")
    if price_val is None:
        return False, "missing price"

    try:
        price_str = str(price_val).strip()
        d_price = Decimal(price_str)
        if d_price <= Decimal("0"):
            return False, "invalid price"
        if "." in price_str and len(price_str.split(".")[1]) > 2:
            return False, "invalid price"
    except Exception:
        return False, "invalid price"

    return True, None


def run_supplier_sync():
    """
    Fetches the supplier catalog using cursor pagination and retries.
    Returns sync metrics dictionary or raises SupplierUnavailableError.
    """
    base_url = getattr(settings, "SUPPLIER_API_URL", "http://127.0.0.1:8099").rstrip("/")
    api_key = getattr(settings, "SUPPLIER_API_KEY", "")

    url = f"{base_url}/v1/products"
    headers = {"X-Api-Key": api_key}

    pages_fetched = 0
    requests_made = 0
    raw_items = []
    cursor = None

    session = requests.Session()

    while True:
        params = {"limit": 20}
        if cursor is not None and cursor != "":
            params["cursor"] = cursor

        success = False
        attempts = 0

        while attempts < 5:
            attempts += 1
            requests_made += 1

            try:
                resp = session.get(url, headers=headers, params=params, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    pages_fetched += 1
                    items = data.get("items", [])
                    raw_items.extend(items)
                    cursor = data.get("next_cursor")
                    success = True
                    break
                elif resp.status_code == 401:
                    # Key rejected: abort immediately
                    raise SupplierUnavailableError("Supplier API key was rejected.")
                elif resp.status_code == 429:
                    retry_after_hdr = resp.headers.get("Retry-After")
                    wait_seconds = 1
                    if retry_after_hdr:
                        try:
                            wait_seconds = max(1, int(float(retry_after_hdr)))
                        except Exception:
                            wait_seconds = 1
                    time.sleep(wait_seconds)
                elif 500 <= resp.status_code < 600:
                    # Transient 5xx error
                    backoff = 0.5 * (2 ** (attempts - 1))
                    time.sleep(backoff)
                else:
                    # Other status code (e.g. 400, 404)
                    backoff = 0.5 * (2 ** (attempts - 1))
                    time.sleep(backoff)

            except (requests.RequestException, Exception) as e:
                if isinstance(e, SupplierUnavailableError):
                    raise
                backoff = 0.5 * (2 ** (attempts - 1))
                time.sleep(backoff)

        if not success:
            raise SupplierUnavailableError(f"Failed to fetch page after 5 attempts.")

        if cursor is None:
            break

    # Process items
    rejected = []
    valid_items = []

    for item in raw_items:
        is_valid, reason = validate_supplier_item(item)
        if not is_valid:
            rejected.append({
                "supplier_sku": item.get("supplier_sku", "UNKNOWN"),
                "reason": reason,
            })
        else:
            valid_items.append(item)

    # Deduplicate by SKU: use latest updated_at
    latest_by_sku = {}
    for item in valid_items:
        sku = item["sku"].strip()
        updated_at_str = item.get("updated_at", "")
        try:
            # Handle ISO string e.g. 2026-10-08T09:15:00Z
            updated_dt = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
        except Exception:
            updated_dt = datetime.min

        if sku not in latest_by_sku:
            latest_by_sku[sku] = (updated_dt, item)
        else:
            if updated_dt > latest_by_sku[sku][0]:
                latest_by_sku[sku] = (updated_dt, item)

    unique_items = [v[1] for v in latest_by_sku.values()]

    created_count = 0
    updated_count = 0
    unchanged_count = 0

    # Apply atomically to database
    with transaction.atomic():
        existing_products = {p.sku: p for p in Product.objects.filter(sku__in=latest_by_sku.keys())}
        new_products = []
        products_to_update = []

        for item in unique_items:
            sku = item["sku"].strip()
            name = item.get("name", "")
            category = item.get("category", "")
            brand = item.get("brand", "")
            vehicle_fitment = item.get("vehicle_fitment", "")
            description = item.get("description", "")
            price = Decimal(str(item.get("price"))).quantize(Decimal("0.01"))

            if sku not in existing_products:
                # Create with stock 0
                p = Product(
                    sku=sku,
                    name=name,
                    category=category,
                    brand=brand,
                    vehicle_fitment=vehicle_fitment,
                    description=description,
                    price=price,
                    stock=0,
                )
                new_products.append(p)
                created_count += 1
            else:
                p = existing_products[sku]
                differs = (
                    p.name != name or
                    p.category != category or
                    p.brand != brand or
                    p.vehicle_fitment != vehicle_fitment or
                    p.description != description or
                    p.price != price
                )
                if differs:
                    p.name = name
                    p.category = category
                    p.brand = brand
                    p.vehicle_fitment = vehicle_fitment
                    p.description = description
                    p.price = price
                    products_to_update.append(p)
                    updated_count += 1
                else:
                    unchanged_count += 1

        if new_products:
            Product.objects.bulk_create(new_products)
        if products_to_update:
            Product.objects.bulk_update(
                products_to_update,
                fields=["name", "category", "brand", "vehicle_fitment", "description", "price"]
            )

    result = {
        "created": created_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "rejected": rejected,
        "pages_fetched": pages_fetched,
        "requests_made": requests_made,
    }

    log_supplier_sync(result)
    return result
