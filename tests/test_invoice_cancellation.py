"""
Tests voor de invoice_cancelled message flow.
Consolideert: test_invoice_cancellation.py + test_block_paid_invoice_cancellation.py
"""
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from src.services.rabbitmq_receiver import validate_invoice_cancelled, process_message
from src.services.fossbilling_api import get_invoice_status
from src.services.crm_publisher import build_cancellation_failed_xml


# ── XML builder ───────────────────────────────────────────────────────────────

def _build_xml_bytes(
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    version: str = "2.0",
    timestamp: str = "2026-04-13T10:00:00Z",
    source: str = "crm_system",
    correlation_id: str = "a23bc45d-89ef-1234-b567-1f03c3d4e580",
    invoice_number: str = "INV-2026-001",
    reason: str = "",
) -> bytes:
    """
    Bouwt een invoice_cancelled XML conform de nieuwe XSD (contract §11.2).
    Body heeft <invoice_number> en optioneel <reason> — geen customer/invoice blokken meer.
    master_uuid is verwijderd uit de header (contract #90).
    """
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    # master_uuid VERWIJDERD — verboden in alle headers (contract #90)
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    if correlation_id:
        ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    # Nieuwe structuur: <invoice_number> i.p.v. <invoice><id> (conform invoice_cancelled.xsd)
    if invoice_number:
        ET.SubElement(body, "invoice_number").text = invoice_number
    if reason:
        ET.SubElement(body, "reason").text = reason

    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'
    return xml_str.encode("utf-8")


def _make_method(delivery_tag: int = 1) -> MagicMock:
    method = MagicMock()
    method.delivery_tag = delivery_tag
    return method


# ── validate_invoice_cancelled ────────────────────────────────────────────────

def test_valid_message_has_no_errors():
    root = ET.fromstring(_build_xml_bytes().decode())
    errors = validate_invoice_cancelled(root)
    assert errors == []


def test_invalid_version_returns_error():
    root = ET.fromstring(_build_xml_bytes(version="1.0").decode())
    errors = validate_invoice_cancelled(root)
    assert any("version" in e for e in errors)


# ── get_invoice_status unit tests ─────────────────────────────────────────────

def test_get_invoice_status_returns_paid():
    with patch("src.services.fossbilling_api._api_post",
               return_value={"result": {"status": "paid", "id": "INV-001"}}):
        assert get_invoice_status("INV-001") == "paid"


def test_get_invoice_status_returns_unpaid():
    with patch("src.services.fossbilling_api._api_post",
               return_value={"result": {"status": "unpaid", "id": "INV-001"}}):
        assert get_invoice_status("INV-001") == "unpaid"


def test_get_invoice_status_returns_none_when_not_found():
    from src.services.fossbilling_api import FossBillingNotFoundError
    with patch("src.services.fossbilling_api._api_post",
               side_effect=FossBillingNotFoundError("Invoice was not found")):
        assert get_invoice_status("INV-999") is None


def test_get_invoice_status_raises_on_connection_error():
    with patch("src.services.fossbilling_api._api_post",
               side_effect=Exception("Connection refused")):
        with pytest.raises(Exception, match="Connection refused"):
            get_invoice_status("INV-999")


# ── build_cancellation_failed_xml unit tests ──────────────────────────────────

def test_cancellation_failed_xml_contains_reason():
    xml_str = build_cancellation_failed_xml(
        invoice_id="INV-001", customer_id="12345",
        correlation_id="corr-001", reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("body/reason") == "invoice_already_paid"


def test_cancellation_failed_xml_has_correct_type():
    xml_str = build_cancellation_failed_xml(
        invoice_id="INV-001", customer_id="12345",
        correlation_id="corr-001", reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("header/type") == "invoice_cancelled"


def test_cancellation_failed_xml_has_failed_status():
    xml_str = build_cancellation_failed_xml(
        invoice_id="INV-001", customer_id="12345",
        correlation_id="corr-001", reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("body/status") == "failed"


def test_cancellation_failed_xml_preserves_correlation_id():
    xml_str = build_cancellation_failed_xml(
        invoice_id="INV-001", customer_id="12345",
        correlation_id="corr-abc", reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("header/correlation_id") == "corr-abc"


# ── process_message integratie ────────────────────────────────────────────────

def test_fossbilling_failure_sends_to_dlq():
    channel = MagicMock()
    body = _build_xml_bytes(msg_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="unpaid"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice",
               return_value=False):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_publish.assert_called_once()
    assert "dlq" in str(channel.basic_publish.call_args).lower()


def test_successful_flow_acks_and_notifies_crm():
    channel = MagicMock()
    body = _build_xml_bytes(msg_id="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="unpaid"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice",
               return_value=True), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_invoice_cancelled") as mock_crm:
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    mock_crm.assert_called_once()
    # Receiver leest invoice_number uit body/invoice_number (contract §11.2)
    args = mock_crm.call_args.args
    assert args[0] == "INV-2026-001"
    assert args[2] == "a23bc45d-89ef-1234-b567-1f03c3d4e580"  # correlation_id


def test_paid_invoice_blocks_cancellation():
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="paid"), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice") as mock_cancel:
        process_message(channel, _make_method(), MagicMock(), body)

    mock_cancel.assert_not_called()


def test_paid_invoice_sends_failed_notification_to_crm():
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="paid"), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed") as mock_failed:
        process_message(channel, _make_method(), MagicMock(), body)

    mock_failed.assert_called_once()
    assert "invoice_already_paid" in str(mock_failed.call_args)


def test_paid_invoice_is_acked_not_sent_to_dlq():
    """Geblokkeerde annulering is een geldig bericht — moet geacked worden, niet naar DLQ."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="paid"), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


def test_pending_invoice_proceeds_with_cancellation():
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="unpaid"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice",
               return_value=True), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_invoice_cancelled"):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)


def test_invoice_not_found_sends_error_to_crm():
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value=None), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed") as mock_failed:
        process_message(channel, _make_method(), MagicMock(), body)

    mock_failed.assert_called_once()
    assert "invoice_not_found" in str(mock_failed.call_args)


def test_fossbilling_unreachable_sends_to_dlq():
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               side_effect=Exception("Connection refused")):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()


def test_empty_invoice_number_sends_to_dlq():
    """Leeg invoice_number moet naar DLQ — niets om op te zoeken."""
    channel = MagicMock()
    body = _build_xml_bytes(invoice_number="")

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()


def test_already_cancelled_invoice_blocks_cancellation():
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="cancelled"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice") as mock_cancel, \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"):
        process_message(channel, _make_method(), MagicMock(), body)

    mock_cancel.assert_not_called()
