import pika
import pika.channel
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import logging
from src.services.rabbitmq_utils import get_connection
from src.utils.xml_validator import validate_xml

# Load environment variables from the .env file
load_dotenv()


def build_consumption_order_xml(
    customer_id: str,
    items: list[dict],
    is_company_linked: bool = False,
    company_id: str = "",
    company_name: str = "",
    source: str = "kassa_bar_01",
) -> str:
    """
    Builds a consumption_order XML message using ElementTree so all input
    values are automatically escaped, preventing XML injection.
    """
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("message")

    # Build header
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "consumption_order"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source

    # Build body — customer
    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = customer_id
    ET.SubElement(customer, "is_company_linked").text = (
        "true" if is_company_linked else "false"
    )
    # company_id and company_name are only included when is_company_linked is True
    if is_company_linked:
        ET.SubElement(customer, "company_id").text = company_id
        ET.SubElement(customer, "company_name").text = company_name

    ET.SubElement(customer, "email").text = ""
    addr = ET.SubElement(customer, "address")
    for field in ["street", "number", "postal_code", "city"]:
        ET.SubElement(addr, field).text = ""
    ET.SubElement(addr, "country").text = "be"

    ET.SubElement(body, "payment_method").text = "company_link"

    # Build body — items
    items_el = ET.SubElement(body, "items")
    for item in items:
        item_el = ET.SubElement(items_el, "item")
        ET.SubElement(item_el, "id").text = str(item["id"])
        ET.SubElement(item_el, "description").text = str(item["description"])
        ET.SubElement(item_el, "quantity").text = str(item["quantity"])
        unit_price_el = ET.SubElement(item_el, "unit_price")
        unit_price_el.text = str(item["unit_price"])
        unit_price_el.set("currency", "eur")  # lowercase per XML Naming Standard
        ET.SubElement(item_el, "vat_rate").text = str(item["vat_rate"])

    ET.indent(root, space="    ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )


def send_message(
    xml_message: str,
    routing_key: str | None = None,
    channel: pika.channel.Channel | None = None,
) -> None:
    """
    Publishes an XML message to a RabbitMQ queue.
    Pass an existing channel to reuse a connection across multiple messages.
    If no channel is provided, a temporary connection is opened and closed
    automatically.
    routing_key defaults to QUEUE_INCOMING (facturatie.incoming).
    Use 'heartbeat' for the central monitoring queue (no team prefix).
    Use 'facturatie.to.<team>' for outgoing messages to other teams.
    """
    if routing_key is None:
        routing_key = os.getenv("QUEUE_INCOMING", "facturatie.incoming")
    connection = None
    if channel is None:
        # No channel provided — open a single-use connection
        connection = get_connection()
        channel = connection.channel()

    channel.queue_declare(queue=routing_key, durable=True)
    # delivery_mode=2 ensures the message is persisted to disk
    channel.basic_publish(
        exchange="",
        routing_key=routing_key,
        body=xml_message.encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml"
        )
    )
    print(f"[SENDER] Message sent to queue '{routing_key}'")

    # Only close if we opened the connection here
    if connection is not None:
        connection.close()


def build_invoice_created_notification_xml(
    invoice_id: str,
    recipient_email: str,
    correlation_id: str,
    first_name: str = "",
    last_name: str = "",
    customer_id: str = "",
    subject: str = "Uw factuur staat klaar",
    source: str = "facturatie",
) -> str:
    """
    Builds a send_mailing XML message to be sent to the Mailing team.
    Queue: crm.to.mailing
    """
    import json

    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    billing_web_base = os.getenv("BILLING_WEB_URL", "https://portal.yourdomain.com").rstrip("/")
    pdf_url = f"{billing_web_base}/invoice/{invoice_id}"

    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "type").text = "send_mailing"
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "campaign_id").text = f"foss-invoice-{invoice_id}"
    ET.SubElement(body, "subject").text = subject
    ET.SubElement(body, "template_id").text = "tmpl-invoice-ready"
    ET.SubElement(body, "mail_type").text = "invoice_ready"

    recipients = ET.SubElement(body, "recipients")
    recipient = ET.SubElement(recipients, "recipient")
    ET.SubElement(recipient, "email").text = recipient_email
    ET.SubElement(recipient, "customer_id").text = customer_id or invoice_id
    contact = ET.SubElement(recipient, "contact")
    ET.SubElement(contact, "first_name").text = first_name
    ET.SubElement(contact, "last_name").text = last_name

    ET.SubElement(body, "template_data").text = json.dumps({
        "invoice_id": invoice_id,
        "pdf_url": pdf_url,
    })

    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'

    is_valid, error_msg = validate_xml(xml_str, "send_mailing")
    if not is_valid:
        raise ValueError(
            f"[SENDER] send_mailing XSD validation failed: {error_msg}"
        )

    return xml_str


def build_payment_confirmed_xml(
    invoice_id: str,
    customer_id: str,
    amount: str,
    currency: str,
    payment_method: str,
    paid_at: str | None = None,
    source: str = "facturatie",
) -> str:
    """
    Builds a payment_registered confirmation XML to publish after a successful
    payment has been processed in FossBilling.
    Sent to queue: facturatie.to.crm
    and payement_registered_outgoing.xsd.
    """
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if paid_at is None:
        paid_at = timestamp

    # Enforce eur — log a warning if something else was passed
    currency_lower = currency.lower()
    if currency_lower != "eur":
        logging.warning(
            "[SENDER] build_payment_confirmed_xml: unexpected currency '%s', forcing 'eur'",
            currency,
        )
        currency_lower = "eur"

    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "type").text = "payment_registered"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "invoice_id").text = invoice_id
    ET.SubElement(body, "customer_id").text = customer_id
    amount_el = ET.SubElement(body, "amount_paid")
    amount_el.text = amount
    amount_el.set("currency", currency_lower)
    ET.SubElement(body, "payment_method").text = payment_method
    ET.SubElement(body, "paid_at").text = paid_at

    ET.indent(root, space="    ")
    xml_str = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )

    # Validate against XSD before sending
    is_valid, error_msg = validate_xml(xml_str, "payment_registered_outgoing")
    if not is_valid:
        raise ValueError(
            f"[SENDER] payment_registered (outgoing) XSD validation failed: {error_msg}"
        )

    return xml_str


def send_error_to_monitor(error_message: str) -> None:
    """
    Sends an error notification to the central error queue (errors.facturatie).
    Call this when a critical failure occurs (e.g. database offline, API
    failure). Per section 7 of the Sidecar Architecture.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("error")
    ET.SubElement(root, "system").text = "facturatie"
    ET.SubElement(root, "timestamp").text = timestamp
    ET.SubElement(root, "message").text = error_message

    xml_error = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )
    send_message(xml_error, routing_key="errors.facturatie")


if __name__ == "__main__":
    items = [
        {
            "id": "BEV-001",
            "description": "Coffee",
            "quantity": 2,
            "unit_price": "2.50",
            "vat_rate": 21
        }
    ]
    xml = build_consumption_order_xml(
        customer_id="12345",
        items=items,
        is_company_linked=True,
        company_id="FOSS-CUST-102",
        company_name="Example NV",
    )
    print("[SENDER] XML:\n", xml)
    send_message(xml)
