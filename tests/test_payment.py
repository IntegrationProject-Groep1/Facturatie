import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from src.services.rabbitmq_sender import send_message


def mark_invoice_as_paid(invoice_id, amount="15.00"):
    # Generate UUIDs (must match the regex pattern in the XSD)
    msg_id = str(uuid.uuid4())
    corr_id = str(uuid.uuid4())
    trans_id = f"TRANS-{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    due_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    root = ET.Element("message")

    # 1. Header — volgorde per contract §6.6: message_id → type → source → timestamp → version
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "type").text = "payment_registered"
    ET.SubElement(header, "source").text = "kassa"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "correlation_id").text = corr_id

    # 2. Body (Must conform to PaymentRegisteredBodyType [cite: 10])
    body = ET.SubElement(root, "body")
    ET.SubElement(body, "payment_context").text = "consumption"

    # Invoice block [cite: 12]
    invoice = ET.SubElement(body, "invoice")
    ET.SubElement(invoice, "id").text = str(invoice_id)
    ET.SubElement(invoice, "status").text = "paid"  # Enumeration

    # Amount paid (with mandatory currency code [cite: 12])
    amount_paid = ET.SubElement(invoice, "amount_paid", currency="eur")
    amount_paid.text = amount

    ET.SubElement(invoice, "due_date").text = due_date

    # Transaction block [cite: 12]
    transaction = ET.SubElement(body, "transaction")
    ET.SubElement(transaction, "id").text = trans_id
    ET.SubElement(transaction, "payment_method").text = "online"  # Enumeration

    # Generate XML
    ET.indent(root, space="    ")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")

    print(f"[TEST] Sending payment for Invoice ID: {invoice_id}")
    print(f"[TEST] Message ID: {msg_id}")
    send_message(xml_str, routing_key="facturatie.incoming")


if __name__ == "__main__":
    mark_invoice_as_paid(2)
