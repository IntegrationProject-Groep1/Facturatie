import pytest
import xml.etree.ElementTree as ET
from src.services.rabbitmq_receiver import is_duplicate
from src.utils.xml_validator import validate_xml


def build_xml(
    msg_type: str = "consumption_order",
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    version: str = "2.0",
    timestamp: str = "2026-02-24T18:30:00Z",
    source: str = "kassa_bar_01",
    is_company_linked: str = "false",
    company_id: str = "FOSS-123",
    company_name: str = "Test Corp",
    vat_rate: str = "21",
    correlation_id: str = None
) -> str:
    """Builds a minimal valid XML string for testing."""
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "consumption_order"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source
    if correlation_id:
        ET.SubElement(header, "correlation_id").text = correlation_id

    body = ET.SubElement(root, "body")

    # Customer section (Strict sequence: id, is_company_linked, company_id?, company_name?, email, address)
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = "12345"
    ET.SubElement(customer, "is_company_linked").text = is_company_linked
    if is_company_linked == "true":
        ET.SubElement(customer, "company_id").text = company_id
        ET.SubElement(customer, "company_name").text = company_name
    ET.SubElement(customer, "email").text = "test@example.com"

    address = ET.SubElement(customer, "address")
    ET.SubElement(address, "street").text = "Kiekenmarkt"
    ET.SubElement(address, "number").text = "42"
    ET.SubElement(address, "postal_code").text = "1000"
    ET.SubElement(address, "city").text = "Brussel"
    ET.SubElement(address, "country").text = "be"

    # Payment method (Required by consumption_order.xsd)
    ET.SubElement(body, "payment_method").text = "online"

    items_el = ET.SubElement(body, "items")
    item_el = ET.SubElement(items_el, "item")
    ET.SubElement(item_el, "id").text = "BEV-001"
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
    """Builds a minimal valid new_registration XML string for testing."""
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source

    body = ET.SubElement(root, "body")

    # Customer section (Strict sequence: id, email, is_company_linked, company_name?, address)
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = "REG-999"
    if email is not None:
        ET.SubElement(customer, "email").text = email
    ET.SubElement(customer, "first_name").text = "Test"
    ET.SubElement(customer, "last_name").text = "User"
    ET.SubElement(customer, "is_company_linked").text = is_company_linked

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


# --- TESTS ---

def test_valid_consumption_order() -> None:
    xml = build_xml(msg_type="consumption_order")
    is_valid, errors = validate_xml(xml, "consumption_order")
    assert is_valid is True, f"Validation failed: {errors}"


def test_invalid_vat_rate_returns_error() -> None:
    xml = build_xml(msg_type="consumption_order", vat_rate="99")
    is_valid, errors = validate_xml(xml, "consumption_order")
    assert is_valid is False
    assert len(errors) > 0


@pytest.mark.parametrize("vat_rate", ["6", "12", "21"])
def test_valid_vat_rates(vat_rate: str) -> None:
    xml = build_xml(msg_type="consumption_order", vat_rate=vat_rate)
    is_valid, errors = validate_xml(xml, "consumption_order")
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
