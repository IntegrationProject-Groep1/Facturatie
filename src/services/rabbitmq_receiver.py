import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv
import os
import re
import xml.etree.ElementTree as ET

# ISO-8601 UTC pattern: 2026-02-24T18:30:00Z
ISO8601_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

load_dotenv()

# Valid values per XML Naming Standard (all lowercase snake_case)
VALID_TYPES: set[str] = {"consumption_order", "payment_registered", "heartbeat"}
VALID_VAT_RATES: set[str] = {"6", "12", "21"}
VALID_PAYMENT_METHODS: set[str] = {"company_link", "on_site", "online"}

# In-memory set for duplicate detection based on header/message_id
# Note: persists only during runtime; will be migrated to MySQL in a later sprint.
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
    Validates the XML message against the XML Naming Standard.
    Returns a list of error strings. An empty list means the message is valid.
    Pass seen_ids to enable duplicate detection based on header/message_id.
    """
    errors: list[str] = []

    msg_id    = root.findtext("header/message_id")
    msg_type  = root.findtext("header/type")
    timestamp = root.findtext("header/timestamp")
    source    = root.findtext("header/source")
    version   = root.findtext("header/version")

    # Duplicate detection: flag if this message_id was already seen
    if seen_ids is not None and msg_id and msg_id in seen_ids:
        errors.append(f"WARN: duplicate_message_id: '{msg_id}'")
        return errors

    # Header field validation
    if not msg_id:
        errors.append("WARN: missing_required_field: message_id")
    if not version or version != "2.0":
        errors.append(f"ERROR: invalid or missing version (expected 2.0, got '{version}')")

    # Message type validation — must be lowercase snake_case
    if not msg_type:
        errors.append("ERROR: unknown_message_type: missing")
    elif msg_type.lower() in VALID_TYPES and msg_type != msg_type.lower():
        # Known type but wrong case (e.g. CONSUMPTION_ORDER instead of consumption_order)
        errors.append(f"ERROR: invalid_enum_case: use snake_case lowercase (got '{msg_type}')")
    elif msg_type not in VALID_TYPES:
        errors.append(f"ERROR: unknown_message_type: '{msg_type}'")

    if not timestamp:
        errors.append("WARN: missing_required_field: timestamp")
    elif not ISO8601_UTC_PATTERN.match(timestamp):
        errors.append(f"ERROR: invalid_iso8601_timestamp: '{timestamp}'")
    if not source:
        errors.append("WARN: missing_required_field: source")

    # Conditional validation: consumption_order
    if msg_type == "consumption_order":
        is_company   = root.findtext("body/customer/is_company_linked")
        company_id   = root.findtext("body/customer/company_id")
        company_name = root.findtext("body/customer/company_name")

        if is_company == "true":
            if not company_id:
                errors.append("ERROR: company_id required when is_company_linked=true")
            if not company_name:
                errors.append("ERROR: company_name required when is_company_linked=true")

        for item in root.findall("body/items/item"):
            vat = item.findtext("vat_rate")
            item_id = item.findtext("id") or "unknown"
            if vat not in VALID_VAT_RATES:
                errors.append(
                    f"ERROR: vat_rate must be 6, 12 or 21 for item '{item_id}' (got '{vat}')"
                )

    # Conditional validation: payment_registered
    if msg_type == "payment_registered":
        correlation_id = root.findtext("header/correlation_id")
        if not correlation_id:
            errors.append("ERROR: correlation_id required for payment_registered")

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

    # Step 2: duplicate detection based on header/message_id
    msg_id = root.findtext("header/message_id")
    if msg_id in seen_message_ids:
        print(f"[RECEIVER] WARN: duplicate_message_id: '{msg_id}' — ignored")
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

    # Step 4: mark message_id as seen
    if msg_id:
        seen_message_ids.add(msg_id)

    msg_type = root.findtext("header/type")
    print(f"[RECEIVER] Valid message received | type={msg_type} | message_id={msg_id}")

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