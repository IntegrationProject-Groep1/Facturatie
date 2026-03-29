import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv
import os
import xml.etree.ElementTree as ET

load_dotenv()

VALID_TYPES: set[str] = {"CONSUMPTION_ORDER", "PAYMENT_REGISTERED", "HEARTBEAT"}
VALID_VAT_RATES: set[str] = {"6", "12", "21"}

# In-memory set for duplicate detection based on header/id 
# Note: Data persists only during runtime; will be migrated to MySQL for 
# permanent storage in a later sprint to ensure reliability after restarts.
seen_message_ids: set[str] = set()


def get_connection() -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(
        os.getenv("RABBITMQ_USER"),
        os.getenv("RABBITMQ_PASSWORD")
    )
    parameters = pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST"),
        port=int(os.getenv("RABBITMQ_PORT", 5672)),
        credentials=credentials
    )
    return pika.BlockingConnection(parameters)


def validate_message(root: ET.Element, seen_ids: set[str] | None = None) -> list[str]:
    """
    Validates the XML message against the agreed structure.
    Returns a list of error messages. An empty list means the message is valid.
    Pass seen_ids to enable duplicate detection based on header/id.
    """
    errors: list[str] = []

    msg_id    = root.findtext("header/id")
    msg_type  = root.findtext("header/type")
    timestamp = root.findtext("header/timestamp")
    source    = root.findtext("header/source")
    version   = root.findtext("header/version")

    # Duplicate detection: flag if this message ID was already seen
    if seen_ids is not None and msg_id and msg_id in seen_ids:
        errors.append(f"ERROR: duplicate message id '{msg_id}'")
        return errors

    # General header validation
    if not msg_id:
        errors.append("WARN: missing required field <id>")
    if not version or version != "2.0":
        errors.append(f"ERROR: invalid or missing version (expected 2.0, got '{version}')")
    if not msg_type or msg_type not in VALID_TYPES:
        errors.append(f"ERROR: unknown or missing message type '{msg_type}'")
    if not timestamp:
        errors.append("ERROR: missing required field <timestamp>")
    if not source:
        errors.append("ERROR: missing required field <source>")

    # Conditional validation: CONSUMPTION_ORDER
    if msg_type == "CONSUMPTION_ORDER":
        is_company   = root.findtext("body/customer/is_company_linked")
        company_id   = root.findtext("body/customer/company_id")
        company_name = root.findtext("body/customer/company_name")

        if is_company == "true":
            if not company_id:
                errors.append(
                    "ERROR: company_id required when is_company_linked=true"
                )
            if not company_name:
                errors.append(
                    "ERROR: company_name required when is_company_linked=true"
                )

        for item in root.findall("body/items/item"):
            vat = item.findtext("vat_rate")
            item_id = item.findtext("id") or "unknown"
            if vat not in VALID_VAT_RATES:
                errors.append(
                    f"ERROR: vat_rate must be 6, 12 or 21 for item '{item_id}' (got '{vat}')"
                )

    # Conditional validation: PAYMENT_REGISTERED
    if msg_type == "PAYMENT_REGISTERED":
        correlation_id = root.findtext("header/correlation_id")
        if not correlation_id:
            errors.append(
                "ERROR: correlation_id required for PAYMENT_REGISTERED"
            )

    return errors


def send_to_dlq(
    channel: pika.channel.Channel,
    body: bytes,
    errors: list[str]
) -> None:
    """Forwards an invalid message to the Dead Letter Queue."""
    channel.queue_declare(queue="facturatie.dlq", durable=True)
    channel.basic_publish(
        exchange="",
        routing_key="facturatie.dlq",
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml",
            headers={"errors": "; ".join(errors)}
        )
    )
    print(f"[DLQ] Message forwarded to DLQ. Errors: {errors}")


def process_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    properties: pika.spec.BasicProperties,
    body: bytes
) -> None:
    print("\n[RECEIVER] Message received")

    # Step 1: parse XML
    try:
        root = ET.fromstring(body.decode("utf-8"))
    except ET.ParseError as e:
        print(f"[RECEIVER] ERROR: Invalid XML — {e}")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 2: duplicate detection based on header/id
    msg_id = root.findtext("header/id")
    if msg_id in seen_message_ids:
        print(f"[RECEIVER] WARN: Duplicate message id '{msg_id}' — ignored")
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 3: validate message structure
    errors = validate_message(root)
    if errors:
        for error in errors:
            print(f"[RECEIVER] {error}")
        send_to_dlq(channel, body, errors)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 4: mark message id as seen
    if msg_id:
        seen_message_ids.add(msg_id)

    msg_type = root.findtext("header/type")
    print(f"[RECEIVER] Valid message received | type={msg_type} | id={msg_id}")

    # later we will add the fossbilling API call here
    channel.basic_ack(delivery_tag=method.delivery_tag)


def start_receiver(queue: str = "facturatie") -> None:
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_message)

    print(f"[RECEIVER] Listening on queue '{queue}'... (CTRL+C to stop)")
    channel.start_consuming()


if __name__ == "__main__":
    start_receiver()