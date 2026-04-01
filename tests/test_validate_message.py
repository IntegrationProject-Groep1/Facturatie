import pytest
import xml.etree.ElementTree as ET
from src.services.rabbitmq_receiver import validate_message, is_duplicate


def build_xml(
    msg_type: str = "consumption_order",
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    version: str = "2.0",
    timestamp: str = "2026-02-24T18:30:00Z",
    source: str = "kassa_bar_01",
    is_company_linked: str = "false",
    company_id: str = "",
    company_name: str = "",
    vat_rate: str = "21",
    correlation_id: str = ""
) -> ET.Element:
    """Helper that builds a minimal valid XML element for testing (XML Naming Standard)."""
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
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = "12345"
    ET.SubElement(customer, "is_company_linked").text = is_company_linked
    if company_id:
        ET.SubElement(customer, "company_id").text = company_id
    if company_name:
        ET.SubElement(customer, "company_name").text = company_name

    items_el = ET.SubElement(body, "items")
    item_el = ET.SubElement(items_el, "item")
    ET.SubElement(item_el, "id").text = "BEV-001"
    ET.SubElement(item_el, "description").text = "Coffee"
    ET.SubElement(item_el, "quantity").text = "2"
    unit_price = ET.SubElement(item_el, "unit_price")
    unit_price.text = "2.50"
    unit_price.set("currency", "eur")
    ET.SubElement(item_el, "vat_rate").text = vat_rate

    return root


# Valid message test
def test_valid_consumption_order() -> None:
    """A fully valid consumption_order should return no errors."""
    root = build_xml()
    errors = validate_message(root)
    assert errors == []


# VAT rate tests
def test_invalid_vat_rate_returns_error() -> None:
    """A vat_rate that is not 6, 12 or 21 must return an error."""
    root = build_xml(vat_rate="99")
    errors = validate_message(root)
    assert any("vat_rate" in e for e in errors)


@pytest.mark.parametrize("vat_rate", ["6", "12", "21"])
def test_valid_vat_rates(vat_rate: str) -> None:
    """All three allowed VAT rates must pass without a vat_rate error."""
    root = build_xml(vat_rate=vat_rate)
    errors = validate_message(root)
    assert not any("vat_rate" in e for e in errors)


# Company linked tests

def test_missing_company_id_when_company_linked() -> None:
    """company_id must be present when is_company_linked=true."""
    root = build_xml(
        is_company_linked="true",
        company_id="",
        company_name="Bedrijf NV"
    )
    errors = validate_message(root)
    assert any("company_id" in e for e in errors)


def test_missing_company_name_when_company_linked() -> None:
    """company_name must be present when is_company_linked=true."""
    root = build_xml(
        is_company_linked="true",
        company_id="FOSS-CUST-102",
        company_name=""
    )
    errors = validate_message(root)
    assert any("company_name" in e for e in errors)


def test_valid_company_linked() -> None:
    """Both company_id and company_name present — no errors expected."""
    root = build_xml(
        is_company_linked="true",
        company_id="FOSS-CUST-102",
        company_name="Bedrijf NV"
    )
    errors = validate_message(root)
    assert errors == []


# Header field tests

def test_missing_message_id() -> None:
    """Empty message_id must trigger missing_required_field error."""
    root = build_xml(msg_id="")
    errors = validate_message(root)
    assert any("missing_required_field" in e and "message_id" in e for e in errors)


def test_missing_timestamp() -> None:
    """Empty timestamp must trigger missing_required_field error."""
    root = build_xml(timestamp="")
    errors = validate_message(root)
    assert any("missing_required_field" in e and "timestamp" in e for e in errors)


def test_invalid_timestamp_format() -> None:
    """A timestamp not in ISO-8601 UTC format must trigger invalid_iso8601_timestamp error."""
    root = build_xml(timestamp="24-02-2026 18:30:00")
    errors = validate_message(root)
    assert any("invalid_iso8601_timestamp" in e for e in errors)


def test_valid_timestamp_format() -> None:
    """A correct ISO-8601 UTC timestamp must not trigger a timestamp error."""
    root = build_xml(timestamp="2026-02-24T18:30:00Z")
    errors = validate_message(root)
    assert not any("timestamp" in e for e in errors)


def test_missing_source() -> None:
    """Empty source must trigger missing_required_field error."""
    root = build_xml(source="")
    errors = validate_message(root)
    assert any("missing_required_field" in e and "source" in e for e in errors)


def test_unknown_message_type() -> None:
    """A completely unknown type must return unknown_message_type error."""
    root = build_xml(msg_type="UNKNOWN_TYPE")
    errors = validate_message(root)
    assert any("unknown_message_type" in e for e in errors)


def test_uppercase_message_type_returns_enum_case_error() -> None:
    """A known type in uppercase (e.g. CONSUMPTION_ORDER) must return invalid_enum_case error."""
    root = build_xml(msg_type="CONSUMPTION_ORDER")
    errors = validate_message(root)
    assert any("invalid_enum_case" in e for e in errors)


# payment_registered specific tests

def test_payment_registered_missing_correlation_id() -> None:
    """payment_registered without correlation_id must return an error."""
    root = build_xml(msg_type="payment_registered", correlation_id="")
    errors = validate_message(root)
    assert any("correlation_id" in e for e in errors)


