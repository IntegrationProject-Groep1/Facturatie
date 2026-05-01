"""
Tests voor de invoice_request en new_registration message flows.
Consolideert: test_consumption_order.py + test_process_new_registration.py

Belangrijke wijzigingen t.o.v. vorige versie:
- invoice_request XML builder: oude <customer>/<items> structuur vervangen door
  <user_id> + <invoice_data> conform contract §11.1
- master_uuid verwijderd uit alle headers (contract #90)
- consumption_store.save_items → save_invoice_request (nieuwe receiver logica)
- Namen zitten in <contact> wrapper bij new_registration (contract Regel 2)
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import src.services.rabbitmq_receiver as receiver
from src.services.rabbitmq_receiver import process_message, extract_customer_data
from src.services.fossbilling_api import (
    get_client_by_company_id,
    get_unpaid_invoice_for_client,
    add_item_to_invoice,
    process_consumption_order,
    MAX_RETRIES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_seen_ids():
    receiver.seen_message_ids.clear()
    yield
    receiver.seen_message_ids.clear()


@pytest.fixture(autouse=True)
def mock_identity():
    with patch("src.services.rabbitmq_receiver.request_master_uuid",
               return_value="88888-MOCK-UUID-12345"):
        yield


# ── XML builders ──────────────────────────────────────────────────────────────

def _build_invoice_request_xml(
    msg_id: str = "a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5",
    user_id: str = "BADGE-007",
    company_name: str = "Bedrijf NV",
    correlation_id: str = "corr-001",
) -> bytes:
    """
    Bouwt een invoice_request XML conform de nieuwe structuur (contract §11.1).
    Geen master_uuid in header, geen <customer>/<items> blokken.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    # Volgorde conform XSD: message_id → type → source → timestamp → version → correlation_id
    # master_uuid VERWIJDERD — verboden in alle headers (contract #90)
    ET.SubElement(header, "type").text = "invoice_request"
    ET.SubElement(header, "source").text = "crm"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "user_id").text = user_id

    invoice_data = ET.SubElement(body, "invoice_data")
    # Volgorde conform InvoiceDataType XSD: first_name → last_name → email → address → company_name → vat_number
    ET.SubElement(invoice_data, "first_name").text = "Test"
    ET.SubElement(invoice_data, "last_name").text = "User"
    ET.SubElement(invoice_data, "email").text = "info@bedrijf.be"

    address = ET.SubElement(invoice_data, "address")
    ET.SubElement(address, "street").text = "Teststraat"
    ET.SubElement(address, "number").text = "1"
    ET.SubElement(address, "postal_code").text = "1000"
    ET.SubElement(address, "city").text = "Brussel"
    ET.SubElement(address, "country").text = "be"

    ET.SubElement(invoice_data, "company_name").text = company_name
    ET.SubElement(invoice_data, "vat_number").text = "BE0123456789"

    ET.indent(root, space="    ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    ).encode("utf-8")


def _build_new_registration_xml() -> bytes:
    """
    Bouwt een new_registration XML conform de nieuwe structuur.
    Namen zitten in <contact> wrapper (contract Regel 2).
    Geen master_uuid in header (contract #90).
    """
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a1b2c3d4-0000-4000-8000-000000000099</message_id>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>2026-03-31T10:00:00Z</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <customer>
      <customer_id>12345</customer_id>
      <email>info@bedrijf.be</email>
      <contact>
        <first_name>Test</first_name>
        <last_name>User</last_name>
      </contact>
      <is_company_linked>false</is_company_linked>
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


def _build_event_ended_xml(
    msg_id: str = "eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee",
    session_id: str = "SESSION-001",
) -> bytes:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = ET.Element("message")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "event_ended"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = "frontend"
    body = ET.SubElement(root, "body")
    ET.SubElement(body, "session_id").text = session_id
    ET.SubElement(body, "ended_at").text = timestamp
    ET.indent(root, space="    ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
    ).encode("utf-8")


def _make_method(delivery_tag: int = 1) -> MagicMock:
    m = MagicMock()
    m.delivery_tag = delivery_tag
    return m


def _mock_response(result_value) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {"result": result_value}
    mock.raise_for_status = MagicMock()
    return mock


def _mock_error_response(message: str) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {"error": {"message": message}}
    mock.raise_for_status = MagicMock()
    return mock


# ── extract_customer_data ─────────────────────────────────────────────────────

def test_extract_customer_data_email() -> None:
    root = ET.fromstring(_build_new_registration_xml())
    data = extract_customer_data(root)
    assert data["email"] == "info@bedrijf.be"


def test_extract_customer_data_names_from_contact_wrapper() -> None:
    """Namen worden gelezen via body/customer/contact/ (contract Regel 2)."""
    root = ET.fromstring(_build_new_registration_xml())
    data = extract_customer_data(root)
    assert data["first_name"] == "Test"
    assert data["last_name"] == "User"


def test_extract_customer_data_fee() -> None:
    root = ET.fromstring(_build_new_registration_xml())
    data = extract_customer_data(root)
    assert data["registration_fee"] == "150.00"
    assert data["fee_currency"] == "eur"


def test_extract_customer_data_address() -> None:
    root = ET.fromstring(_build_new_registration_xml())
    data = extract_customer_data(root)
    assert data["address"]["street"] == "Kiekenmarkt"
    assert data["address"]["city"] == "Brussel"
    assert data["address"]["country"] == "be"


# ── get_client_by_company_id ──────────────────────────────────────────────────

class TestGetClientByCompanyId:
    def test_returns_client_id_when_found(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [{"id": 42, "company": "FOSS-CUST-102"}], "total": 1})):
            assert get_client_by_company_id("FOSS-CUST-102") == 42

    def test_returns_none_when_not_found(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [], "total": 0})):
            assert get_client_by_company_id("FOSS-CUST-999") is None

    def test_raises_on_api_error(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_error_response("server error")):
            with pytest.raises(Exception):
                get_client_by_company_id("FOSS-CUST-102")


