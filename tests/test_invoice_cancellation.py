import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from src.services.invoice_cancellation_receiver import validate_invoice_cancelled, process_message


def build_cancellation_xml(
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    msg_type: str = "invoice_cancelled",
    version: str = "2.0",
    timestamp: str = "2026-03-29T18:30:00Z",
    source: str = "frontend_system",
    correlation_id: str = "a23bc45d-89ef-1234-b567-1f03c3d4e580",
    invoice_id: str = "INV-2026-001",
    customer_id: str = "12345",
    reason: str = "",
) -> ET.Element:
    """Builds a minimal valid invoice_cancelled XML element for testing."""
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = msg_type
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    if correlation_id:
        ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    if invoice_id:
        ET.SubElement(body, "invoice_id").text = invoice_id
    if customer_id:
        ET.SubElement(body, "customer_id").text = customer_id
    if reason:
        ET.SubElement(body, "reason").text = reason

    return root


# --- Validation tests ---

def test_valid_message_has_no_errors():
    root = build_cancellation_xml()
    errors = validate_invoice_cancelled(root)
    assert errors == []


def test_missing_invoice_id_returns_error():
    root = build_cancellation_xml(invoice_id="")
    errors = validate_invoice_cancelled(root)
    assert any("invoice_id" in e for e in errors)


def test_missing_customer_id_returns_error():
    root = build_cancellation_xml(customer_id="")
    errors = validate_invoice_cancelled(root)
    assert any("customer_id" in e for e in errors)


def test_missing_correlation_id_returns_error():
    root = build_cancellation_xml(correlation_id="")
    errors = validate_invoice_cancelled(root)
    assert any("correlation_id" in e for e in errors)


def test_valid_message_with_reason_has_no_errors():
    root = build_cancellation_xml(reason="Customer cancelled registration")
    errors = validate_invoice_cancelled(root)
    assert errors == []


# --- Integration / process_message tests ---

def _make_method(delivery_tag: int = 1) -> MagicMock:
    method = MagicMock()
    method.delivery_tag = delivery_tag
    return method


def _build_xml_bytes(**kwargs) -> bytes:
    root = build_cancellation_xml(**kwargs)
    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'
    return xml_str.encode("utf-8")


def test_fossbilling_failure_sends_to_dlq():
    channel = MagicMock()
    method = _make_method()
    body = _build_xml_bytes()

    with patch("src.services.invoice_cancellation_receiver.fossbilling_client.cancel_invoice", return_value=False):
        process_message(channel, method, MagicMock(), body)

    # Message must be plain and DLQ must be called
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_publish.assert_called_once()
    publish_kwargs = channel.basic_publish.call_args
    assert "facturatie.dlq" in str(publish_kwargs) or "dlq" in str(publish_kwargs).lower()


def test_successful_flow_sends_to_crm():
    channel = MagicMock()
    method = _make_method()
    body = _build_xml_bytes()

    with patch("src.services.invoice_cancellation_receiver.fossbilling_client.cancel_invoice", return_value=True), \
         patch("src.services.invoice_cancellation_receiver.crm_publisher.publish_invoice_cancelled") as mock_crm:
        process_message(channel, method, MagicMock(), body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    mock_crm.assert_called_once_with("INV-2026-001", "12345", "a23bc45d-89ef-1234-b567-1f03c3d4e580")
