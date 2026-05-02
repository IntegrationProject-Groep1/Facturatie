"""
Sends an invoice_cancelled message to facturatie.incoming to manually test
the paid invoice blocking logic.

Usage:
    python scripts/send_cancellation.py <invoice_id>

Example:
    python scripts/send_cancellation.py 14
"""
import sys
import uuid
import pika
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

invoice_id = sys.argv[1] if len(sys.argv) > 1 else "14"

msg_id = str(uuid.uuid4())
correlation_id = str(uuid.uuid4())
timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

root = ET.Element("message")

header = ET.SubElement(root, "header")
ET.SubElement(header, "message_id").text = msg_id
ET.SubElement(header, "version").text = "2.0"
ET.SubElement(header, "type").text = "invoice_cancelled"
ET.SubElement(header, "timestamp").text = timestamp
ET.SubElement(header, "source").text = "crm_system"
ET.SubElement(header, "correlation_id").text = correlation_id

body = ET.SubElement(root, "body")

customer = ET.SubElement(body, "customer")
ET.SubElement(customer, "id").text = "12345"
ET.SubElement(customer, "email").text = "test@example.com"
ET.SubElement(customer, "is_company_linked").text = "false"
address = ET.SubElement(customer, "address")
ET.SubElement(address, "street").text = "Teststraat"
ET.SubElement(address, "number").text = "1"
ET.SubElement(address, "postal_code").text = "1000"
ET.SubElement(address, "city").text = "Brussels"
ET.SubElement(address, "country").text = "be"

invoice = ET.SubElement(body, "invoice")
ET.SubElement(invoice, "id").text = invoice_id
ET.SubElement(invoice, "status").text = "pending"
amount_el = ET.SubElement(invoice, "amount_paid")
amount_el.text = "150.00"
amount_el.set("currency", "eur")
ET.SubElement(invoice, "due_date").text = "2026-05-01"
ET.SubElement(invoice, "cancellation_reason").text = "Customer requested cancellation"

ET.indent(root, space="    ")
xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'

creds = pika.PlainCredentials(os.getenv("RABBITMQ_USER"), os.getenv("RABBITMQ_PASSWORD"))
params = pika.ConnectionParameters(
    host=os.getenv("RABBITMQ_HOST"),
    port=int(os.getenv("RABBITMQ_PORT", 5672)),
    virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
    credentials=creds
)
conn = pika.BlockingConnection(params)
ch = conn.channel()
queue = os.getenv("QUEUE_INCOMING", "facturatie.incoming")
ch.queue_declare(queue=queue, durable=True)
ch.basic_publish(
    exchange="",
    routing_key=queue,
    body=xml_str.encode("utf-8"),
    properties=pika.BasicProperties(delivery_mode=2, content_type="application/xml")
)
print(f"Sent invoice_cancelled for invoice_id={invoice_id} to {queue}")
print(f"message_id={msg_id} | correlation_id={correlation_id}")
conn.close()