# ── get_unpaid_invoice_for_client ─────────────────────────────────────────────

class TestGetUnpaidInvoiceForClient:
    def test_returns_invoice_id_when_unpaid_exists(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [{"id": "INV-2026-001", "status": "unpaid"}], "total": 1})):
            assert get_unpaid_invoice_for_client(42) == "INV-2026-001"

    def test_returns_none_when_no_invoices(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [], "total": 0})):
            assert get_unpaid_invoice_for_client(42) is None

    def test_ignores_paid_invoices(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [{"id": "INV-2026-001", "status": "paid"}], "total": 1})):
            assert get_unpaid_invoice_for_client(42) is None


# ── add_item_to_invoice ───────────────────────────────────────────────────────

class TestAddItemToInvoice:
    def test_sends_invoice_id_in_payload(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response(True)) as mock_post:
            add_item_to_invoice("INV-2026-001", {"title": "Coca-Cola", "price": "2.50", "quantity": 1})
        payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert payload.get("id") == "INV-2026-001"

    def test_sends_title_price_and_quantity(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response(True)) as mock_post:
            add_item_to_invoice("INV-2026-001", {"title": "Water", "price": "1.50", "quantity": 3})
        payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert payload.get("price") == "1.50"
        assert payload.get("quantity") == 3

    def test_raises_on_api_error(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_error_response("invoice not found")):
            with pytest.raises(Exception):
                add_item_to_invoice("INV-BAD", {"title": "Cola", "price": "2.50", "quantity": 1})


# ── process_consumption_order ─────────────────────────────────────────────────

class TestProcessConsumptionOrder:
    ITEMS = [{"title": "Coca-Cola (badge: BADGE-007)", "price": "2.50", "quantity": 1, "vat_rate": "21"}]

    def test_creates_invoice_with_all_items(self):
        with patch("src.services.fossbilling_api.get_client_by_company_id", return_value=42), \
             patch("src.services.fossbilling_api._create_invoice", return_value="INV-2026-001") as mock_create:
            result = process_consumption_order("FOSS-CUST-102", self.ITEMS)
        assert result == "INV-2026-001"
        mock_create.assert_called_once_with(42, self.ITEMS)

    def test_raises_when_company_not_found(self):
        with patch("src.services.fossbilling_api.get_client_by_company_id", return_value=None):
            with pytest.raises(Exception, match="company_id"):
                process_consumption_order("FOSS-CUST-UNKNOWN", self.ITEMS)

    def test_retries_on_transient_api_failure(self):
        with patch("src.services.fossbilling_api.get_client_by_company_id",
                   side_effect=[Exception("timeout"), 42]), \
             patch("src.services.fossbilling_api._create_invoice", return_value="INV-2026-001"), \
             patch("src.services.fossbilling_api.time.sleep"):
            result = process_consumption_order("FOSS-CUST-102", self.ITEMS)
        assert result == "INV-2026-001"

    def test_raises_after_max_retries(self):
        with patch("src.services.fossbilling_api.get_client_by_company_id",
                   side_effect=Exception("timeout")), \
             patch("src.services.fossbilling_api.time.sleep"):
            with pytest.raises(Exception, match=f"after {MAX_RETRIES} attempts"):
                process_consumption_order("FOSS-CUST-102", self.ITEMS)


# ── process_message: invoice_request handler ──────────────────────────────────

