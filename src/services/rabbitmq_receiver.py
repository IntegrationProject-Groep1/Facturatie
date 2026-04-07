import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv
import os
import xml.etree.ElementTree as ET

from .fossbilling_api import create_registration_invoice
from .rabbitmq_sender import build_invoice_request_xml, send_message
from src.utils.xml_validator import validate_xml
from src.services.rabbitmq_utils import get_connection, send_to_dlq

# Valid values per XML Naming Standard (all lowercase snake_case)
VALID_TYPES: set[str] = {
    "consumption_order", "payment_registered",
    "heartbeat", "new_registration"
}
VALID_VAT_RATES: set[str] = {"6", "12", "21"}
VALID_PAYMENT_METHODS: set[str] = {"company_link", "on_site", "online"}

# In-memory set for duplicate detection based on header/message_id
# Note: persists only during runtime; will be migrated to MySQL in a later sprint.
seen_message_ids: set[str] = set()

load_dotenv()


def is_duplicate(msg_id: str, seen_ids: set[str]) -> bool:
    """Returns True if the message_id has already been processed."""
    return msg_id in seen_ids


def extract_customer_data(root: ET.Element) -> dict:
    """Extracts customer and registration data from a new_registration XML message."""
    fee_el = root.find("body/registration_fee")
    return {
        "email": root.findtext("body/customer/email"),
        "first_name": root.findtext("body/customer/first_name") or "",
        "last_name": root.findtext("body/customer/last_name") or "",
        "company_name": root.findtext("body/customer/company_name") or "",
        "address": {
            field: root.findtext(f"body/customer/address/{field}") or ""
            for field in ["street", "number", "postal_code", "city", "country"]
        },
        "registration_fee": root.findtext("body/registration_fee"),
        "fee_currency": fee_el.get("currency", "eur") if fee_el is not None else "eur",
    }


def process_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    properties: pika.spec.BasicProperties,
    body: bytes
) -> None:
    print("\n[RECEIVER] Message received")

    # Step 1: parse XML — catch both invalid XML and bad encodings
    try:
        xml_str = body.decode("utf-8")
        root = ET.fromstring(xml_str)
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
    msg_type = root.findtext("header/type") or "unknown"
    is_valid, error_msg = validate_xml(xml_str, msg_type)

    if not is_valid:
        print(f"[RECEIVER] ERROR: xsd_validation_failed — {error_msg}")
        send_to_dlq(channel, body, [f"ERROR: xsd_validation: {error_msg}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 4: mark message_id as seen
    if msg_id:
        seen_message_ids.add(msg_id)

    print(
        f"[RECEIVER] Valid message received"
        f" | type={msg_type} | message_id={msg_id}"
    )

    # Process new customer registration
    if msg_type == "new_registration":
        customer_data = extract_customer_data(root)
        try:
            # Create registration invoice in FossBilling
            invoice_id = create_registration_invoice(customer_data)
        except Exception as e:
            # Handle failure and move to Dead Letter Queue
            send_to_dlq(channel, body, [f"ERROR: fossbilling_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # Build and send XML for the Mailing Service
        invoice_request_xml = build_invoice_request_xml(
            invoice_id=invoice_id,
            client_email=customer_data["email"],
            correlation_id=msg_id,
            company_name=customer_data.get("company_name", ""),
        )

        send_message(
            invoice_request_xml,
            routing_key="facturatie.to.mailing",
            channel=channel
        )

        print(
            f"[RECEIVER] invoice_request sent | invoice_id={invoice_id}"
            f" | correlation_id={msg_id}"
        )

        channel.basic_ack(delivery_tag=method.delivery_tag)

    # Note: If adding more msg_types (like consumption_order), add an elif here
    # with its own channel.basic_ack() at the end of that block.


def start_receiver(queue: str | None = None) -> None:
    if queue is None:
        # Check environment variable, default to the new CRM queue name if not set
        queue = os.getenv("QUEUE_INCOMING", "crm.to.facturatie")

    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_message)

    print(f"[RECEIVER] Listening on queue '{queue}'... (CTRL+C to stop)")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[RECEIVER] Stopping consumer...")
    finally:
        connection.close()


if __name__ == "__main__":
    start_receiver()
