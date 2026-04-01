import xml.etree.ElementTree as ET
from src.services.rabbitmq_sender import build_invoice_request_xml


INVOICE_ID = "INV-2026-001"
CLIENT_EMAIL = "info@bedrijf.be"
CORRELATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
COMPANY_NAME = "Bedrijf NV"


def parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_invoice_request_type() -> None:
    """type must be invoice_request."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("header/type") == "invoice_request"


def test_invoice_request_version() -> None:
    """version must be 2.0."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("header/version") == "2.0"


def test_invoice_request_correlation_id() -> None:
    """correlation_id must match the provided value."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("header/correlation_id") == CORRELATION_ID


def test_invoice_request_invoice_id() -> None:
    """invoice_id in body must match the provided value."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("body/invoice_id") == INVOICE_ID


def test_invoice_request_client_email() -> None:
    """client_email in body must match the provided value."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("body/client_email") == CLIENT_EMAIL


def test_invoice_request_without_company_name() -> None:
    """company_name must not be present when not provided."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("body/company_name") is None


def test_invoice_request_with_company_name() -> None:
    """company_name must be present when provided."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID, company_name=COMPANY_NAME))
    assert root.findtext("body/company_name") == COMPANY_NAME


def test_invoice_request_message_id_is_uuid() -> None:
    """message_id must be a valid UUID v4 format."""
    import re
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    msg_id = root.findtext("header/message_id")
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", msg_id)


def test_invoice_request_source() -> None:
    """source must default to facturatie."""
    root = parse(build_invoice_request_xml(INVOICE_ID, CLIENT_EMAIL, CORRELATION_ID))
    assert root.findtext("header/source") == "facturatie"
