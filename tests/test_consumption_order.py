"""
Tests for the consumption_order message processing flow.

Covers:
 - fossbilling_api: get_client_by_company_id, get_unpaid_invoice_for_client,
   add_item_to_invoice, process_consumption_order
 - rabbitmq_receiver: process_message handler for consumption_order
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.services.rabbitmq_receiver import process_message
from src.services.fossbilling_api import (
    get_client_by_company_id,
    get_unpaid_invoice_for_client,
    add_item_to_invoice,
    process_consumption_order,
    MAX_RETRIES,
)


# ── XML builder helper ────────────────────────────────────────────────────────

def _build_xml(
    msg_id: str = "a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5",
    is_company_linked: bool = True,
    company_id: str = "FOSS-CUST-102",
    company_name: str = "Bedrijf NV",
    customer_id: str = "BADGE-007",
    email: str = "info@bedrijf.be",
    payment_method: str = "company_link",
    items: list | None = None,
) -> bytes:
    """Builds a valid consumption_order XML message as bytes."""
    if items is None:
        items = [{"id": "BEV-001", "description": "Coca-Cola", "quantity": 1, "unit_price": "2.50", "vat_rate": "21"}]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "consumption_order"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = "kassa_bar_01"

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = customer_id
    ET.SubElement(customer, "is_company_linked").text = "true" if is_company_linked else "false"
    if is_company_linked:
        ET.SubElement(customer, "company_id").text = company_id
        ET.SubElement(customer, "company_name").text = company_name
    ET.SubElement(customer, "email").text = email
    addr = ET.SubElement(customer, "address")
    ET.SubElement(addr, "street").text = "Teststraat"
    ET.SubElement(addr, "number").text = "1"
    ET.SubElement(addr, "postal_code").text = "1000"
    ET.SubElement(addr, "city").text = "Brussel"
    ET.SubElement(addr, "country").text = "be"

    ET.SubElement(body, "payment_method").text = payment_method

    items_el = ET.SubElement(body, "items")
    for item in items:
        item_el = ET.SubElement(items_el, "item")
        ET.SubElement(item_el, "id").text = item["id"]
        ET.SubElement(item_el, "description").text = item["description"]
        ET.SubElement(item_el, "quantity").text = str(item["quantity"])
        price_el = ET.SubElement(item_el, "unit_price")
        price_el.text = item["unit_price"]
        price_el.set("currency", "eur")
        ET.SubElement(item_el, "vat_rate").text = str(item["vat_rate"])

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

    def test_passes_company_id_to_api(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [], "total": 0})) as mock_post:
            get_client_by_company_id("FOSS-CUST-102")
        payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert "FOSS-CUST-102" in str(payload.values())

    def test_raises_on_api_error(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_error_response("server error")):
            with pytest.raises(Exception):
                get_client_by_company_id("FOSS-CUST-102")


# ── get_unpaid_invoice_for_client ─────────────────────────────────────────────

class TestGetUnpaidInvoiceForClient:
    def test_returns_invoice_id_when_unpaid_exists(self):
        invoices = [{"id": "INV-2026-001", "status": "unpaid"}]
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": invoices, "total": 1})):
            assert get_unpaid_invoice_for_client(42) == "INV-2026-001"

    def test_returns_none_when_no_invoices(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [], "total": 0})):
            assert get_unpaid_invoice_for_client(42) is None

    def test_ignores_paid_invoices(self):
        invoices = [{"id": "INV-2026-001", "status": "paid"}]
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": invoices, "total": 1})):
            assert get_unpaid_invoice_for_client(42) is None

    def test_passes_client_id_to_api(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response({"list": [], "total": 0})) as mock_post:
            get_unpaid_invoice_for_client(42)
        payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert 42 in payload.values() or str(42) in str(payload.values())


# ── add_item_to_invoice ───────────────────────────────────────────────────────

class TestAddItemToInvoice:
    def test_sends_invoice_id_in_payload(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response(True)) as mock_post:
            add_item_to_invoice(
                "INV-2026-001",
                {"title": "Coca-Cola (badge: BADGE-007)", "price": "2.50", "quantity": 1},
            )
        payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert payload.get("id") == "INV-2026-001"

    def test_sends_title_price_and_quantity(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_response(True)) as mock_post:
            add_item_to_invoice("INV-2026-001", {"title": "Water (badge: BADGE-007)", "price": "1.50", "quantity": 3})
        payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert payload.get("price") == "1.50"
        assert payload.get("quantity") == 3

    def test_raises_on_api_error(self):
        with patch("src.services.fossbilling_api.requests.post",
                   return_value=_mock_error_response("invoice not found")):
            with pytest.raises(Exception):
                add_item_to_invoice("INV-BAD", {"title": "Coca-Cola", "price": "2.50", "quantity": 1})


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


# ── process_message: consumption_order handler ────────────────────────────────

class TestProcessMessageConsumptionOrder:

    def test_happy_path_acks_message(self):
        """Valid company-linked message → saved to DB and acknowledged."""
        channel = MagicMock()
        body = _build_xml(msg_id="11111111-1111-4111-1111-111111111111")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.save_items"):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_ack.assert_called_once_with(delivery_tag=1)
        channel.basic_nack.assert_not_called()

    def test_company_id_and_badge_id_saved_to_db(self):
        """company_id and badge_id from XML must be passed to consumption_store."""
        channel = MagicMock()
        body = _build_xml(
            msg_id="11111111-1111-4111-1111-111111111112",
            company_id="FOSS-CUST-102",
            customer_id="BADGE-007",
        )

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.save_items") as mock_save:
            process_message(channel, _make_method(), MagicMock(), body)

        args = mock_save.call_args
        assert "FOSS-CUST-102" in str(args)
        assert "BADGE-007" in str(args)

    def test_item_description_saved_without_badge_in_title(self):
        """Raw description is saved to DB; badge is added later when invoice is created."""
        channel = MagicMock()
        body = _build_xml(
            msg_id="22222222-2222-4222-2222-222222222222",
            customer_id="BADGE-007",
        )
        captured = {}

        def capture(company_id, badge_id, master_uuid, items):
            captured["items"] = items

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.save_items",
                   side_effect=capture):
            process_message(channel, _make_method(), MagicMock(), body)

        assert len(captured["items"]) > 0
        assert "description" in captured["items"][0]

    def test_multiple_items_all_saved(self):
        """All items in the message must be saved to the DB."""
        channel = MagicMock()
        items = [
            {"id": "BEV-001", "description": "Coca-Cola", "quantity": 2, "unit_price": "2.50", "vat_rate": "21"},
            {"id": "BEV-002", "description": "Water", "quantity": 1, "unit_price": "1.50", "vat_rate": "6"},
        ]
        body = _build_xml(msg_id="33333333-3333-4333-3333-333333333333", items=items)

        captured = {}

        def capture(company_id, badge_id, master_uuid, saved_items):
            captured["items"] = saved_items

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.save_items",
                   side_effect=capture):
            process_message(channel, _make_method(), MagicMock(), body)

        assert len(captured["items"]) == 2

    def test_db_failure_sends_to_dlq(self):
        """DB save error → DLQ and nack."""
        channel = MagicMock()
        body = _build_xml(msg_id="44444444-4444-4444-4444-444444444444")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml", return_value=(True, None)), \
             patch("src.services.rabbitmq_receiver.consumption_store.save_items",
                   side_effect=Exception("DB unreachable")):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
        channel.basic_publish.assert_called_once()

    def test_duplicate_message_is_skipped(self):
        """Duplicate message_id → acknowledged without saving to DB."""
        channel = MagicMock()
        body = _build_xml(msg_id="55555555-5555-4555-5555-555555555555")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=True), \
             patch("src.services.rabbitmq_receiver.consumption_store.save_items") as mock_save:
            process_message(channel, _make_method(), MagicMock(), body)

        mock_save.assert_not_called()
        channel.basic_ack.assert_called_once_with(delivery_tag=1)

    def test_invalid_xml_sends_to_dlq(self):
        """Malformed XML → DLQ and nack without processing."""
        channel = MagicMock()
        process_message(channel, _make_method(), MagicMock(), b"<not valid xml")
        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)

    def test_xsd_validation_failure_sends_to_dlq(self):
        """XSD-invalid message → DLQ and nack."""
        channel = MagicMock()
        body = _build_xml(msg_id="66666666-6666-4666-6666-666666666666")

        with patch("src.services.rabbitmq_receiver.is_duplicate", return_value=False), \
             patch("src.services.rabbitmq_receiver.validate_xml",
                   return_value=(False, "missing required field")):
            process_message(channel, _make_method(), MagicMock(), body)

        channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
