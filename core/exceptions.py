from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler
from django.http import Http404


class DealerDeskException(APIException):
    status_code = 400
    code = "VALIDATION_ERROR"
    message = "An error occurred."
    details = []

    def __init__(self, message=None, code=None, details=None, status_code=None):
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if details is not None:
            self.details = details
        else:
            self.details = []
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


class ValidationError(DealerDeskException):
    status_code = 400
    code = "VALIDATION_ERROR"


class NotFoundError(DealerDeskException):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found."


class DuplicateError(DealerDeskException):
    status_code = 409
    code = "DUPLICATE"
    message = "Resource already exists."


class InsufficientStockError(DealerDeskException):
    status_code = 409
    code = "INSUFFICIENT_STOCK"


class InvalidTransitionError(DealerDeskException):
    status_code = 409
    code = "INVALID_TRANSITION"


class OrderLockedError(DealerDeskException):
    status_code = 409
    code = "ORDER_LOCKED"
    message = "Order is locked and cannot be edited."


class IdempotencyConflictError(DealerDeskException):
    status_code = 409
    code = "IDEMPOTENCY_CONFLICT"
    message = "Idempotency key reused with conflicting request body."


class SupplierUnavailableError(DealerDeskException):
    status_code = 502
    code = "SUPPLIER_UNAVAILABLE"
    message = "Supplier API is unavailable."


def custom_exception_handler(exc, context):
    # Handle DealerDeskException
    if isinstance(exc, DealerDeskException):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details if isinstance(exc.details, list) else [],
                }
            },
            status=exc.status_code,
        )

    if isinstance(exc, Http404):
        return Response(
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Resource not found.",
                    "details": [],
                }
            },
            status=404,
        )

    # Let DRF handle standard exceptions first
    response = exception_handler(exc, context)

    if response is not None:
        code = "VALIDATION_ERROR"
        status_code = response.status_code

        if status_code == 404:
            code = "NOT_FOUND"
            message = "Resource not found."
        elif status_code == 400:
            code = "VALIDATION_ERROR"
            message = "Validation error."
        elif status_code == 409:
            code = "DUPLICATE"
            message = "Conflict."
        elif status_code >= 500:
            code = "INTERNAL_ERROR"
            message = "Internal server error."
        else:
            message = str(exc)

        # Extract details if available
        details = []
        if isinstance(response.data, dict):
            if "detail" in response.data:
                message = str(response.data["detail"])
            else:
                for k, v in response.data.items():
                    if isinstance(v, list):
                        for item in v:
                            details.append(f"{k}: {item}")
                    else:
                        details.append(f"{k}: {v}")
                if details:
                    message = details[0]
        elif isinstance(response.data, list):
            details = [str(item) for item in response.data]
            if details:
                message = details[0]

        return Response(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                }
            },
            status=status_code,
        )

    # If unhandled server exception
    return Response(
        {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(exc),
                "details": [],
            }
        },
        status=500,
    )
