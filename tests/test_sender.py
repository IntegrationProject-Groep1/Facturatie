"""
Tests for rabbitmq_sender XML builders.
Consolidates: test_invoice_request.py (fully rewritten to new XSD)
"""
import re
import xml.etree.ElementTree as ET
import pytest
from unittest.mock import patch

from src.services.rabbitmq_sender import (
    build_invoice_created_notification_xml,
    build_invoice_status_xml,
    build_payment_confirmed_xml,
)

INVOICE_ID = "INV-2026-001"
RECIPIENT_EMAIL = "info@bedrijf.be"
CORRELATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
CUSTOMER_ID = "CUST-42"


def parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


# ── build_invoice_created_notification_xml (send_mailing) ────────────────────

@pytest.fixture
def notification_xml(monkeypatch):
    monkeypatch.setattr("src.services.rabbitmq_sender.BILLING_WEB_URL", "https://portal.yourdomain.com")
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        return build_invoice_created_notification_xml(
            invoice_id=INVOICE_ID,
            recipient_email=RECIPIENT_EMAIL,
            correlation_id=CORRELATION_ID,
            first_name="Jan",
            last_name="Peeters",
            customer_id=CUSTOMER_ID,
        )


def test_notification_type(notification_xml) -> None:
    assert parse(notification_xml).findtext("header/type") == "send_mailing"


def test_notification_version(notification_xml) -> None:
    assert parse(notification_xml).findtext("header/version") == "2.0"


def test_notification_source_defaults_to_facturatie(notification_xml) -> None:
    assert parse(notification_xml).findtext("header/source") == "facturatie"


def test_notification_message_id_is_uuid(notification_xml) -> None:
    msg_id = parse(notification_xml).findtext("header/message_id")
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        msg_id,
    )


def test_notification_correlation_id_in_header(notification_xml) -> None:
    assert parse(notification_xml).findtext("header/correlation_id") == CORRELATION_ID


def test_notification_no_master_uuid_in_header(notification_xml) -> None:
    assert parse(notification_xml).findtext("header/master_uuid") is None


def test_notification_recipient_email(notification_xml) -> None:
    root = parse(notification_xml)
    assert root.findtext("body/recipients/recipient/email") == RECIPIENT_EMAIL


def test_notification_invoice_id(notification_xml) -> None:
    import json
    root = parse(notification_xml)
    template_data = json.loads(root.findtext("body/template_data"))
    assert template_data["invoice_id"] == INVOICE_ID


def test_notification_pdf_url_format(notification_xml) -> None:
    import json
    root = parse(notification_xml)
    template_data = json.loads(root.findtext("body/template_data"))
    assert template_data["pdf_url"] == f"https://portal.yourdomain.com/invoice/{INVOICE_ID}"


def test_notification_mail_type(notification_xml) -> None:
    assert parse(notification_xml).findtext("body/mail_type") == "invoice_ready"


def test_notification_xsd_validation_error_raises() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(False, "missing field")):
        with pytest.raises(ValueError, match="XSD validation failed"):
            build_invoice_created_notification_xml(
                invoice_id=INVOICE_ID,
                recipient_email=RECIPIENT_EMAIL,
                correlation_id=CORRELATION_ID,
            )


# ── build_payment_confirmed_xml ───────────────────────────────────────────────

@pytest.fixture
def payment_xml():
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        return build_payment_confirmed_xml(
            invoice_id=INVOICE_ID,
            identity_uuid=CUSTOMER_ID,
            amount="150.00",
            currency="eur",
            payment_method="cash",
            paid_at="2026-05-01T10:00:00Z",
            payment_context="consumption",
        )