def test_payment_registered_with_correlation_id() -> None:
    """payment_registered with correlation_id present — no correlation error."""
    root = build_xml(
        msg_type="payment_registered",
        correlation_id="f47ac10b-58cc-4372-a567-0e02b2c3d479"
    )
    errors = validate_message(root)
    assert not any("correlation_id" in e for e in errors)


# Version validation tests

def test_invalid_version_returns_error() -> None:
    """A version other than 2.0 must return an error."""
    root = build_xml(version="1.0")
    errors = validate_message(root)
    assert any("version" in e.lower() for e in errors)


def test_valid_version() -> None:
    """Version 2.0 should not return a version error."""
    root = build_xml(version="2.0")
    errors = validate_message(root)
    assert not any("version" in e.lower() for e in errors)


# new_registration tests

def build_registration_xml(
    msg_id: str = "a1b2c3d4-0000-4000-8000-000000000001",
    version: str = "2.0",
    timestamp: str = "2026-03-30T10:00:00Z",
    source: str = "frontend",
    email: str = "info@bedrijf.be",
    is_company_linked: str = "false",
    company_id: str = "",
    company_name: str = "",
    street: str = "Kiekenmarkt",
    number: str = "42",
    postal_code: str = "1000",
    city: str = "Brussel",
    country: str = "be",
    registration_fee: str = "150.00",
    fee_currency: str = "eur",
) -> ET.Element:
    """Helper that builds a minimal valid new_registration XML element for testing."""
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "email").text = email
    ET.SubElement(customer, "is_company_linked").text = is_company_linked
    if company_id:
        ET.SubElement(customer, "company_id").text = company_id
    if company_name:
        ET.SubElement(customer, "company_name").text = company_name

    address = ET.SubElement(customer, "address")
    ET.SubElement(address, "street").text = street
    ET.SubElement(address, "number").text = number
    ET.SubElement(address, "postal_code").text = postal_code
    ET.SubElement(address, "city").text = city
    ET.SubElement(address, "country").text = country

    if registration_fee:
        fee_el = ET.SubElement(body, "registration_fee")
        fee_el.text = registration_fee
        fee_el.set("currency", fee_currency)

    return root


def test_valid_new_registration() -> None:
    """A fully valid new_registration should return no errors."""
    root = build_registration_xml()
    errors = validate_message(root)
    assert errors == []


def test_valid_new_registration_company_linked() -> None:
    """A valid new_registration with is_company_linked=true should return no errors."""
    root = build_registration_xml(
        is_company_linked="true",
        company_id="FOSS-CUST-200",
        company_name="Bedrijf NV"
    )
    errors = validate_message(root)
    assert errors == []


def test_new_registration_missing_email() -> None:
    """Missing email must trigger missing_required_field error."""
    root = build_registration_xml(email="")
    errors = validate_message(root)
    assert any("missing_required_field" in e and "email" in e for e in errors)


def test_new_registration_missing_is_company_linked() -> None:
    """Missing is_company_linked must trigger missing_required_field error."""
    root = build_registration_xml(is_company_linked="")
    errors = validate_message(root)
    assert any("missing_required_field" in e and "is_company_linked" in e for e in errors)


def test_new_registration_missing_company_id_when_linked() -> None:
    """company_id must be present when is_company_linked=true."""
    root = build_registration_xml(
        is_company_linked="true",
        company_id="",
        company_name="Bedrijf NV"
    )
    errors = validate_message(root)
    assert any("company_id" in e for e in errors)


def test_new_registration_missing_company_name_when_linked() -> None:
    """company_name must be present when is_company_linked=true."""
    root = build_registration_xml(
        is_company_linked="true",
        company_id="FOSS-CUST-200",
        company_name=""
    )
    errors = validate_message(root)
    assert any("company_name" in e for e in errors)


@pytest.mark.parametrize("field,kwargs", [
    ("street",      {"street": ""}),
    ("number",      {"number": ""}),
    ("postal_code", {"postal_code": ""}),
    ("city",        {"city": ""}),
    ("country",     {"country": ""}),
])
def test_new_registration_missing_address_field(field: str, kwargs: dict) -> None:
    """Each missing address sub-field must trigger a missing_required_field error."""
    root = build_registration_xml(**kwargs)
    errors = validate_message(root)
    assert any(f"address.{field}" in e for e in errors)


def test_new_registration_missing_registration_fee() -> None:
    """Missing registration_fee must trigger missing_required_field error."""
    root = build_registration_xml(registration_fee="")
    errors = validate_message(root)
    assert any("registration_fee" in e for e in errors)


# Duplicate detection tests — use is_duplicate() directly

def test_duplicate_message_is_flagged() -> None:
    """A message_id already in seen_ids must be detected as duplicate."""
    seen_ids: set[str] = {"f47ac10b-58cc-4372-a567-0e02b2c3d479"}
    assert is_duplicate("f47ac10b-58cc-4372-a567-0e02b2c3d479", seen_ids) is True


def test_unique_message_is_not_flagged() -> None:
    """A message_id not in seen_ids must not be detected as duplicate."""
    seen_ids: set[str] = {"some-other-id"}
    assert is_duplicate("f47ac10b-58cc-4372-a567-0e02b2c3d479", seen_ids) is False
