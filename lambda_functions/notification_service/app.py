"""
NotificationService Lambda
----------------------------
Triggered by SNS (OrderNotification topic). Keeping this as its own
function — rather than emailing directly from PaymentService — means you
can add channels (SMS via SNS, push, Slack webhook, SES branded emails)
or swap providers without touching payment logic at all.

Environment variables:
    ORDERS_TABLE - DynamoDB table name (to log notification delivery)
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["ORDERS_TABLE"]
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event, context):
    for record in event.get("Records", []):
        try:
            sns_message = json.loads(record["Sns"]["Message"])
            order_id = sns_message["orderId"]
            status = sns_message["status"]

            # In production: call SES for a templated email, or another
            # channel. SNS's own email subscription (set up in the console)
            # already handles plain email delivery for you — this function
            # is where you'd add anything beyond that.
            logger.info(json.dumps({
                "event": "notification_dispatched",
                "orderId": order_id,
                "status": status,
            }))

            table.update_item(
                Key={"orderId": order_id},
                UpdateExpression="SET notifiedAt = :now, notificationStatus = :s",
                ExpressionAttributeValues={
                    ":now": int(time.time()),
                    ":s": "SENT",
                },
            )
        except Exception as e:
            logger.error(json.dumps({"event": "notification_failed", "error": str(e)}))
            raise  # let Lambda retry / DLQ handle it
