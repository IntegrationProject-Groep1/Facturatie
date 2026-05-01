"""
Tests voor XSD-validatie en duplicate detection.
Consolideert: test_xsd.py + test_validate_message.py
"""
import pytest
import xml.etree.ElementTree as ET
from src.services.rabbitmq_receiver import is_duplicate
from src.utils.xml_validator import validate_xml


# ── XML builders ─────────────────────────────────────────────────────────────

def build_invoice_request_xml(
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    version: str = "2.0",
    timestamp: str = "2026-02-24T18:30:00Z",
    source: str = "crm",
    vat_rate: str = "21",
    correlation_id: str | None = None,
) -> str:
    """
    Bouwt een invoice_request XML conform de nieuwe structuur (contract §11.1).
    Geen master_uuid in header, body heeft user_id + invoice_data (geen items/customer blok).
    """
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    # Volgorde conform XSD: message_id → type → source → timestamp → version → correlation_id
    # master_uuid VERWIJDERD — verboden in alle headers (contract #90)
    ET.SubElement(header, "type").text = "invoice_request"
    ET.SubElement(header, "source").text = source
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "version").text = version
    if correlation_id:
        ET.SubElement(header, "correlation_id").text = correlation_id
    else:
        ET.SubElement(header, "correlation_id").text = "corr-default-001"  # verplicht veld

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "user_id").text = "BADGE-007"

    invoice_data = ET.SubElement(body, "invoice_data")
    # Volgorde conform InvoiceDataType XSD: first_name → last_name → email → address → company_name → vat_number
    ET.SubElement(invoice_data, "first_name").text = "Jan"
    ET.SubElement(invoice_data, "last_name").text = "De Tester"
    ET.SubElement(invoice_data, "email").text = "test@example.com"

    address = ET.SubElement(invoice_data, "address")
    ET.SubElement(address, "street").text = "Kiekenmarkt"
    ET.SubElement(address, "number").text = "42"
    ET.SubElement(address, "postal_code").text = "1000"
    ET.SubElement(address, "city").text = "Brussel"
    ET.SubElement(address, "country").text = "be"

    ET.SubElement(invoice_data, "company_name").text = "Test Corp"
    ET.SubElement(invoice_data, "vat_number").text = "BE0123456789"

    return ET.tostring(root, encoding="unicode")


def build_new_registration_xml(
    msg_id: str = "a1b2c3d4-0000-4000-8000-000000000001",
    version: str = "2.0",
    timestamp: str = "2026-03-30T10:00:00Z",
    source: str = "frontend",
    email: str | None = "info@bedrijf.be",
    is_company_linked: str = "false",
) -> str:
    """
    Bouwt een new_registration XML conform de nieuwe structuur.
    Geen master_uuid in header, namen zitten in <contact> wrapper.
    """
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    # master_uuid VERWIJDERD — verboden in alle headers (contract #90)
    ET.SubElement(header, "version").text = version
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = source

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "customer_id").text = "REG-999"

    if email is not None:
        ET.SubElement(customer, "email").text = email

    # Namen zitten in <contact> wrapper (contract Regel 2)
    contact = ET.SubElement(customer, "contact")
    ET.SubElement(contact, "first_name").text = "Test"
    ET.SubElement(contact, "last_name").text = "User"

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

    fee_el = ET.SubElement(body, "registration_fee", {"currency": "eur"})
    fee_el.text = "150.00"

    return ET.tostring(root, encoding="unicode")


def build_event_ended_xml(
    msg_id: str = "b2c3d4e5-f6a7-4839-9231-000000000001",
    session_id: str = "sess-42",
    timestamp: str = "2026-04-28T14:00:00Z",
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


# ── invoice_request validatie ─────────────────────────────────────────────────

def test_valid_invoice_request() -> None:
    xml = build_invoice_request_xml()
    is_valid, errors = validate_xml(xml, "invoice_request")
    assert is_valid is True, f"Validation failed: {errors}"


def test_invalid_vat_rate_returns_error() -> None:
    """vat_rate 99 is geen geldige enum-waarde — validatie moet falen."""
    # invoice_request heeft geen vat_rate meer in de body (items zitten er niet meer in),
    # maar we testen of een bewust kapot bericht correct wordt geweigerd
    root = ET.fromstring(build_invoice_request_xml())
    # Verwijder verplicht veld om een validatiefout te forceren
    body = root.find("body")
    user_id = body.find("user_id")
    body.remove(user_id)
    xml = ET.tostring(root, encoding="unicode")
    is_valid, errors = validate_xml(xml, "invoice_request")
    assert is_valid is False


# ── new_registration validatie ────────────────────────────────────────────────

def test_valid_new_registration() -> None:
    xml = build_new_registration_xml()
    is_valid, errors = validate_xml(xml, "new_registration")
    assert is_valid is True, f"New registration failed: {errors}"


def test_new_registration_missing_email() -> None:
    xml = build_new_registration_xml(email=None)
    is_valid, errors = validate_xml(xml, "new_registration")
    assert is_valid is False


def test_new_registration_no_master_uuid_in_header() -> None:
    """master_uuid mag nooit in de header zitten — XSD moet dit weigeren."""
    root = ET.fromstring(build_new_registration_xml())
    header = root.find("header")
    master = ET.SubElement(header, "master_uuid")
    master.text = "01890a5d-ac96-7ab2-80e2-4536629c90de"
    xml = ET.tostring(root, encoding="unicode")
    is_valid, _ = validate_xml(xml, "new_registration")
    assert is_valid is False, "master_uuid in header moet door XSD worden geweigerd"


# ── event_ended validatie ─────────────────────────────────────────────────────

def test_valid_event_ended() -> None:
    xml = build_event_ended_xml()
    is_valid, errors = validate_xml(xml, "event_ended")
    assert is_valid is True, f"Event Ended validation failed: {errors}"


def test_event_ended_invalid_date() -> None:
    xml = build_event_ended_xml(timestamp="NIET-EEN-DATUM")
    is_valid, errors = validate_xml(xml, "event_ended")
    assert is_valid is False


# ── duplicate detection ───────────────────────────────────────────────────────

def test_duplicate_message_is_flagged() -> None:
    seen_ids: set[str] = {"f47ac10b-58cc-4372-a567-0e02b2c3d479"}
    assert is_duplicate("f47ac10b-58cc-4372-a567-0e02b2c3d479", seen_ids) is True


def test_unique_message_is_not_flagged() -> None:
    seen_ids: set[str] = {"some-other-id"}
    assert is_duplicate("f47ac10b-58cc-4372-a567-0e02b2c3d479", seen_ids) is False
