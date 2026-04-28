import pytest
import xml.etree.ElementTree as ET
from src.services.rabbitmq_receiver import is_duplicate
from src.utils.xml_validator import validate_xml


def build_xml(
    msg_type: str = "invoice_request", # Veranderd van consumption_order
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    version: str = "2.0",
    timestamp: str = "2026-02-24T18:30:00Z",
    source: str = "kassa_bar_01",
    is_company_linked: str = "false",
    vat_rate: str = "21",
    correlation_id: str = None
) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "master_uuid").text = "01890a5d-ac96-7ab2-80e2-4536629c90de" # TOEVOEGEN
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "invoice_request"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    if correlation_id:
        ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")

    # CUSTOMER
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "customer_id").text = "12345"
    ET.SubElement(customer, "email").text = "test@example.com"
    ET.SubElement(customer, "first_name").text = "Jan"
    ET.SubElement(customer, "last_name").text = "De Tester"
    ET.SubElement(customer, "is_company_linked").text = is_company_linked
    ET.SubElement(customer, "company_id").text = "FOSS-123"
    ET.SubElement(customer, "company_name").text = "Test Corp"

    address = ET.SubElement(customer, "address")
    ET.SubElement(address, "street").text = "Kiekenmarkt"
    ET.SubElement(address, "number").text = "42"
    ET.SubElement(address, "postal_code").text = "1000"
    ET.SubElement(address, "city").text = "Brussel"
    ET.SubElement(address, "country").text = "be"

    invoice = ET.SubElement(body, "invoice")
    ET.SubElement(invoice, "description").text = "Consumpties"
    amount = ET.SubElement(invoice, "amount", {"currency": "eur"})
    amount.text = "5.00"
    ET.SubElement(invoice, "due_date").text = "2026-03-29"

    # ITEMS
    items_el = ET.SubElement(body, "items")
    item_el = ET.SubElement(items_el, "item")
    ET.SubElement(item_el, "description").text = "Coffee"
    ET.SubElement(item_el, "quantity").text = "2"
    unit_price = ET.SubElement(item_el, "unit_price", {"currency": "eur"})
    unit_price.text = "2.50"
    ET.SubElement(item_el, "vat_rate").text = vat_rate

    return ET.tostring(root, encoding="unicode")


def build_registration_xml(
    msg_id: str = "a1b2c3d4-0000-4000-8000-000000000001",
    version: str = "2.0",
    timestamp: str = "2026-03-30T10:00:00Z",
    source: str = "frontend",
    email: str = "info@bedrijf.be",
    is_company_linked: str = "false"
) -> str:
    """Builds a valid new_registration XML string according to the latest XSD."""
    root = ET.Element("message")

    # --- HEADER (Volgorde: message_id -> master_uuid -> version -> type -> timestamp -> source) ---
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "master_uuid").text = "01890a5d-ac96-7ab2-80e2-4536629c90de"
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source

    body = ET.SubElement(root, "body")

    # --- CUSTOMER (Volgorde: customer_id -> email -> first_name -> last_name -> is_company_linked -> company_id? -> company_name? -> address) ---
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "customer_id").text = "REG-999" # 'id' vervangen door 'customer_id'

    if email is not None:
        ET.SubElement(customer, "email").text = email

    ET.SubElement(customer, "first_name").text = "Test"
    ET.SubElement(customer, "last_name").text = "User"
    ET.SubElement(customer, "is_company_linked").text = is_company_linked

    if is_company_linked == "true":
        ET.SubElement(customer, "company_id").text = "CRM-COMP-888"
        ET.SubElement(customer, "company_name").text = "Test Bedrijf NV"

    address = ET.SubElement(customer, "address")
    ET.SubElement(address, "street").text = "Kiekenmarkt"
    ET.SubElement(address, "number").text = "42"
    ET.SubElement(address, "postal_code").text = "1000"
    ET.SubElement(address, "city").text = "Brussel"
    ET.SubElement(address, "country").text = "be"

    # Registration fee
    fee_el = ET.SubElement(body, "registration_fee", {"currency": "eur"})
    fee_el.text = "150.00"

    return ET.tostring(root, encoding="unicode")


def build_event_ended_xml(
    msg_id: str = "b2c3d4e5-f6a7-4839-9231-000000000001",
    session_id: str = "sess-42",
    timestamp: str = "2026-04-28T14:00:00Z"
) -> str:
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

    return ET.tostring(root, encoding="unicode")


# --- TESTS ---

def test_valid_consumption_order() -> None:
    xml = build_xml(msg_type="invoice_request")
    is_valid, errors = validate_xml(xml, "invoice_request")
    assert is_valid is True, f"Validation failed: {errors}"


def test_invalid_vat_rate_returns_error() -> None:
    xml = build_xml(msg_type="invoice_request", vat_rate="99")
    is_valid, errors = validate_xml(xml, "invoice_request")
    assert is_valid is False
    assert len(errors) > 0


@pytest.mark.parametrize("vat_rate", ["6", "12", "21"])
def test_valid_vat_rates(vat_rate: str) -> None:
    xml = build_xml(vat_rate=vat_rate)
    is_valid, errors = validate_xml(xml, "invoice_request")
    assert is_valid is True, f"VAT rate {vat_rate} failed: {errors}"


def test_valid_new_registration() -> None:
    xml = build_registration_xml()
    is_valid, errors = validate_xml(xml, "new_registration")
    assert is_valid is True, f"New registration failed: {errors}"


def test_new_registration_missing_email() -> None:
    xml = build_registration_xml(email=None)
    is_valid, errors = validate_xml(xml, "new_registration")
    assert is_valid is False


def test_duplicate_message_is_flagged() -> None:
    """A message_id already in seen_ids must be detected as duplicate."""
    seen_ids: set[str] = {"f47ac10b-58cc-4372-a567-0e02b2c3d479"}
    assert is_duplicate("f47ac10b-58cc-4372-a567-0e02b2c3d479", seen_ids) is True


def test_unique_message_is_not_flagged() -> None:
    """A message_id not in seen_ids must not be detected as duplicate."""
    seen_ids: set[str] = {"some-other-id"}
    assert is_duplicate("f47ac10b-58cc-4372-a567-0e02b2c3d479", seen_ids) is False


def test_valid_event_ended() -> None:
    xml = build_event_ended_xml()
    is_valid, errors = validate_xml(xml, "event_ended")
    assert is_valid is True, f"Event Ended validation failed: {errors}"

def test_event_ended_invalid_date() -> None:
    xml = build_event_ended_xml(timestamp="NIET-EEN-DATUM")
    is_valid, errors = validate_xml(xml, "event_ended")
    assert is_valid is False
