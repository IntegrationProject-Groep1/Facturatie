import pytest
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
from src.services.rabbitmq_receiver import process_message, extract_customer_data
import src.services.rabbitmq_receiver as receiver


@pytest.fixture(autouse=True)
def clear_seen_ids():
    """Reset seen_message_ids before each test to avoid duplicate detection."""
    receiver.seen_message_ids.clear()
    yield
    receiver.seen_message_ids.clear()

@pytest.fixture(autouse=True)
def mock_identity():
    """Zorgt ervoor dat request_master_uuid altijd direct een test-id teruggeeft."""
    with patch("src.services.rabbitmq_receiver.request_master_uuid", return_value="88888-MOCK-UUID-12345"):
        yield

def make_channel() -> MagicMock:
    channel = MagicMock()
    channel.queue_declare = MagicMock()
    channel.basic_publish = MagicMock()
    channel.basic_ack = MagicMock()
    channel.basic_nack = MagicMock()
    return channel


def make_method(delivery_tag: int = 1) -> MagicMock:
    method = MagicMock()
    method.delivery_tag = delivery_tag
    return method


# Updated XML: <id> added in customer element (as required by XSD)
VALID_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a1b2c3d4-0000-4000-8000-000000000099</message_id>
    <master_uuid>01890a5d-ac96-7ab2-80e2-4536629c90ib</master_uuid>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>2026-03-31T10:00:00Z</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <customer>
      <customer_id>12345</customer_id>
      <email>info@bedrijf.be</email>
      <first_name>Test</first_name>
      <last_name>User</last_name>
      <is_company_linked>false</is_company_linked>
      <company_id>4</company_id>
      <company_name></company_name>
      <address>
        <street>Kiekenmarkt</street>
        <number>42</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>be</country>
      </address>
    </customer>
    <registration_fee currency="eur">150.00</registration_fee>
  </body>
</message>"""


# Mock the validator so tests always proceed to the processing logic
@pytest.fixture(autouse=True)
def mock_validator():
    with patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, "")):
        yield

# --- Tests ---


def test_extract_customer_data_email() -> None:
    """extract_customer_data must return the correct email."""
    root = ET.fromstring(VALID_XML)
    data = extract_customer_data(root)
    assert data["email"] == "info@bedrijf.be"


def test_extract_customer_data_fee() -> None:
    """extract_customer_data must return the correct registration_fee and currency."""
    root = ET.fromstring(VALID_XML)
    data = extract_customer_data(root)
    assert data["registration_fee"] == "150.00"
    assert data["fee_currency"] == "eur"


def test_extract_customer_data_address() -> None:
    """extract_customer_data must return all address sub-fields."""
    root = ET.fromstring(VALID_XML)
    data = extract_customer_data(root)
    assert data["address"]["street"] == "Kiekenmarkt"
    assert data["address"]["city"] == "Brussel"
    assert data["address"]["country"] == "be"


# process_message integration tests

def test_process_new_registration_acks_on_success() -> None:
    """process_message must ack the message when FossBilling succeeds."""
    channel = make_channel()
    with patch("src.services.rabbitmq_receiver.create_registration_invoice", return_value="INV-001"):
        process_message(channel, make_method(), MagicMock(), VALID_XML)

    # If this fails, check stdout.
    # basic_ack must be called because validate_xml is mocked to return True.
    channel.basic_ack.assert_called_once()
    channel.basic_nack.assert_not_called()


def test_process_new_registration_sends_invoice_request() -> None:
    """process_message must send invoice_request to facturatie.to.mailing on success."""
    channel = make_channel()
    with patch("src.services.rabbitmq_receiver.create_registration_invoice", return_value="INV-001"):
        process_message(channel, make_method(), MagicMock(), VALID_XML)

    # Find the call that does NOT go to the DLQ
    actual_routing_key = None
    for call in channel.basic_publish.call_args_list:
        if call.kwargs.get("routing_key") == "facturatie.to.mailing":
            actual_routing_key = "facturatie.to.mailing"

    assert actual_routing_key == "facturatie.to.mailing"


def test_process_new_registration_nacks_to_dlq_on_fossbilling_failure() -> None:
    """process_message must nack and send to DLQ when FossBilling raises an exception."""
    channel = make_channel()
    with patch(
        "src.services.rabbitmq_receiver.create_registration_invoice",
        side_effect=Exception("API unreachable")
    ):
        process_message(channel, make_method(), MagicMock(), VALID_XML)
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()


def test_process_new_registration_dlq_contains_fossbilling_error() -> None:
    """DLQ message header must contain the FossBilling error description."""
    channel = make_channel()
    with patch(
        "src.services.rabbitmq_receiver.create_registration_invoice",
        side_effect=Exception("fossbilling_failed: API unreachable")
    ):
        process_message(channel, make_method(), MagicMock(), VALID_XML)

    headers = channel.basic_publish.call_args.kwargs["properties"].headers
    error_str = "".join(headers["errors"])
    assert "fossbilling_failed" in error_str
