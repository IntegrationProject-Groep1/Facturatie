from unittest.mock import MagicMock
import xml.etree.ElementTree as ET

from src.services.dlq_consumer import process_dlq_message


# --- Helpers ---

def _make_channel() -> MagicMock:
    return MagicMock()


def _make_method(delivery_tag: int = 1) -> MagicMock:
    method = MagicMock()
    method.delivery_tag = delivery_tag
    return method


def _make_properties(errors: str = "", x_death: list | None = None) -> MagicMock:
    props = MagicMock()
    headers = {}
    if errors:
        headers["errors"] = errors
    if x_death is not None:
        headers["x-death"] = x_death
    props.headers = headers
    return props


def _build_valid_xml(
    msg_type: str = "new_registration",
    msg_id: str = "msg-001",
    correlation_id: str = "corr-001",
) -> bytes:
    root = ET.Element("message")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "type").text = msg_type
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "correlation_id").text = correlation_id
    return ET.tostring(root, encoding="utf-8")


# --- Tests ---

def test_valid_xml_is_acked():
    """A parseable message must always be acked so the queue drains."""
    channel = _make_channel()
    method = _make_method()
    props = _make_properties(errors="ERROR: xsd_validation: missing field")
    body = _build_valid_xml()

    process_dlq_message(channel, method, props, body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


def test_unparseable_xml_is_still_acked():
    """Even corrupt/unreadable XML must be acked — we can only log, not fix it."""
    channel = _make_channel()
    method = _make_method()
    props = _make_properties(errors="ERROR: invalid_xml: syntax error")
    body = b"<<not valid xml>>"

    process_dlq_message(channel, method, props, body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


def test_errors_header_is_read(caplog):
    """The rejection reason from the errors header must appear in the output."""
    channel = _make_channel()
    method = _make_method()
    error_text = "ERROR: xsd_validation: missing invoice_id"
    props = _make_properties(errors=error_text)
    body = _build_valid_xml()

    process_dlq_message(channel, method, props, body)

    assert error_text in caplog.text


def test_message_fields_logged(caplog):
    """message_id and correlation_id extracted from XML must appear in the log."""
    channel = _make_channel()
    method = _make_method()
    props = _make_properties(errors="ERROR: some error")
    body = _build_valid_xml(msg_id="msg-123", correlation_id="corr-456")

    process_dlq_message(channel, method, props, body)

    assert "msg-123" in caplog.text
    assert "corr-456" in caplog.text


def test_message_type_logged(caplog):
    """The message type extracted from XML must appear in the log."""
    channel = _make_channel()
    method = _make_method()
    props = _make_properties(errors="ERROR: invalid_xml")
    body = _build_valid_xml(msg_type="payment_registered")

    process_dlq_message(channel, method, props, body)

    assert "payment_registered" in caplog.text


def test_x_death_original_queue_logged(caplog):
    """When x-death metadata is present, the original queue name must be logged."""
    channel = _make_channel()
    method = _make_method()
    x_death = [{"queue": "crm.to.facturatie", "reason": "rejected"}]
    props = _make_properties(errors="ERROR: invalid_xml", x_death=x_death)
    body = _build_valid_xml()

    process_dlq_message(channel, method, props, body)

    assert "crm.to.facturatie" in caplog.text


def test_no_headers_does_not_crash():
    """A message with no headers at all must not raise an exception."""
    channel = _make_channel()
    method = _make_method()
    props = MagicMock()
    props.headers = None
    body = _build_valid_xml()

    process_dlq_message(channel, method, props, body)

    channel.basic_ack.assert_called_once_with(delivery_tag=1)


def test_alert_line_present(caplog):
    """An [ALERT][DLQ] line must always be logged for monitoring."""
    channel = _make_channel()
    method = _make_method()
    props = _make_properties(errors="ERROR: something")
    body = _build_valid_xml()

    process_dlq_message(channel, method, props, body)

    assert "[ALERT][DLQ]" in caplog.text