class TestProcessMessageInvoiceRequest:

    def test_happy_path_acks_message(self):
        """Geldig invoice_request bericht → opgeslagen en geacked."""
        channel = MagicMock()
        body = _build_invoice_request_xml(msg_id="11111111-1111-4111-1111-111111111111")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch.object(receiver.consumption_store, "save_invoice_request", create=True):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_ack.assert_called_once_with(delivery_tag=1)
        channel.basic_nack.assert_not_called()

    def test_user_id_and_correlation_id_saved(self):
        """user_id en correlation_id moeten doorgegeven worden aan consumption_store."""
        channel = MagicMock()
        body = _build_invoice_request_xml(
            msg_id="11111111-1111-4111-1111-111111111112",
            user_id="BADGE-007",
            correlation_id="corr-xyz",
        )

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch.object(receiver.consumption_store, "save_invoice_request",
                          create=True) as mock_save:
            process_message(channel, _make_method(), MagicMock(), body)

        args = str(mock_save.call_args)
        assert "BADGE-007" in args
        assert "corr-xyz" in args

    def test_missing_company_name_sends_to_dlq(self):
        """invoice_request zonder company_name → DLQ (Facturatie vereist bedrijfsklant)."""
        channel = MagicMock()
        body = _build_invoice_request_xml(
            msg_id="11111111-1111-4111-1111-111111111113",
            company_name="",
        )

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)

    def test_db_failure_sends_to_dlq(self):
        """DB save fout → DLQ en nack."""
        channel = MagicMock()
        body = _build_invoice_request_xml(msg_id="44444444-4444-4444-4444-444444444444")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch.object(receiver.consumption_store, "save_invoice_request",
                          create=True, side_effect=Exception("DB unreachable")):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)

    def test_duplicate_message_is_skipped(self):
        channel = MagicMock()
        body = _build_invoice_request_xml(msg_id="55555555-5555-4555-5555-555555555555")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=True), \
             patch.object(receiver.consumption_store, "save_invoice_request",
                          create=True) as mock_save:
            process_message(channel, _make_method(), MagicMock(), body)

        mock_save.assert_not_called()
        channel.basic_ack.assert_called_once_with(delivery_tag=1)

    def test_invalid_xml_sends_to_dlq(self):
        channel = MagicMock()
        process_message(channel, _make_method(), MagicMock(), b"<not valid xml")
        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)

    def test_xsd_validation_failure_sends_to_dlq(self):
        channel = MagicMock()
        body = _build_invoice_request_xml(msg_id="66666666-6666-4666-6666-666666666666")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml",
                   return_value=(False, "missing required field")):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)


# ── process_message: new_registration handler ─────────────────────────────────

class TestProcessMessageNewRegistration:

    @pytest.fixture(autouse=True)
    def mock_validator(self):
        with patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, "")):
            yield

    def _make_channel(self) -> MagicMock:
        ch = MagicMock()
        ch.queue_declare = MagicMock()
        ch.basic_publish = MagicMock()
        ch.basic_ack = MagicMock()
        ch.basic_nack = MagicMock()
        return ch

    def test_acks_on_success(self) -> None:
        channel = self._make_channel()
        with patch("src.services.rabbitmq_receiver.create_registration_invoice",
                   return_value="INV-001"), \
             patch("src.services.rabbitmq_receiver.build_invoice_created_notification_xml",
                   return_value="<xml>mock</xml>"):
            process_message(channel, _make_method(), MagicMock(), _build_new_registration_xml())

        channel.basic_ack.assert_called_once()
        channel.basic_nack.assert_not_called()

    def test_sends_notification_to_mailing(self) -> None:
        channel = self._make_channel()
        with patch("src.services.rabbitmq_receiver.create_registration_invoice",
                   return_value="INV-001"), \
             patch("src.services.rabbitmq_receiver.build_invoice_created_notification_xml",
                   return_value="<xml>mock</xml>"):
            process_message(channel, _make_method(), MagicMock(), _build_new_registration_xml())

        routing_keys = [c.kwargs.get("routing_key") for c in channel.basic_publish.call_args_list]
        assert "facturatie.to.mailing" in routing_keys

    def test_nacks_to_dlq_on_fossbilling_failure(self) -> None:
        channel = self._make_channel()
        with patch("src.services.rabbitmq_receiver.create_registration_invoice",
                   side_effect=Exception("API unreachable")):
            process_message(channel, _make_method(), MagicMock(), _build_new_registration_xml())

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
        channel.basic_ack.assert_not_called()

    def test_dlq_contains_fossbilling_error(self) -> None:
        channel = self._make_channel()
        with patch("src.services.rabbitmq_receiver.create_registration_invoice",
                   side_effect=Exception("fossbilling_failed: API unreachable")):
            process_message(channel, _make_method(), MagicMock(), _build_new_registration_xml())

        headers = channel.basic_publish.call_args.kwargs["properties"].headers
        error_str = "".join(headers["errors"])
        assert "fossbilling_failed" in error_str

    def test_notification_uses_correlation_id_not_master_uuid(self) -> None:
        """build_invoice_created_notification_xml moet correlation_id krijgen, geen master_uuid."""
        channel = self._make_channel()
        with patch("src.services.rabbitmq_receiver.create_registration_invoice",
                   return_value="INV-001"), \
             patch("src.services.rabbitmq_receiver.build_invoice_created_notification_xml",
                   return_value="<xml>mock</xml>") as mock_builder:
            process_message(channel, _make_method(), MagicMock(), _build_new_registration_xml())

        call_kwargs = mock_builder.call_args.kwargs
        assert "master_uuid" not in call_kwargs
        assert "correlation_id" in call_kwargs


