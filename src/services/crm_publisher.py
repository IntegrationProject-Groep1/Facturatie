import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pika
from dotenv import load_dotenv

from src.services.rabbitmq_utils import get_connection

load_dotenv()

CRM_QUEUE = "facturatie.to.crm"


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
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )


def build_cancellation_failed_xml(
    invoice_id: str,
    customer_id: str,
    correlation_id: str,
    reason: str,
) -> str:
    """Builds an invoice_cancelled XML message with status=failed to notify CRM of a blocked cancellation."""
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
    ET.SubElement(body, "status").text = "failed"
    ET.SubElement(body, "reason").text = reason

    ET.indent(root, space="    ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    )


def publish_cancellation_failed(
    invoice_id: str,
    customer_id: str,
    correlation_id: str,
    reason: str,
) -> None:
    """Publishes a failed invoice_cancelled message to CRM when a cancellation is blocked."""
    xml_message = build_cancellation_failed_xml(
        invoice_id, customer_id, correlation_id, reason
    )
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
    logging.info(
        "[CRM_PUBLISHER] cancellation_failed sent to '%s' for invoice '%s' — reason: %s",
        CRM_QUEUE, invoice_id, reason
    )
    connection.close()


def publish_invoice_cancelled(
    invoice_id: str,
    customer_id: str,
    correlation_id: str,
) -> None:
    """Publishes an invoice_cancelled message to the CRM queue."""
    xml_message = build_invoice_cancelled_xml(
        invoice_id, customer_id, correlation_id
    )
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
    print(
        f"[CRM_PUBLISHER] invoice_cancelled sent to '{CRM_QUEUE}'"
        f" for invoice '{invoice_id}'"
    )
    connection.close()
