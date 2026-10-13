import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import Product, Dealer, Order, OrderItem, OrderDailySequence, IdempotencyRecord, InventoryAdjustment
from .exceptions import (
    ValidationError,
    NotFoundError,
    InsufficientStockError,
    InvalidTransitionError,
    OrderLockedError,
    IdempotencyConflictError,
)
from .mongodb import mirror_order, mirror_product, log_audit_event


KOLKATA_TZ = ZoneInfo("Asia/Kolkata")
TAX_RATE = Decimal("0.18")


def get_kolkata_today_str() -> str:
    now = datetime.now(KOLKATA_TZ)
    return now.strftime("%Y%m%d")


def generate_order_number() -> str:
    """Generate ORD-YYYYMMDD-NNNN unique sequence in Asia/Kolkata timezone with concurrency lock."""
    date_str = get_kolkata_today_str()
    with transaction.atomic():
        seq_obj, _ = OrderDailySequence.objects.select_for_update().get_or_create(
            date_str=date_str,
            defaults={"last_seq": 0},
        )
        seq_obj.last_seq += 1
        seq_obj.save(update_fields=["last_seq"])
        seq_num = seq_obj.last_seq

    return f"ORD-{date_str}-{seq_num:04d}"


def calculate_line_item(unit_price: Decimal, quantity: int):
    """
    line_subtotal = unit_price * quantity
    line_tax = line_subtotal * 0.18, rounded to 2 decimals using ROUND_HALF_UP (0.005 -> 0.01)
    line_total = line_subtotal + line_tax
    """
    line_subtotal = (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    line_tax = (line_subtotal * TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    line_total = (line_subtotal + line_tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return line_subtotal, line_tax, line_total


def validate_order_items(items_raw):
    if not items_raw or not isinstance(items_raw, list):
        raise ValidationError("Order must have at least one item.")
    if len(items_raw) == 0:
        raise ValidationError("Order must have at least one item.")

    seen_skus = set()
    cleaned_items = []

    for idx, item in enumerate(items_raw):
        if not isinstance(item, dict):
            raise ValidationError(f"Item at index {idx} must be an object.")
        sku = item.get("sku")
        qty = item.get("quantity")

        if not sku or not isinstance(sku, str):
            raise ValidationError(f"Item at index {idx} has invalid or missing SKU.")
        sku = sku.strip()

        if sku in seen_skus:
            raise ValidationError(f"Duplicate SKU '{sku}' in order items.")
        seen_skus.add(sku)

        if qty is None or isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValidationError(f"Quantity for SKU '{sku}' must be an integer >= 1.")

        cleaned_items.append({"sku": sku, "quantity": qty})

    # Validate that every SKU exists
    existing_products = {p.sku: p for p in Product.objects.filter(sku__in=seen_skus)}
    for item in cleaned_items:
        sku = item["sku"]
        if sku not in existing_products:
            raise ValidationError(f"SKU '{sku}' does not exist.")
        item["product"] = existing_products[sku]

    return cleaned_items


def hash_request_payload(data) -> str:
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def create_order_service(dealer_id, items_raw, idempotency_key=None):
    request_data = {"dealer_id": dealer_id, "items": items_raw}
    request_hash = hash_request_payload(request_data)

    if idempotency_key:
        try:
            record = IdempotencyRecord.objects.get(key=idempotency_key)
            if record.request_hash == request_hash:
                return record.response_data, record.status_code
            else:
                raise IdempotencyConflictError("Idempotency key reused with different request payload.")
        except IdempotencyRecord.DoesNotExist:
            pass

    # Validate dealer
    try:
        dealer = Dealer.objects.get(id=dealer_id)
    except (Dealer.DoesNotExist, ValueError, TypeError):
        raise ValidationError(f"Dealer with id {dealer_id} does not exist.")

    # Validate items
    validated_items = validate_order_items(items_raw)

    with transaction.atomic():
        order_number = generate_order_number()
        order = Order.objects.create(
            order_number=order_number,
            dealer=dealer,
            status=Order.STATUS_DRAFT,
            created_at=datetime.now(KOLKATA_TZ),
        )

        subtotal_sum = Decimal("0.00")
        tax_total_sum = Decimal("0.00")
        grand_total_sum = Decimal("0.00")

        order_items_to_create = []
        for v in validated_items:
            product = v["product"]
            unit_price = product.price
            l_subtotal, l_tax, l_total = calculate_line_item(unit_price, v["quantity"])

            subtotal_sum += l_subtotal
            tax_total_sum += l_tax
            grand_total_sum += l_total

            order_items_to_create.append(
                OrderItem(
                    order=order,
                    product=product,
                    sku=product.sku,
                    name=product.name,
                    quantity=v["quantity"],
                    unit_price=unit_price,
                    line_subtotal=l_subtotal,
                    line_tax=l_tax,
                    line_total=l_total,
                )
            )

        OrderItem.objects.bulk_create(order_items_to_create)

        order.subtotal = subtotal_sum
        order.tax_total = tax_total_sum
        order.grand_total = grand_total_sum
        order.save(update_fields=["subtotal", "tax_total", "grand_total"])

        from .serializers import OrderSerializer
        response_data = OrderSerializer(order).data

        if idempotency_key:
            IdempotencyRecord.objects.create(
                key=idempotency_key,
                request_hash=request_hash,
                order_number=order_number,
                response_data=response_data,
                status_code=201,
            )

    mirror_order(response_data)
    log_audit_event("ORDER_CREATED", {"order_number": order_number, "dealer_id": dealer.id})
    return response_data, 201


def update_order_items_service(order_number: str, items_raw):
    try:
        order = Order.objects.get(order_number=order_number)
    except Order.DoesNotExist:
        raise NotFoundError(f"Order '{order_number}' not found.")

    if order.status != Order.STATUS_DRAFT:
        raise OrderLockedError("Only DRAFT orders can be edited.")

    validated_items = validate_order_items(items_raw)

    with transaction.atomic():
        order.items.all().delete()

        subtotal_sum = Decimal("0.00")
        tax_total_sum = Decimal("0.00")
        grand_total_sum = Decimal("0.00")

        order_items_to_create = []
        for v in validated_items:
            product = v["product"]
            unit_price = product.price
            l_subtotal, l_tax, l_total = calculate_line_item(unit_price, v["quantity"])

            subtotal_sum += l_subtotal
            tax_total_sum += l_tax
            grand_total_sum += l_total

            order_items_to_create.append(
                OrderItem(
                    order=order,
                    product=product,
                    sku=product.sku,
                    name=product.name,
                    quantity=v["quantity"],
                    unit_price=unit_price,
                    line_subtotal=l_subtotal,
                    line_tax=l_tax,
                    line_total=l_total,
                )
            )

        OrderItem.objects.bulk_create(order_items_to_create)

        order.subtotal = subtotal_sum
        order.tax_total = tax_total_sum
        order.grand_total = grand_total_sum
        order.save(update_fields=["subtotal", "tax_total", "grand_total", "updated_at"])

    from .serializers import OrderSerializer
    res = OrderSerializer(order).data
    mirror_order(res)
    return res


def confirm_order_service(order_number: str):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise NotFoundError(f"Order '{order_number}' not found.")

        if order.status != Order.STATUS_DRAFT:
            raise InvalidTransitionError(f"Cannot confirm order from status '{order.status}'.")

        # Concurrency safety: Lock all relevant product rows
        items = list(order.items.select_related("product").all())
        skus = [it.sku for it in items]
        locked_products = {
            p.sku: p
            for p in Product.objects.select_for_update().filter(sku__in=skus)
        }

        short_lines = []
        message_parts = []
        for it in items:
            product = locked_products.get(it.sku)
            available = product.stock if product else 0
            if available < it.quantity:
                short_lines.append({
                    "sku": it.sku,
                    "requested": it.quantity,
                    "available": available,
                })
                message_parts.append(
                    f"Insufficient stock for {it.name}. Available: {available}, Requested: {it.quantity}."
                )

        if short_lines:
            raise InsufficientStockError(
                message=" ".join(message_parts),
                details=short_lines,
            )

        # Deduct stock
        for it in items:
            product = locked_products[it.sku]
            product.stock -= it.quantity
            product.save(update_fields=["stock", "updated_at"])
            from .serializers import ProductSerializer
            mirror_product(ProductSerializer(product).data)

        order.status = Order.STATUS_CONFIRMED
        order.save(update_fields=["status", "updated_at"])

    from .serializers import OrderSerializer
    res = OrderSerializer(order).data
    mirror_order(res)
    log_audit_event("ORDER_CONFIRMED", {"order_number": order_number})
    return res


def dispatch_order_service(order_number: str):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise NotFoundError(f"Order '{order_number}' not found.")

        if order.status != Order.STATUS_CONFIRMED:
            raise InvalidTransitionError(f"Cannot dispatch order from status '{order.status}'.")

        order.status = Order.STATUS_DISPATCHED
        order.save(update_fields=["status", "updated_at"])

    from .serializers import OrderSerializer
    res = OrderSerializer(order).data
    mirror_order(res)
    log_audit_event("ORDER_DISPATCHED", {"order_number": order_number})
    return res


def deliver_order_service(order_number: str):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise NotFoundError(f"Order '{order_number}' not found.")

        if order.status != Order.STATUS_DISPATCHED:
            raise InvalidTransitionError(f"Cannot deliver order from status '{order.status}'.")

        order.status = Order.STATUS_DELIVERED
        order.save(update_fields=["status", "updated_at"])

    from .serializers import OrderSerializer
    res = OrderSerializer(order).data
    mirror_order(res)
    log_audit_event("ORDER_DELIVERED", {"order_number": order_number})
    return res


def cancel_order_service(order_number: str):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise NotFoundError(f"Order '{order_number}' not found.")

        if order.status not in (Order.STATUS_DRAFT, Order.STATUS_CONFIRMED):
            raise InvalidTransitionError(f"Cannot cancel order from status '{order.status}'.")

        # If CONFIRMED, restore stock exactly once
        if order.status == Order.STATUS_CONFIRMED:
            items = list(order.items.select_related("product").all())
            skus = [it.sku for it in items]
            locked_products = {
                p.sku: p
                for p in Product.objects.select_for_update().filter(sku__in=skus)
            }
            for it in items:
                product = locked_products.get(it.sku)
                if product:
                    product.stock += it.quantity
                    product.save(update_fields=["stock", "updated_at"])
                    from .serializers import ProductSerializer
                    mirror_product(ProductSerializer(product).data)

        order.status = Order.STATUS_CANCELLED
        order.save(update_fields=["status", "updated_at"])

    from .serializers import OrderSerializer
    res = OrderSerializer(order).data
    mirror_order(res)
    log_audit_event("ORDER_CANCELLED", {"order_number": order_number})
    return res


def update_inventory_service(sku: str, quantity: int, reason: str = ""):
    if quantity is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise ValidationError("Quantity must be an integer >= 0.")

    with transaction.atomic():
        try:
            product = Product.objects.select_for_update().get(sku=sku)
        except Product.DoesNotExist:
            raise NotFoundError(f"Product with SKU '{sku}' not found.")

        product.stock = quantity
        product.save(update_fields=["stock", "updated_at"])

        InventoryAdjustment.objects.create(
            sku=sku,
            quantity=quantity,
            reason=reason or "",
        )

    from .serializers import ProductSerializer
    mirror_product(ProductSerializer(product).data)
    log_audit_event("INVENTORY_UPDATED", {"sku": sku, "quantity": quantity, "reason": reason})
    return {"sku": sku, "quantity": quantity}
