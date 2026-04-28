import re
import xml.etree.ElementTree as ET
from src.services.rabbitmq_sender import build_invoice_created_notification_xml


INVOICE_ID = "INV-2026-001"
RECIPIENT_EMAIL = "info@bedrijf.be"
CORRELATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
COMPANY_NAME = "Bedrijf NV"
MASTER_UUID = "88888-AAAAA-UUID-TEST"

def parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_type() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/type") == "invoice_created_notification"

def test_version() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/version") == "1.0"

def test_master_uuid_header() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/master_uuid") == MASTER_UUID

def test_recipient_email() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("body/recipient_email") == RECIPIENT_EMAIL

def test_invoice_request_master_uuid() -> None:
    """master_uuid must be present in the header."""
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/master_uuid") == MASTER_UUID

def test_invoice_id() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("body/invoice_id") == INVOICE_ID


def test_pdf_url_format() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    pdf_url = root.findtext("body/pdf_url")
    assert pdf_url == f'https://portal.yourdomain.com/invoice/{INVOICE_ID}'


def test_master_uuid_in_header() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/master_uuid") == MASTER_UUID


def test_message_id_is_uuid() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    msg_id = root.findtext("header/message_id")
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", msg_id)


def test_no_correlation_id_in_header() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/correlation_id") is None


def test_invoice_request_source() -> None:
    """source must default to facturatie."""
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL, MASTER_UUID))
    assert root.findtext("header/source") == "facturatie"
