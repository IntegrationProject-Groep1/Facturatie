import os
import re
import xml.etree.ElementTree as ET

import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv

from src.services import fossbilling_client, crm_publisher

load_dotenv()

ISO8601_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def get_connection() -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(
        os.getenv("RABBITMQ_USER"),
        os.getenv("RABBITMQ_PASSWORD")
    )
    parameters = pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST"),
        port=int(os.getenv("RABBITMQ_PORT", 5672)),
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
        credentials=credentials
    )
    return pika.BlockingConnection(parameters)


def validate_invoice_cancelled(root: ET.Element) -> list[str]:
    """
    Validates an invoice_cancelled XML message.
    Returns a list of error strings. An empty list means the message is valid.
    """
    errors: list[str] = []

    msg_id = root.findtext("header/message_id")
    msg_type = root.findtext("header/type")
    timestamp = root.findtext("header/timestamp")
    source = root.findtext("header/source")
    version = root.findtext("header/version")
    correlation_id = root.findtext("header/correlation_id")
    invoice_id = root.findtext("body/invoice_id")
    customer_id = root.findtext("body/customer_id")
    reason = root.findtext("body/reason")

    if not msg_id:
        errors.append("WARN: missing_required_field: message_id")
    if not version or version != "2.0":
        errors.append(f"ERROR: invalid or missing version (expected 2.0, got '{version}')")
    if not msg_type or msg_type != "invoice_cancelled":
        errors.append(f"ERROR: expected type invoice_cancelled, got '{msg_type}'")
    if not timestamp:
        errors.append("WARN: missing_required_field: timestamp")
    elif not ISO8601_UTC_PATTERN.match(timestamp):
        errors.append(f"ERROR: invalid_iso8601_timestamp: '{timestamp}'")
    if not source:
        errors.append("WARN: missing_required_field: source")
    if not correlation_id:
        errors.append("ERROR: correlation_id required for invoice_cancelled")
    if not invoice_id:
        errors.append("ERROR: invoice_id required for invoice_cancelled")
    if not customer_id:
        errors.append("ERROR: customer_id required for invoice_cancelled")

    if reason:
        print(f"[CANCELLATION] Cancellation reason: {reason}")

    return errors


def send_to_dlq(
    channel: pika.channel.Channel,
    body: bytes,
    errors: list[str],
) -> None:
    """Forwards an invalid or failed message to the Dead Letter Queue."""
    dlq = os.getenv("QUEUE_DLQ", "facturatie.dlq")
    channel.queue_declare(queue=dlq, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=dlq,
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml",
            headers={"errors": "; ".join(errors)},
        )
    )
    print(f"[CANCELLATION] Message forwarded to DLQ. Errors: {errors}")


def process_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    properties: pika.spec.BasicProperties,
    body: bytes,
) -> None:
    print("\n[CANCELLATION] Message received")

    # Step 1: parse XML
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print(f"[CANCELLATION] ERROR: Invalid XML — {e}")
        send_to_dlq(channel, body, [f"ERROR: invalid_xml: {e}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 2: filter — only handle invoice_cancelled messages
    msg_type = root.findtext("header/type")
    if msg_type != "invoice_cancelled":
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 3: validate
    errors = validate_invoice_cancelled(root)
    if errors:
        for error in errors:
            print(f"[CANCELLATION] {error}")
        send_to_dlq(channel, body, errors)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    invoice_id = root.findtext("body/invoice_id")
    customer_id = root.findtext("body/customer_id")
    correlation_id = root.findtext("header/correlation_id")
    msg_id = root.findtext("header/message_id")

    print(f"[CANCELLATION] Processing invoice_cancelled | invoice={invoice_id} | message_id={msg_id}")

    # Step 4: cancel invoice in FossBilling
    success = fossbilling_client.cancel_invoice(invoice_id)
    if not success:
        error_msg = f"ERROR: FossBilling failed to cancel invoice '{invoice_id}'"
        print(f"[CANCELLATION] {error_msg}")
        send_to_dlq(channel, body, [error_msg])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 5: notify CRM
    crm_publisher.publish_invoice_cancelled(invoice_id, customer_id, correlation_id)
    print(f"[CANCELLATION] Flow complete for invoice '{invoice_id}'")
    channel.basic_ack(delivery_tag=method.delivery_tag)


def start_receiver(queue: str | None = None) -> None:
    if queue is None:
        queue = os.getenv("QUEUE_INCOMING", "facturatie.incoming")
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_message)

    print(f"[CANCELLATION] Listening on queue '{queue}'... (CTRL+C to stop)")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[CANCELLATION] Stopping consumer...")
    finally:
        connection.close()


if __name__ == "__main__":
    start_receiver()