def test_payment_type(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/type") == "payment_registered"


def test_payment_version(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/version") == "2.0"


def test_payment_source(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/source") == "facturatie"


def test_payment_no_correlation_id_when_not_passed(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/correlation_id") is None


def test_payment_correlation_id_included_when_passed() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        xml = build_payment_confirmed_xml(
            invoice_id=INVOICE_ID,
            identity_uuid=CUSTOMER_ID,
            amount="150.00",
            currency="eur",
            payment_method="online",
            correlation_id=CORRELATION_ID,
        )
    assert parse(xml).findtext("header/correlation_id") == CORRELATION_ID


def test_payment_no_master_uuid(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/master_uuid") is None


def test_payment_invoice_id_in_body(payment_xml) -> None:
    assert parse(payment_xml).findtext("body/invoice/id") == INVOICE_ID


def test_payment_customer_id_in_body(payment_xml) -> None:
    assert parse(payment_xml).findtext("body/identity_uuid") == CUSTOMER_ID


def test_payment_amount_and_currency(payment_xml) -> None:
    amount_el = parse(payment_xml).find("body/invoice/amount_paid")
    assert amount_el is not None
    assert amount_el.text == "150.00"
    assert amount_el.get("currency") == "eur"


def test_payment_method(payment_xml) -> None:
    assert parse(payment_xml).findtext("body/invoice/status") == "paid"


def test_payment_non_eur_currency_is_forced_to_eur() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        xml = build_payment_confirmed_xml(
            invoice_id=INVOICE_ID,
            identity_uuid=CUSTOMER_ID,
            amount="100.00",
            currency="USD",
            payment_method="card",
        )
    amount_el = parse(xml).find("body/invoice/amount_paid")
    assert amount_el.get("currency") == "eur"


def test_payment_xsd_validation_error_raises() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(False, "bad field")):
        with pytest.raises(ValueError, match="XSD validation failed"):
            build_payment_confirmed_xml(
                invoice_id=INVOICE_ID,
                identity_uuid=CUSTOMER_ID,
                amount="100.00",
                currency="eur",
                payment_method="card",
            )


# ── build_invoice_status_xml ──────────────────────────────────────────────────

IDENTITY_UUID = "a1b2c3d4-e5f6-4789-abcd-ef0123456789"


@pytest.fixture
def status_xml_sent():
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        return build_invoice_status_xml(
            invoice_id=INVOICE_ID,
            identity_uuid=IDENTITY_UUID,
            status="sent",
            amount="150.00",
            correlation_id=CORRELATION_ID,
        )


def test_invoice_status_type(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("header/type") == "invoice_status"


def test_invoice_status_version(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("header/version") == "2.0"


def test_invoice_status_source(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("header/source") == "facturatie"


def test_invoice_status_message_id_is_uuid(status_xml_sent) -> None:
    msg_id = parse(status_xml_sent).findtext("header/message_id")
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        msg_id,
    )


def test_invoice_status_correlation_id(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("header/correlation_id") == CORRELATION_ID


def test_invoice_status_no_correlation_id_when_not_passed() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        xml = build_invoice_status_xml(
            invoice_id=INVOICE_ID,
            identity_uuid=IDENTITY_UUID,
            status="paid",
            amount="150.00",
        )
    assert parse(xml).findtext("header/correlation_id") is None


def test_invoice_status_invoice_id(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("body/invoice_id") == INVOICE_ID


def test_invoice_status_identity_uuid(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("body/identity_uuid") == IDENTITY_UUID


def test_invoice_status_status_field(status_xml_sent) -> None:
    assert parse(status_xml_sent).findtext("body/status") == "sent"


def test_invoice_status_amount_and_currency(status_xml_sent) -> None:
    amount_el = parse(status_xml_sent).find("body/amount")
    assert amount_el is not None
    assert amount_el.text == "150.00"
    assert amount_el.get("currency") == "eur"


@pytest.mark.parametrize("status", ["draft", "sent", "paid", "overdue", "cancelled"])
def test_invoice_status_all_valid_statuses(status) -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        xml = build_invoice_status_xml(
            invoice_id=INVOICE_ID,
            identity_uuid=IDENTITY_UUID,
            status=status,
            amount="100.00",
        )
    assert parse(xml).findtext("body/status") == status


def test_invoice_status_xsd_validation_error_raises() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(False, "missing field")):
        with pytest.raises(ValueError, match="XSD validation failed"):
            build_invoice_status_xml(
                invoice_id=INVOICE_ID,
                identity_uuid=IDENTITY_UUID,
                status="sent",
                amount="100.00",
            )
