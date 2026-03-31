import os
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pika
from dotenv import load_dotenv

load_dotenv()

CRM_QUEUE = "crm"


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


def build_invoice_cancelled_xml(
    invoice_id: str,
    customer_id: str,
    correlation_id: str,
) -> str:
    """Builds an invoice_cancelled XML message to notify the CRM system."""
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = "facturatie_system"
    ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "invoice_id").text = invoice_id
    ET.SubElement(body, "customer_id").text = customer_id

    ET.indent(root, space="    ")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'


def publish_invoice_cancelled(
    invoice_id: str,
    customer_id: str,
    correlation_id: str,
) -> None:
    """Publishes an invoice_cancelled message to the CRM queue."""
    xml_message = build_invoice_cancelled_xml(invoice_id, customer_id, correlation_id)
    connection = get_connection()
    channel = connection.channel()
    channel.queue_declare(queue=CRM_QUEUE, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=CRM_QUEUE,
        body=xml_message.encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml",
        )
    )
    print(f"[CRM_PUBLISHER] invoice_cancelled sent to '{CRM_QUEUE}' for invoice '{invoice_id}'")
    connection.close()
