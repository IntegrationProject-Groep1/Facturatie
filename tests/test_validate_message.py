import pytest
import xml.etree.ElementTree as ET
from src.services.rabbitmq_receiver import validate_message

def build_xml(
    msg_type: str = "CONSUMPTION_ORDER",
    msg_id: str = "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    version: str = "2.0",
    timestamp: str = "2026-02-24T18:30:00Z",
    source: str = "Kassa_Bar_01",
    is_company_linked: str = "false",
    company_id: str = "",
    company_name: str = "",
    vat_rate: str = "21",
    correlation_id: str = ""
) -> ET.Element:
    """Helper that builds a minimal valid XML element for testing."""
    company_id_tag = f"<company_id>{company_id}</company_id>" if company_id else ""
    company_name_tag = f"<company_name>{company_name}</company_name>" if company_name else ""
    correlation_tag = f"<correlation_id>{correlation_id}</correlation_id>" if correlation_id else ""

    raw = f"""
    <message>
        <header>
            <id>{msg_id}</id>
            <version>{version}</version>
            <type>{msg_type}</type>
            <timestamp>{timestamp}</timestamp>
            <source>{source}</source>
            {correlation_tag}
        </header>
        <body>
            <customer>
                <id>12345</id>
                <is_company_linked>{is_company_linked}</is_company_linked>
                {company_id_tag}
                {company_name_tag}
            </customer>
            <items>
                <item>
                    <id>BEV-001</id>
                    <description>Coffee</description>
                    <quantity>2</quantity>
                    <price_unit currency="EUR">2.50</price_unit>
                    <vat_rate>{vat_rate}</vat_rate>
                </item>
            </items>
        </body>
    </message>
    """
    return ET.fromstring(raw)

# valid message test for order
def test_valid_consumption_order() -> None:
    """A fully valid CONSUMPTION_ORDER should return no errors."""
    root = build_xml()
    errors = validate_message(root)
    assert errors == []

# VAT rate tests
def test_invalid_vat_rate_returns_error() -> None:
    """A vat_rate that is not 6, 12 or 21 must return an error."""
    root = build_xml(vat_rate="99")
    errors = validate_message(root)
    assert any("vat_rate" in e for e in errors)


def test_valid_vat_rate_6() -> None:
    root = build_xml(vat_rate="6")
    errors = validate_message(root)
    assert not any("vat_rate" in e for e in errors)


def test_valid_vat_rate_12() -> None:
    root = build_xml(vat_rate="12")
    errors = validate_message(root)
    assert not any("vat_rate" in e for e in errors)


def test_valid_vat_rate_21() -> None:
    root = build_xml(vat_rate="21")
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
    root = build_xml(msg_id="")
    errors = validate_message(root)
    assert any("<id>" in e for e in errors)


def test_missing_timestamp() -> None:
    root = build_xml(timestamp="")
    errors = validate_message(root)
    assert any("<timestamp>" in e for e in errors)


def test_missing_source() -> None:
    root = build_xml(source="")
    errors = validate_message(root)
    assert any("<source>" in e for e in errors)


def test_unknown_message_type() -> None:
    root = build_xml(msg_type="UNKNOWN_TYPE")
    errors = validate_message(root)
    assert any("unknown" in e.lower() for e in errors)

# PAYMENT_REGISTER specific tests

def test_payment_registered_missing_correlation_id() -> None:
    """PAYMENT_REGISTERED without correlation_id must return an error."""
    root = build_xml(msg_type="PAYMENT_REGISTERED", correlation_id="")
    errors = validate_message(root)
    assert any("correlation_id" in e for e in errors)


def test_payment_registered_with_correlation_id() -> None:
    """PAYMENT_REGISTERED with correlation_id present — no correlation error."""
    root = build_xml(
        msg_type="PAYMENT_REGISTERED",
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


# Duplicate detection tests

def test_duplicate_message_is_flagged() -> None:
    """A message whose ID was already seen must be flagged as duplicate."""
    seen_ids: set[str] = {"f47ac10b-58cc-4372-a567-0e02b2c3d479"}
    root = build_xml(msg_id="f47ac10b-58cc-4372-a567-0e02b2c3d479")
    errors = validate_message(root, seen_ids=seen_ids)
    assert any("duplicate" in e.lower() for e in errors)


def test_unique_message_is_not_flagged() -> None:
    """A message with a new ID should not be flagged as duplicate."""
    seen_ids: set[str] = {"some-other-id"}
    root = build_xml(msg_id="f47ac10b-58cc-4372-a567-0e02b2c3d479")
    errors = validate_message(root, seen_ids=seen_ids)
    assert not any("duplicate" in e.lower() for e in errors)