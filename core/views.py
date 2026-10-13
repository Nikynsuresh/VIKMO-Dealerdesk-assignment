from decimal import Decimal
from django.db.models import Q
from django.shortcuts import render
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Product, Dealer, Order
from .serializers import (
    ProductSerializer,
    ProductCreateSerializer,
    DealerSerializer,
    DealerCreateSerializer,
    OrderSerializer,
)
from .pagination import StandardPagination
from .exceptions import (
    ValidationError,
    NotFoundError,
    DuplicateError,
)
from .services import (
    create_order_service,
    update_order_items_service,
    confirm_order_service,
    dispatch_order_service,
    deliver_order_service,
    cancel_order_service,
    update_inventory_service,
)
from .supplier_sync import run_supplier_sync
from .assistant import handle_assistant_chat
from .forecast import get_sku_forecast, get_reorder_suggestions
from .mongodb import get_mongo_stats, mirror_product, mirror_dealer


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})


class ProductListCreateView(APIView):
    pagination_class = StandardPagination

    def get(self, request):
        qs = Product.objects.all()

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(sku__icontains=search) |
                Q(name__icontains=search) |
                Q(brand__icontains=search)
            )

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)

        vehicle = request.query_params.get("vehicle")
        if vehicle:
            qs = qs.filter(vehicle_fitment__icontains=vehicle)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        sku = request.data.get("sku")
        if not sku or not isinstance(sku, str) or not sku.strip():
            raise ValidationError("Product SKU is required.")
        sku = sku.strip()

        if Product.objects.filter(sku=sku).exists():
            raise DuplicateError(f"Product with SKU '{sku}' already exists.")

        serializer = ProductCreateSerializer(data=request.data)
        if not serializer.is_valid():
            err_msg = next(iter(serializer.errors.values()))[0] if serializer.errors else "Invalid product data."
            raise ValidationError(str(err_msg))

        product = serializer.save(stock=0)
        res_data = ProductSerializer(product).data
        mirror_product(res_data)
        return Response(res_data, status=status.HTTP_201_CREATED)


class ProductDetailView(APIView):
    def get_object(self, sku):
        try:
            return Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            raise NotFoundError(f"Product with SKU '{sku}' not found.")

    def get(self, request, sku):
        product = self.get_object(sku)
        return Response(ProductSerializer(product).data)

    def patch(self, request, sku):
        product = self.get_object(sku)

        # SKU cannot change
        if "sku" in request.data and request.data["sku"] != sku:
            raise ValidationError("SKU cannot be changed.")

        if "price" in request.data:
            try:
                str_p = str(request.data["price"]).strip()
                p_val = Decimal(str_p)
                if p_val <= Decimal("0"):
                    raise ValidationError("Price must be greater than zero.")
                if "." in str_p and len(str_p.split(".")[1]) > 2:
                    raise ValidationError("Price can have at most 2 decimal places.")
                product.price = p_val
            except ValidationError:
                raise
            except Exception:
                raise ValidationError("Invalid price format.")

        for field in ["name", "category", "brand", "vehicle_fitment", "description"]:
            if field in request.data:
                setattr(product, field, request.data[field])

        product.save()
        res_data = ProductSerializer(product).data
        mirror_product(res_data)
        return Response(res_data)


class InventoryUpdateView(APIView):
    def put(self, request, sku):
        quantity = request.data.get("quantity")
        reason = request.data.get("reason", "")
        res = update_inventory_service(sku=sku, quantity=quantity, reason=reason)
        return Response(res)


