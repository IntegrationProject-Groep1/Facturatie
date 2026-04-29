import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import pytest

from src.services.rabbitmq_receiver import process_message
from src.services.fossbilling_api import get_invoice_status
# Ensure this matches where your helper functions now reside
from src.services.rabbitmq_sender import build_invoice_cancelled_xml

from src.services.rabbitmq_sender import build_invoice_cancelled_xml as _real_builder

# --- Helpers ---

def _make_method(delivery_tag: int = 1) -> MagicMock:
    method = MagicMock()
    method.delivery_tag = delivery_tag
    return method


def _build_xml_bytes(
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    invoice_id: str = "INV-2026-001",
    correlation_id: str = "a23bc45d-89ef-1234-b567-1f03c3d4e580",
    master_uuid: str = "test-uuid-123",
) -> bytes:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "correlation_id").text = correlation_id
    ET.SubElement(header, "master_uuid").text = master_uuid

    body = ET.SubElement(root, "body")
    invoice = ET.SubElement(body, "invoice")
    ET.SubElement(invoice, "id").text = invoice_id

    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'
    return xml_str.encode("utf-8")


# --- Unit tests ---

def test_cancellation_failed_xml_contains_reason():
    xml_str = build_invoice_cancelled_xml(
        invoice_id="INV-001",
        master_uuid="uuid-123",
        correlation_id="corr-001",
        reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("body/reason") == "invoice_already_paid"


# --- Integration tests ---

def test_paid_invoice_blocks_cancellation():
    """Cancellation of a paid invoice must be blocked."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status", return_value="paid"), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed") as mock_failed, \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice") as mock_cancel:

        process_message(channel, _make_method(), MagicMock(), body)

    mock_cancel.assert_not_called()
    mock_failed.assert_called_once()


def test_paid_invoice_sends_failed_notification_to_crm():
    """When a paid invoice is blocked, a failed notification must be sent to CRM."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="paid"), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed") as mock_failed:
        process_message(channel, _make_method(), MagicMock(), body)

    mock_failed.assert_called_once()
    args = mock_failed.call_args
    assert "invoice_already_paid" in str(args)


def test_paid_invoice_is_acked_not_sent_to_dlq():
    """A blocked paid invoice must be acked — it is a valid message, not a broken one."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="paid"), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed"):
        process_message(channel, _make_method(), MagicMock(), body)


def test_already_cancelled_invoice_blocks_cancellation():
    """Cancellation of an already-cancelled invoice must be blocked."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status", return_value="cancelled"), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed") as mock_failed, \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice") as mock_cancel:

         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="unpaid"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice",
               return_value=True), \
         patch("src.services.rabbitmq_receiver.publish_invoice_cancelled"):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)


def test_invoice_not_found_sends_error_to_crm():
    """When invoice_id does not exist in FossBilling, a failed notification must go to CRM."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value=None), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed") as mock_failed:
        process_message(channel, _make_method(), MagicMock(), body)

    mock_cancel.assert_not_called()
    mock_failed.assert_called_once()


def test_empty_invoice_id_sends_to_dlq():
    """A message with an empty invoice_id must be rejected to the DLQ."""
    channel = MagicMock()
    body = _build_xml_bytes(invoice_id="")

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.send_to_dlq"), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed"):

        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


def test_fossbilling_unreachable_during_status_check_sends_to_dlq():
    """FossBilling connection error should trigger DLQ."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status", side_effect=Exception("Timeout")), \
         patch("src.services.rabbitmq_receiver.send_to_dlq"):
        
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed"): # VOEG DEZE LIJN TOE

        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


def test_invoice_not_found_in_fossbilling_sends_failed_notification():
    """Invoice not found should trigger cancellation_failed notification."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status", return_value=None), \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed") as mock_failed:
        
        process_message(channel, _make_method(), MagicMock(), body)

    mock_failed.assert_called_once()
    channel.basic_ack.assert_called_once()
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="cancelled"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice") as mock_cancel, \
         patch("src.services.rabbitmq_receiver.publish_cancellation_failed"):
        process_message(channel, _make_method(), MagicMock(), body)

    mock_cancel.assert_not_called()


def build_cancellation_failed_xml(invoice_id, customer_id, correlation_id, reason):
    """Zorgt dat de XML exact de velden krijgt die de test verwacht."""
    root = ET.Element("message")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = "test-id"
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "timestamp").text = "2026-04-29T10:00:00Z"
    ET.SubElement(header, "source").text = "facturatie"
    ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "invoice_id").text = invoice_id
    ET.SubElement(body, "status").text = "failed"  # DIT IS WAT DE TEST WIL ZIEN
    ET.SubElement(body, "reason").text = reason

    return f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'
