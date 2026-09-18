"""
Unit tests for OrderService's payload validation.

    pip install -r requirements-dev.txt
    pytest -v

Only the pure validation function is exercised here, so no AWS resources or
credentials are needed. For full handler coverage, add `moto` and wrap the
tests in `mock_aws` to get in-memory DynamoDB and SQS.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda_functions", "order_service"))

# app.py builds its boto3 clients and reads its configuration at import time,
# so these have to be in place before the import below. No AWS call is made:
# only the pure validation function is exercised.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ORDERS_TABLE", "Orders")
os.environ.setdefault("ORDER_QUEUE_URL", "https://sqs.fake/OrderQueue")

from app import _validate_payload  # noqa: E402


def test_valid_payload_has_no_errors():
    payload = {
        "customerId": "cust_1",
        "items": [{"sku": "PIZZA", "quantity": 1}],
        "amount": 12.5,
    }
    assert _validate_payload(payload) == []


def test_missing_customer_id():
    payload = {"items": [{"sku": "PIZZA", "quantity": 1}], "amount": 12.5}
    errors = _validate_payload(payload)
    assert any("customerId" in e for e in errors)


def test_empty_items_rejected():
    payload = {"customerId": "cust_1", "items": [], "amount": 12.5}
    errors = _validate_payload(payload)
    assert any("items" in e for e in errors)


def test_negative_amount_rejected():
    payload = {
        "customerId": "cust_1",
        "items": [{"sku": "PIZZA", "quantity": 1}],
        "amount": -5,
    }
    errors = _validate_payload(payload)
    assert any("amount" in e for e in errors)


def test_invalid_quantity_rejected():
    payload = {
        "customerId": "cust_1",
        "items": [{"sku": "PIZZA", "quantity": 0}],
        "amount": 12.5,
    }
    errors = _validate_payload(payload)
    assert any("quantity" in e for e in errors)
