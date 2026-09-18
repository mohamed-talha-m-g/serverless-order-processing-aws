"""
PaymentService Lambda
----------------------
Triggered by SQS (OrderQueue). Processes each order's "payment", updates the
order's status in DynamoDB, and publishes a result event to SNS so
NotificationService (and anything else) can react.

Uses SQS partial batch failure reporting (ReportBatchItemFailures) so a bad
message doesn't force the whole batch to be retried — only the failed ones
go back to the queue / eventually to the DLQ.

Environment variables:
    ORDERS_TABLE      - DynamoDB table name
    NOTIFICATION_TOPIC_ARN - SNS topic ARN
"""

import json
import logging
import os
import random
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

TABLE_NAME = os.environ["ORDERS_TABLE"]
TOPIC_ARN = os.environ["NOTIFICATION_TOPIC_ARN"]
table = dynamodb.Table(TABLE_NAME)


def _process_payment(order_id: str, amount: str) -> bool:
    """
    Placeholder for a real payment gateway call (Stripe, Razorpay, etc).
    Replace this with an actual HTTP call + proper error handling.
    Simulated ~90% success rate so you can see both code paths in demos.
    """
    time.sleep(0.05)
    return random.random() < 0.9


def _update_order_status(order_id: str, new_status: str) -> None:
    table.update_item(
        Key={"orderId": order_id},
        UpdateExpression="SET #s = :new, updatedAt = :now",
        ConditionExpression="#s = :placed",  # only transition from PLACED
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":new": new_status,
            ":placed": "PLACED",
            ":now": int(time.time()),
        },
    )


def _publish_notification(order_id: str, status: str) -> None:
    sns.publish(
        TopicArn=TOPIC_ARN,
        Subject=f"Order {order_id} {status}",
        Message=json.dumps({"orderId": order_id, "status": status}),
        MessageAttributes={
            "status": {"DataType": "String", "StringValue": status}
        },
    )


def lambda_handler(event, context):
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            payload = json.loads(record["body"])
            order_id = payload["orderId"]
            amount = payload.get("amount", "0")

            success = _process_payment(order_id, amount)
            new_status = "PAID" if success else "PAYMENT_FAILED"

            try:
                _update_order_status(order_id, new_status)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    # Order was already transitioned (duplicate delivery from SQS
                    # at-least-once semantics) — safe to ignore.
                    logger.info(json.dumps({"event": "status_already_updated", "orderId": order_id}))
                else:
                    raise

            _publish_notification(order_id, new_status)
            logger.info(json.dumps({"event": "payment_processed", "orderId": order_id, "status": new_status}))

        except Exception as e:
            logger.error(json.dumps({
                "event": "payment_processing_failed",
                "messageId": message_id,
                "error": str(e),
            }))
            # Only this message goes back to the queue / DLQ, not the whole batch
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