# ── process_message: event_ended handler ─────────────────────────────────────

class TestProcessMessageEventEnded:

    def test_happy_path_creates_invoices_and_acks(self):
        channel = MagicMock()
        body = _build_event_ended_xml()

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_pending_company_ids",
                   return_value=["FOSS-CUST-102"]), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_items_for_company",
                   return_value=([{"title": "Coca-Cola", "price": "2.50", "quantity": 1, "vat_rate": "21"}], [1])), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_company_meta",
                   return_value={"email": "info@bedrijf.be", "company_name": "Bedrijf NV"}), \
             patch("src.services.rabbitmq_receiver.fossbilling_client.process_consumption_order",
                   return_value="INV-2026-001"), \
             patch("src.services.rabbitmq_receiver.send_message"), \
             patch("src.services.rabbitmq_receiver.consumption_store.clear_by_ids"):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_ack.assert_called_once_with(delivery_tag=1)
        channel.basic_nack.assert_not_called()

    def test_no_pending_companies_acks_immediately(self):
        channel = MagicMock()
        body = _build_event_ended_xml()

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_pending_company_ids",
                   return_value=[]), \
             patch("src.services.rabbitmq_receiver.fossbilling_client.process_consumption_order") as mock_fb:
            process_message(channel, _make_method(), MagicMock(), body)

        mock_fb.assert_not_called()
        channel.basic_ack.assert_called_once_with(delivery_tag=1)

    def test_fossbilling_failure_sends_to_dlq(self):
        channel = MagicMock()
        body = _build_event_ended_xml()

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_pending_company_ids",
                   return_value=["FOSS-CUST-102"]), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_items_for_company",
                   return_value=([{"title": "Test Item", "price": "10.00", "quantity": 1}], [123])), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_company_meta",
                   return_value={"email": "", "company_name": ""}), \
             patch("src.services.rabbitmq_receiver.fossbilling_client.process_consumption_order",
                   side_effect=Exception("API timeout")):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)

    def test_clear_company_called_after_invoice(self):
        channel = MagicMock()
        body = _build_event_ended_xml()

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_pending_company_ids",
                   return_value=["FOSS-CUST-102"]), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_items_for_company",
                   return_value=([{"title": "Fanta", "price": "2.50", "quantity": 1}], [42])), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_company_meta",
                   return_value={"email": "test@test.be", "company_name": "Test NV"}), \
             patch("src.services.rabbitmq_receiver.fossbilling_client.process_consumption_order",
                   return_value="INV-001"), \
             patch("src.services.rabbitmq_receiver.send_message"), \
             patch("src.services.rabbitmq_receiver.consumption_store.clear_by_ids") as mock_clear:
            process_message(channel, _make_method(), MagicMock(), body)

        mock_clear.assert_called_once_with([42])

    def test_event_ended_uses_msg_id_as_correlation_id(self):
        """Mailing notificatie moet correlation_id=msg_id krijgen, geen master_uuid."""
        channel = MagicMock()
        body = _build_event_ended_xml(msg_id="eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee")
        sent = []

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_pending_company_ids",
                   return_value=["FOSS-CUST-102"]), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_items_for_company",
                   return_value=([{"title": "Cola", "price": "2.50", "quantity": 1}], [1])), \
             patch("src.services.rabbitmq_receiver.consumption_store.get_company_meta",
                   return_value={"email": "test@test.be", "company_name": "Test NV"}), \
             patch("src.services.rabbitmq_receiver.fossbilling_client.process_consumption_order",
                   return_value="INV-001"), \
             patch("src.services.rabbitmq_receiver.build_invoice_created_notification_xml",
                   side_effect=lambda **kw: sent.append(kw) or "<xml/>") as mock_builder, \
             patch("src.services.rabbitmq_receiver.send_message"), \
             patch("src.services.rabbitmq_receiver.consumption_store.clear_by_ids"):
            process_message(channel, _make_method(), MagicMock(), body)

        assert len(sent) == 1
        assert sent[0]["correlation_id"] == "eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee"
        assert "master_uuid" not in sent[0]
