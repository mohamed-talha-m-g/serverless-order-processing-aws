"""
OrderService Lambda
--------------------
Receives POST /order from API Gateway (HTTP API), validates the payload,
writes the order to DynamoDB with an idempotency guard, and publishes an
event to SQS so downstream services (PaymentService) can process it
asynchronously.

Environment variables (set via SAM template, never hardcode):
    ORDERS_TABLE   - DynamoDB table name
    ORDER_QUEUE_URL - SQS queue URL
    POWERTOOLS_SERVICE_NAME - used for structured logging (optional)
"""

import json
import logging
import os
import time
import uuid
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

TABLE_NAME = os.environ["ORDERS_TABLE"]
QUEUE_URL = os.environ["ORDER_QUEUE_URL"]
table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, default=str),
    }


def _validate_payload(payload: dict) -> list:
    """Return a list of validation errors (empty list = valid)."""
    errors = []
    if not isinstance(payload.get("customerId"), str) or not payload.get("customerId"):
        errors.append("customerId is required and must be a non-empty string")

    items = payload.get("items")
    if not isinstance(items, list) or len(items) == 0:
        errors.append("items must be a non-empty array")
    else:
        for idx, item in enumerate(items):
            if not isinstance(item.get("sku"), str):
                errors.append(f"items[{idx}].sku must be a string")
            qty = item.get("quantity")
            if not isinstance(qty, (int, float)) or qty <= 0:
                errors.append(f"items[{idx}].quantity must be a positive number")

    amount = payload.get("amount")
    if not isinstance(amount, (int, float)) or amount <= 0:
        errors.append("amount must be a positive number")

    return errors


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Malformed JSON body"})

    validation_errors = _validate_payload(body)
    if validation_errors:
        return _response(400, {"error": "Validation failed", "details": validation_errors})

    # Idempotency: if the client supplies an Idempotency-Key header, use it as
    # the order id so retried requests (e.g. from a flaky mobile network)
    # don't create duplicate orders.
    headers = event.get("headers") or {}
    idempotency_key = headers.get("idempotency-key") or headers.get("Idempotency-Key")
    order_id = idempotency_key or str(uuid.uuid4())

    now = int(time.time())
    item = {
        "orderId": order_id,
        "customerId": body["customerId"],
        "items": body["items"],
        "amount": Decimal(str(body["amount"])),
        "status": "PLACED",
        "createdAt": now,
        "updatedAt": now,
        # TTL example: auto-expire abandoned/failed test orders after 30 days
        "ttl": now + (30 * 24 * 60 * 60),
    }

    try:
        # ConditionExpression makes the write idempotent: if this orderId
        # already exists we skip re-inserting and just re-emit the queue
        # message, so retries are safe.
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(orderId)",
        )
        logger.info(json.dumps({"event": "order_created", "orderId": order_id}))
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            logger.error(json.dumps({"event": "dynamodb_put_failed", "error": str(e)}))
            return _response(500, {"error": "Failed to persist order"})
        logger.info(json.dumps({"event": "duplicate_order_ignored", "orderId": order_id}))

    try:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({
                "orderId": order_id,
                "customerId": body["customerId"],
                "amount": str(body["amount"]),
            }),
            MessageAttributes={
                "eventType": {"DataType": "String", "StringValue": "ORDER_PLACED"}
            },
        )
    except ClientError as e:
        # DB write already succeeded, so don't fail the whole request —
        # log loudly; a reconciliation job or DynamoDB Stream trigger can
        # pick up orders stuck in PLACED with no queue message.
        logger.error(json.dumps({"event": "sqs_send_failed", "orderId": order_id, "error": str(e)}))

    return _response(200, {"orderId": order_id, "status": "PLACED"})
