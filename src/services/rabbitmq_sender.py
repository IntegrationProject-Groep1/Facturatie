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

BILLING_WEB_URL = os.getenv("BILLING_WEB_URL", "https://portal.yourdomain.com").rstrip("/")


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
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "type").text = "consumption_order"
    ET.SubElement(header, "version").text = "2.0"

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
    logging.info("[SENDER] Message sent to queue '%s'", routing_key)

    # PROTOCOL: Outbound Message (The "Tracker" Log)
    # Skip internal monitoring queues to avoid infinite recursion.
    _INTERNAL_QUEUES = {"logs", "heartbeat", "errors.facturatie"}
    if routing_key not in _INTERNAL_QUEUES:
        try:
            temp_root = ET.fromstring(xml_message)
            msg_type = temp_root.findtext("header/type") or "unknown"
            corr_id = (
                temp_root.findtext("header/correlation_id")
                or temp_root.findtext("header/message_id")
                or "N/A"
            )

            action_map = {
                "invoice_available": "invoice",
                "invoice_cancelled": "invoice",
                "payment_registered": "payment",
                "send_mailing": "email",
                "system_error": "system_error",
                "heartbeat": "session"
            }
            log_action = action_map.get(msg_type, "invoice")

            send_log(
                level="info",
                action=log_action,
                message=f"Published {msg_type} to {routing_key}. CorrelationID: {corr_id}",
                channel=channel
            )
        except Exception as log_err:
            logging.warning("[SENDER] Metadata extraction for Tracker log failed: %s", log_err)

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
    identity_uuid: str = "",
    subject: str = "Uw factuur staat klaar",
    source: str = "facturatie",
) -> str:
    """
    Builds a send_mailing XML message to be sent to the Mailing team.
    Queue: facturatie.to.mailing
    """
    import json

    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    billing_web_base = BILLING_WEB_URL
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
    ET.SubElement(body, "mail_type").text = "invoice_ready"

    recipients = ET.SubElement(body, "recipients")
    recipient = ET.SubElement(recipients, "recipient")
    ET.SubElement(recipient, "email").text = recipient_email
    ET.SubElement(recipient, "identity_uuid").text = identity_uuid or customer_id
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
    identity_uuid: str,
    amount: str,
    currency: str,
    payment_method: str,
    paid_at: str | None = None,
    source: str = "facturatie",
    status: str = "paid",
    due_date: str = "",
    transaction_id: str = "",
    payment_context: str = "online_invoice"
) -> str:
    """
    Builds a payment_registered confirmation XML to publish after a successful
    payment has been processed in FossBilling.
    Conforms to the standard v2.0 message format (§8.2).
    Sent to queue: crm.incoming
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
    ET.SubElement(body, "identity_uuid").text = identity_uuid

    invoice = ET.SubElement(body, "invoice")
    ET.SubElement(invoice, "id").text = invoice_id
    amount_el = ET.SubElement(invoice, "amount_paid")
    amount_el.text = amount
    amount_el.set("currency", currency_lower)
    ET.SubElement(invoice, "status").text = status
    ET.SubElement(invoice, "due_date").text = due_date

    ET.SubElement(body, "payment_context").text = payment_context

    transaction = ET.SubElement(body, "transaction")
    ET.SubElement(transaction, "id").text = transaction_id or str(uuid.uuid4())
    ET.SubElement(transaction, "payment_method").text = payment_method

    ET.indent(root, space="    ")
    xml_str = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )

    # Validate against unified XSD (Section 8.2)
    is_valid, error_msg = validate_xml(xml_str, "payment_registered")
    if not is_valid:
        raise ValueError(
            f"[SENDER] payment_registered XSD validation failed: {error_msg}"
        )

    return xml_str


CRM_QUEUE = os.getenv("QUEUE_CRM", "crm.incoming")
FRONTEND_QUEUE = os.getenv("QUEUE_FRONTEND", "facturatie.to.frontend")


def build_invoice_link_xml(
    invoice_id: str,
    master_uuid: str,
    source: str = "facturatie",
) -> str:
    """
    Builds an invoice_available XML message to be sent to the Frontend team.
    Queue: facturatie.to.frontend
    """
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    billing_web_base = os.getenv("BILLING_WEB_URL", "https://portal.yourdomain.com").rstrip("/")
    pdf_url = f"{billing_web_base}/invoice/{invoice_id}"

    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "type").text = "invoice_available"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "identity_uuid").text = master_uuid
    ET.SubElement(body, "invoice_id").text = invoice_id
    ET.SubElement(body, "pdf_url").text = pdf_url

    ET.indent(root, space="    ")
    xml_str = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )

    is_valid, error_msg = validate_xml(xml_str, "invoice_available")
    if not is_valid:
        raise ValueError(f"[SENDER] invoice_available XSD validation failed: {error_msg}")

    return xml_str


def publish_invoice_link(
    invoice_id: str,
    master_uuid: str,
    channel: pika.channel.Channel | None = None,
) -> None:
    """Publishes an invoice_available notification to the Frontend queue."""
    xml_message = build_invoice_link_xml(invoice_id, master_uuid)
    send_message(xml_message, routing_key=FRONTEND_QUEUE, channel=channel)
    logging.info(
        "[SENDER] invoice_link sent to '%s' | invoice_id=%s | master_uuid=%s",
        FRONTEND_QUEUE, invoice_id, master_uuid,
    )


def build_invoice_cancelled_xml(
    invoice_id: str,
    customer_id: str,
    reason: str | None = None,
) -> str:
    """Builds an invoice_cancelled XML message to notify the CRM system."""
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = "facturatie"
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "invoice_id").text = invoice_id
    ET.SubElement(body, "user_id").text = customer_id
    if reason:
        ET.SubElement(body, "reason").text = reason

    ET.indent(root, space="    ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )


def publish_invoice_cancelled(
    invoice_id: str,
    customer_id: str,
    channel: pika.channel.Channel | None = None,
) -> None:
    """Publishes an invoice_cancelled notification to the CRM queue."""
    xml_message = build_invoice_cancelled_xml(invoice_id, customer_id)
    send_message(xml_message, routing_key=CRM_QUEUE, channel=channel)
    logging.info(
        "[SENDER] invoice_cancelled sent to '%s' | invoice_id=%s",
        CRM_QUEUE, invoice_id,
    )


def publish_cancellation_failed(
    invoice_id: str,
    customer_id: str,
    reason: str,
    channel: pika.channel.Channel | None = None,
) -> None:
    """Publishes a failed invoice_cancelled message to CRM when a cancellation is blocked."""
    xml_message = build_invoice_cancelled_xml(invoice_id, customer_id, reason)
    send_message(xml_message, routing_key=CRM_QUEUE, channel=channel)
    logging.info(
        "[SENDER] cancellation_failed sent to '%s' | invoice_id=%s | reason=%s",
        CRM_QUEUE, invoice_id, reason,
    )


def build_system_error_xml(
    error_code: str,
    message: str,
    severity: str = "critical",
    correlation_id: str | None = None,
    source: str = "facturatie",
) -> str:
    """
    Builds a system_error XML message (Section 2.6).
    """
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("message")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "type").text = "system_error"
    ET.SubElement(header, "version").text = "2.0"
    if correlation_id:
        ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "error_code").text = error_code
    ET.SubElement(body, "message").text = message
    ET.SubElement(body, "severity").text = severity

    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'

    # Pre-validation against system_error.xsd
    is_valid, err = validate_xml(xml_str, "system_error")
    if not is_valid:
        logging.error("[SENDER] system_error XSD validation failed locally: %s", err)

    return xml_str


def send_system_error(
    error_code: str,
    message: str,
    severity: str = "critical",
    correlation_id: str | None = None,
    channel: pika.channel.Channel | None = None,
) -> None:
    """Publishes a system_error to the errors.facturatie queue."""
    xml = build_system_error_xml(error_code, message, severity, correlation_id)
    send_message(xml, routing_key="errors.facturatie", channel=channel)


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


def build_log_xml(
    level: str,
    action: str,
    message: str,
    source: str = "facturatie",
) -> str:
    """
    Builds a log XML message (Section 3.5).
    """
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("message")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "type").text = "log"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "level").text = level
    ET.SubElement(body, "action").text = action
    ET.SubElement(body, "message").text = message

    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'

    # Pre-validation against log.xsd
    is_valid, err = validate_xml(xml_str, "log")
    if not is_valid:
        logging.error("[SENDER] log XSD validation failed locally: %s", err)

    return xml_str


def send_log(level: str, action: str, message: str, channel: pika.channel.Channel | None = None) -> None:
    """Sends a validated log message to the central 'logs' queue."""
    try:
        xml = build_log_xml(level, action, message)
        send_message(xml, routing_key="logs", channel=channel)
    except Exception as e:
        logging.error("[SENDER] Failed to send log: %s", e)


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
