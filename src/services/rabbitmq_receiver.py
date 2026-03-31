import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv
import os
import re
import xml.etree.ElementTree as ET
from .fossbilling_api import create_registration_invoice
from .rabbitmq_sender import build_invoice_request_xml, send_message

# ISO-8601 UTC pattern: 2026-02-24T18:30:00Z
ISO8601_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

load_dotenv()

# Valid values per XML Naming Standard (all lowercase snake_case)
VALID_TYPES: set[str] = {"consumption_order", "payment_registered", "heartbeat", "new_registration"}
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
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
        credentials=credentials
    )
    return pika.BlockingConnection(parameters)


def is_duplicate(msg_id: str, seen_ids: set[str]) -> bool:
    """Returns True if the message_id has already been processed."""
    return msg_id in seen_ids


def validate_message(root: ET.Element) -> list[str]:
    """
    Validates the XML message against the XML Naming Standard.
    Returns a list of error strings. An empty list means the message is valid.
    Duplicate detection is handled separately by is_duplicate().
    """
    errors: list[str] = []

    msg_id = root.findtext("header/message_id")
    msg_type = root.findtext("header/type")
    timestamp = root.findtext("header/timestamp")
    source = root.findtext("header/source")
    version = root.findtext("header/version")

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
        is_company = root.findtext("body/customer/is_company_linked")
        company_id = root.findtext("body/customer/company_id")
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

    # Conditional validation: new_registration
    if msg_type == "new_registration":
        email = root.findtext("body/customer/email")
        is_company = root.findtext("body/customer/is_company_linked")
        company_id = root.findtext("body/customer/company_id")
        company_name = root.findtext("body/customer/company_name")

        if not email:
            errors.append("ERROR: missing_required_field: email")
        if not is_company:
            errors.append("ERROR: missing_required_field: is_company_linked")

        if is_company == "true":
            if not company_id:
                errors.append("ERROR: company_id required when is_company_linked=true")
            if not company_name:
                errors.append("ERROR: company_name required when is_company_linked=true")

        for field in ["street", "number", "postal_code", "city", "country"]:
            if not root.findtext(f"body/customer/address/{field}"):
                errors.append(f"ERROR: missing_required_field: address.{field}")

    # Conditional validation: payment_registered
    if msg_type == "payment_registered":
        correlation_id = root.findtext("header/correlation_id")
        if not correlation_id:
            errors.append("ERROR: correlation_id required for payment_registered")

    # Conditional validation: new_registration
    if msg_type == "new_registration":
        email = root.findtext("body/customer/email")
        is_company = root.findtext("body/customer/is_company_linked")
        if not email:
            errors.append("ERROR: email required for new_registration")
        if not is_company:
            errors.append("ERROR: is_company_linked required for new_registration")
        if is_company == "true":
            company_id = root.findtext("body/customer/company_id")
            company_name = root.findtext("body/customer/company_name")
            if not company_id:
                errors.append("ERROR: company_id required when is_company_linked=true")
            if not company_name:
                errors.append("ERROR: company_name required when is_company_linked=true")

        if not root.findtext("body/registration_fee"):
            errors.append("ERROR: missing_required_field: registration_fee")

    # Conditional validation: invoice_cancelled
    if msg_type == "invoice_cancelled":
        invoice_id = root.findtext("body/invoice_id")
        customer_id = root.findtext("body/customer_id")
        if not invoice_id:
            errors.append("ERROR: invoice_id required for invoice_cancelled")
        if not customer_id:
            errors.append("ERROR: customer_id required for invoice_cancelled")

    return errors


def extract_customer_data(root: ET.Element) -> dict:
    """Extracts customer and registration data from a new_registration XML message."""
    fee_el = root.find("body/registration_fee")
    return {
        "email": root.findtext("body/customer/email"),
        "company_name": root.findtext("body/customer/company_name") or "",
        "address": {
            "street": root.findtext("body/customer/address/street"),
            "number": root.findtext("body/customer/address/number"),
            "postal_code": root.findtext("body/customer/address/postal_code"),
            "city": root.findtext("body/customer/address/city"),
            "country": root.findtext("body/customer/address/country"),
        },
        "registration_fee": root.findtext("body/registration_fee"),
        "fee_currency": fee_el.get("currency", "eur") if fee_el is not None else "eur",
    }


def send_to_dlq(
    channel: pika.channel.Channel,
    body: bytes,
    errors: list[str]
) -> None:
    """Forwards an invalid message to the Dead Letter Queue."""
    dlq = os.getenv("QUEUE_DLQ", "facturatie.dlq")
    channel.queue_declare(queue=dlq, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=dlq,
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

    # Step 1: parse XML — catch both invalid XML and bad encodings
    try:
        root = ET.fromstring(body.decode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError) as e:
        print(f"[RECEIVER] ERROR: Invalid XML or encoding — {e}")
        send_to_dlq(channel, body, [f"ERROR: invalid_xml: {e}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 2: duplicate detection based on header/message_id
    msg_id = root.findtext("header/message_id")
    if msg_id and is_duplicate(msg_id, seen_message_ids):
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

    if msg_type == "new_registration":
        customer_data = extract_customer_data(root)
        try:
            invoice_id = create_registration_invoice(customer_data)
        except Exception as e:
            send_to_dlq(channel, body, [f"ERROR: fossbilling_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        invoice_request_xml = build_invoice_request_xml(
            invoice_id=invoice_id,
            client_email=customer_data["email"],
            correlation_id=msg_id,
            company_name=customer_data["company_name"],
        )
        send_message(invoice_request_xml, routing_key="facturatie.to.mailing", channel=channel)
        print(f"[RECEIVER] invoice_request sent | invoice_id={invoice_id} | correlation_id={msg_id}")

    channel.basic_ack(delivery_tag=method.delivery_tag)


def start_receiver(queue: str | None = None) -> None:
    if queue is None:
        queue = os.getenv("QUEUE_INCOMING", "facturatie.incoming")
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_message)

    print(f"[RECEIVER] Listening on queue '{queue}'... (CTRL+C to stop)")
    # Graceful shutdown: always close the connection on exit
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[RECEIVER] Stopping consumer...")
    finally:
        connection.close()


if __name__ == "__main__":
    start_receiver()
