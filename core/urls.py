from django.urls import path
from .views import (
    HealthView,
    ProductListCreateView,
    ProductDetailView,
    InventoryUpdateView,
    DealerListCreateView,
    DealerDetailView,
    OrderListCreateView,
    OrderDetailView,
    OrderConfirmView,
    OrderDispatchView,
    OrderDeliverView,
    OrderCancelView,
    SupplierSyncView,
    AssistantChatView,
    ForecastDetailView,
    ReorderSuggestionsView,
    MongoStatusView,
)

urlpatterns = [
    # Health
    path("health/", HealthView.as_view(), name="health"),

    # Products
    path("products/", ProductListCreateView.as_view(), name="products-list-create"),
    path("products/<str:sku>/", ProductDetailView.as_view(), name="product-detail"),

    # Inventory
    path("inventory/<str:sku>/", InventoryUpdateView.as_view(), name="inventory-update"),

    # Dealers
    path("dealers/", DealerListCreateView.as_view(), name="dealers-list-create"),
    path("dealers/<int:id>/", DealerDetailView.as_view(), name="dealer-detail"),

    # Orders
    path("orders/", OrderListCreateView.as_view(), name="orders-list-create"),
    path("orders/<str:order_number>/", OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:order_number>/confirm/", OrderConfirmView.as_view(), name="order-confirm"),
    path("orders/<str:order_number>/dispatch/", OrderDispatchView.as_view(), name="order-dispatch"),
    path("orders/<str:order_number>/deliver/", OrderDeliverView.as_view(), name="order-deliver"),
    path("orders/<str:order_number>/cancel/", OrderCancelView.as_view(), name="order-cancel"),

    # Supplier Sync
    path("sync/supplier/", SupplierSyncView.as_view(), name="supplier-sync"),

    # Dealer Assistant
    path("assistant/chat/", AssistantChatView.as_view(), name="assistant-chat"),

    # Forecasting & Inventory
    path("forecast/<str:sku>/", ForecastDetailView.as_view(), name="forecast-detail"),
    path("reorder-suggestions/", ReorderSuggestionsView.as_view(), name="reorder-suggestions"),

    # MongoDB Integration
    path("mongodb/status/", MongoStatusView.as_view(), name="mongodb-status"),
]
