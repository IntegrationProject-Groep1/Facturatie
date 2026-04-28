import re
import xml.etree.ElementTree as ET
from src.services.rabbitmq_sender import build_invoice_created_notification_xml


INVOICE_ID = "INV-2026-001"
RECIPIENT_EMAIL = "info@bedrijf.be"


def parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


def test_type() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("header/type") == "invoice_created_notification"


def test_version() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("header/version") == "1.0"


def test_recipient_email() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("body/recipient_email") == RECIPIENT_EMAIL


def test_invoice_id() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("body/invoice_id") == INVOICE_ID


def test_subject_present() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("body/subject") is not None


def test_message_text_present() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("body/message_text") is not None


def test_pdf_url_contains_invoice_id() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert INVOICE_ID in root.findtext("body/pdf_url")


def test_message_id_is_uuid() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    msg_id = root.findtext("header/message_id")
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", msg_id)


def test_no_correlation_id_in_header() -> None:
    root = parse(build_invoice_created_notification_xml(INVOICE_ID, RECIPIENT_EMAIL))
    assert root.findtext("header/correlation_id") is None
