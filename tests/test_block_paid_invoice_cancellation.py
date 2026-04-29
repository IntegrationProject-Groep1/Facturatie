import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from src.services.rabbitmq_receiver import process_message
from src.services.fossbilling_api import get_invoice_status
from src.services.rabbitmq_sender import build_invoice_cancelled_xml


# --- Helpers (reuse pattern from test_invoice_cancellation.py) ---

def _make_method(delivery_tag: int = 1) -> MagicMock:
    method = MagicMock()
    method.delivery_tag = delivery_tag
    return method


def _build_xml_bytes(
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    invoice_id: str = "INV-2026-001",
    customer_id: str = "12345",
    correlation_id: str = "a23bc45d-89ef-1234-b567-1f03c3d4e580",
) -> bytes:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "timestamp").text = "2026-04-13T10:00:00Z"
    ET.SubElement(header, "source").text = "crm_system"
    ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = customer_id
    invoice = ET.SubElement(body, "invoice")
    ET.SubElement(invoice, "id").text = invoice_id

    ET.indent(root, space="    ")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{ET.tostring(root, encoding="unicode")}'
    return xml_str.encode("utf-8")


# --- Unit tests: get_invoice_status ---

def test_get_invoice_status_returns_paid():
    """Returns 'paid' when FossBilling reports the invoice as paid."""
    mock_result = {"result": {"status": "paid", "id": "INV-001"}}
    with patch("src.services.fossbilling_api._api_post", return_value=mock_result):
        assert get_invoice_status("INV-001") == "paid"


def test_get_invoice_status_returns_pending():
    """Returns 'pending' when FossBilling reports the invoice as pending."""
    mock_result = {"result": {"status": "unpaid", "id": "INV-001"}}
    with patch("src.services.fossbilling_api._api_post", return_value=mock_result):
        assert get_invoice_status("INV-001") == "unpaid"


def test_get_invoice_status_returns_none_when_not_found():
    """Returns None when FossBilling raises FossBillingNotFoundError."""
    from src.services.fossbilling_api import FossBillingNotFoundError
    with patch("src.services.fossbilling_api._api_post",
               side_effect=FossBillingNotFoundError("Invoice was not found")):
        assert get_invoice_status("INV-999") is None


def test_get_invoice_status_raises_on_connection_error():
    """Re-raises exception when FossBilling is unreachable (transient error)."""
    with patch("src.services.fossbilling_api._api_post", side_effect=Exception("Connection refused")):
        try:
            get_invoice_status("INV-999")
            assert False, "Expected exception to be raised"
        except Exception as e:
            assert "Connection refused" in str(e)


# --- Unit tests: build_invoice_cancelled_xml ---

def test_cancellation_failed_xml_contains_reason():
    """The failed XML must include the reason in the body."""
    xml_str = build_invoice_cancelled_xml(
        invoice_id="INV-001",
        customer_id="12345",
        correlation_id="corr-001",
        reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("body/reason") == "invoice_already_paid"


def test_cancellation_failed_xml_has_correct_type():
    """The failed XML must use type invoice_cancelled."""
    xml_str = build_invoice_cancelled_xml(
        invoice_id="INV-001",
        customer_id="12345",
        correlation_id="corr-001",
        reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("header/type") == "invoice_cancelled"


def test_cancellation_failed_xml_has_failed_status():
    """The failed XML must include status=failed in the body."""
    xml_str = build_invoice_cancelled_xml(
        invoice_id="INV-001",
        customer_id="12345",
        correlation_id="corr-001",
        reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("body/status") == "failed"


def test_cancellation_failed_xml_preserves_correlation_id():
    """The failed XML must carry the original correlation_id."""
    xml_str = build_invoice_cancelled_xml(
        invoice_id="INV-001",
        customer_id="12345",
        correlation_id="corr-abc",
        reason="invoice_already_paid",
    )
    root = ET.fromstring(xml_str.split("\n", 1)[1])
    assert root.findtext("header/correlation_id") == "corr-abc"


# --- Integration tests: process_message with status check ---

def test_paid_invoice_blocks_cancellation():
    """Cancellation of a paid invoice must be blocked — cancel_invoice must NOT be called."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status", return_value="paid"), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.cancel_invoice") as mock_cancel:

        process_message(channel, _make_method(), MagicMock(), body)

    mock_cancel.assert_not_called()


def test_paid_invoice_sends_failed_notification_to_crm():
    """When a paid invoice is blocked, a failed notification must be sent to CRM."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value="paid"), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed") as mock_failed:
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
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


def test_pending_invoice_proceeds_with_cancellation():
    """Cancellation of a pending invoice must proceed — cancel_invoice must be called."""
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
    """When invoice_id does not exist in FossBilling, a failed notification must go to CRM."""
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               return_value=None), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed") as mock_failed:
        process_message(channel, _make_method(), MagicMock(), body)

    mock_failed.assert_called_once()
    args = mock_failed.call_args
    assert "invoice_not_found" in str(args)


def test_fossbilling_unreachable_during_status_check_sends_to_dlq():
    """When FossBilling is temporarily unreachable, get_invoice_status raises an exception.
    The message must be sent to DLQ and nacked so it can be replayed later — not lost.
    """
    channel = MagicMock()
    body = _build_xml_bytes()

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.fossbilling_client.get_invoice_status",
               side_effect=Exception("Connection refused")):
        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()


def test_empty_invoice_id_sends_to_dlq():
    """A message with an empty invoice_id must be rejected to the DLQ — nothing to look up."""
    channel = MagicMock()
    body = _build_xml_bytes(invoice_id="")

    with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
         patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
         patch("src.services.rabbitmq_receiver.crm_publisher.publish_cancellation_failed"): # VOEG DEZE LIJN TOE

        process_message(channel, _make_method(), MagicMock(), body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()


def test_already_cancelled_invoice_blocks_cancellation():
    """Cancellation of an already-cancelled invoice must be blocked."""
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