class DealerListCreateView(APIView):
    pagination_class = StandardPagination

    def get(self, request):
        qs = Dealer.objects.all()
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        dealer_code = request.data.get("dealer_code")
        name = request.data.get("name")

        if not dealer_code or not str(dealer_code).strip():
            raise ValidationError("dealer_code is required.")
        if not name or not str(name).strip():
            raise ValidationError("name is required.")

        dealer_code = str(dealer_code).strip()
        if Dealer.objects.filter(dealer_code=dealer_code).exists():
            raise DuplicateError(f"Dealer with dealer_code '{dealer_code}' already exists.")

        serializer = DealerCreateSerializer(data=request.data)
        if not serializer.is_valid():
            err_msg = next(iter(serializer.errors.values()))[0] if serializer.errors else "Invalid dealer data."
            raise ValidationError(str(err_msg))

        dealer = serializer.save()
        res_data = DealerSerializer(dealer).data
        mirror_dealer(res_data)
        return Response(res_data, status=status.HTTP_201_CREATED)


class DealerDetailView(APIView):
    def get(self, request, id):
        try:
            dealer = Dealer.objects.get(id=id)
        except Dealer.DoesNotExist:
            raise NotFoundError(f"Dealer with id {id} not found.")
        return Response(DealerSerializer(dealer).data)


class OrderListCreateView(APIView):
    pagination_class = StandardPagination

    def get(self, request):
        qs = Order.objects.select_related("dealer").prefetch_related("items").all()

        dealer_id = request.query_params.get("dealer_id")
        if dealer_id:
            qs = qs.filter(dealer_id=dealer_id)

        st = request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        dealer_id = request.data.get("dealer_id")
        items = request.data.get("items")
        idempotency_key = request.headers.get("Idempotency-Key")

        if dealer_id is None:
            raise ValidationError("dealer_id is required.")
        if items is None or not isinstance(items, list):
            raise ValidationError("items list is required.")

        res_data, st_code = create_order_service(
            dealer_id=dealer_id,
            items_raw=items,
            idempotency_key=idempotency_key,
        )
        return Response(res_data, status=st_code)


class OrderDetailView(APIView):
    def get(self, request, order_number):
        try:
            order = Order.objects.select_related("dealer").prefetch_related("items").get(order_number=order_number)
        except Order.DoesNotExist:
            raise NotFoundError(f"Order '{order_number}' not found.")
        return Response(OrderSerializer(order).data)

    def put(self, request, order_number):
        items = request.data.get("items")
        if items is None or not isinstance(items, list):
            raise ValidationError("items list is required.")
        res = update_order_items_service(order_number, items)
        return Response(res)


class OrderConfirmView(APIView):
    def post(self, request, order_number):
        res = confirm_order_service(order_number)
        return Response(res)


class OrderDispatchView(APIView):
    def post(self, request, order_number):
        res = dispatch_order_service(order_number)
        return Response(res)


class OrderDeliverView(APIView):
    def post(self, request, order_number):
        res = deliver_order_service(order_number)
        return Response(res)


class OrderCancelView(APIView):
    def post(self, request, order_number):
        res = cancel_order_service(order_number)
        return Response(res)


class SupplierSyncView(APIView):
    def post(self, request):
        res = run_supplier_sync()
        return Response(res)


class AssistantChatView(APIView):
    def post(self, request):
        session_id = request.data.get("session_id")
        dealer_id = request.data.get("dealer_id")
        message = request.data.get("message")

        if not session_id or not isinstance(session_id, str):
            raise ValidationError("session_id is required.")
        if dealer_id is None:
            raise ValidationError("dealer_id is required.")
        if not message or not isinstance(message, str):
            raise ValidationError("message is required.")

        res = handle_assistant_chat(session_id=session_id, dealer_id=dealer_id, message=message)
        return Response(res)


class ForecastDetailView(APIView):
    def get(self, request, sku):
        res = get_sku_forecast(sku)
        return Response(res)


class ReorderSuggestionsView(APIView):
    def get(self, request):
        res = get_reorder_suggestions()
        return Response({"results": res})


class MongoStatusView(APIView):
    def get(self, request):
        stats = get_mongo_stats()
        return Response(stats)


def dashboard_view(request):
    """Renders the executive formal frontend UI dashboard."""
    return render(request, "dashboard.html")
