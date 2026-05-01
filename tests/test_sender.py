"""
Tests voor rabbitmq_sender XML builders.
Consolideert: test_invoice_request.py (volledig herschreven aan nieuwe XSD)
"""
import os
import re
import xml.etree.ElementTree as ET
import pytest
from unittest.mock import patch

from src.services.rabbitmq_sender import (
    build_invoice_created_notification_xml,
    build_payment_confirmed_xml,
)

INVOICE_ID = "INV-2026-001"
RECIPIENT_EMAIL = "info@bedrijf.be"
CORRELATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
CUSTOMER_ID = "CUST-42"


def parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str.split("\n", 1)[1])


# ── invoice_created_notification ──────────────────────────────────────────────

@pytest.fixture
def notification_xml(monkeypatch):
    """Bouwt een invoice_created_notification XML met gemockte BILLING_WEB_URL."""
    monkeypatch.setenv("BILLING_WEB_URL", "https://portal.yourdomain.com")
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        return build_invoice_created_notification_xml(
            invoice_id=INVOICE_ID,
            recipient_email=RECIPIENT_EMAIL,
            correlation_id=CORRELATION_ID,
        )


def test_notification_type(notification_xml) -> None:
    assert parse(notification_xml).findtext("header/type") == "invoice_created_notification"


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
    """correlation_id vervangt master_uuid als koppelingssleutel (contract #90)."""
    assert parse(notification_xml).findtext("header/correlation_id") == CORRELATION_ID


def test_notification_no_master_uuid_in_header(notification_xml) -> None:
    """master_uuid mag nooit in de header zitten (contract #90)."""
    assert parse(notification_xml).findtext("header/master_uuid") is None


def test_notification_recipient_email(notification_xml) -> None:
    assert parse(notification_xml).findtext("body/recipient_email") == RECIPIENT_EMAIL


def test_notification_invoice_id(notification_xml) -> None:
    assert parse(notification_xml).findtext("body/invoice_id") == INVOICE_ID


def test_notification_pdf_url_format(notification_xml) -> None:
    pdf_url = parse(notification_xml).findtext("body/pdf_url")
    assert pdf_url == f"https://portal.yourdomain.com/invoice/{INVOICE_ID}"


def test_notification_xsd_validation_error_raises() -> None:
    """Als XSD-validatie faalt moet de builder een ValueError gooien."""
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
    """Bouwt een payment_registered (outgoing) XML met gemockte XSD-validatie."""
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        return build_payment_confirmed_xml(
            invoice_id=INVOICE_ID,
            customer_id=CUSTOMER_ID,
            amount="150.00",
            currency="eur",
            payment_method="on_site",
            correlation_id=CORRELATION_ID,
            paid_at="2026-05-01T10:00:00Z",
        )


def test_payment_type(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/type") == "payment_registered"


def test_payment_version(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/version") == "2.0"


def test_payment_source(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/source") == "facturatie"


def test_payment_correlation_id(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/correlation_id") == CORRELATION_ID


def test_payment_no_master_uuid(payment_xml) -> None:
    assert parse(payment_xml).findtext("header/master_uuid") is None


def test_payment_invoice_id_in_body(payment_xml) -> None:
    """Outgoing body heeft losse invoice_id — geen invoice/transaction blokken (contract §8.2)."""
    assert parse(payment_xml).findtext("body/invoice_id") == INVOICE_ID


def test_payment_customer_id_in_body(payment_xml) -> None:
    assert parse(payment_xml).findtext("body/customer_id") == CUSTOMER_ID


def test_payment_amount_and_currency(payment_xml) -> None:
    amount_el = parse(payment_xml).find("body/amount_paid")
    assert amount_el is not None
    assert amount_el.text == "150.00"
    assert amount_el.get("currency") == "eur"


def test_payment_method(payment_xml) -> None:
    assert parse(payment_xml).findtext("body/payment_method") == "on_site"


def test_payment_non_eur_currency_is_forced_to_eur() -> None:
    """Niet-EUR valuta wordt geforceerd naar 'eur' met een warning (contract Regel 3)."""
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(True, None)):
        xml = build_payment_confirmed_xml(
            invoice_id=INVOICE_ID,
            customer_id=CUSTOMER_ID,
            amount="100.00",
            currency="USD",
            payment_method="online",
            correlation_id=CORRELATION_ID,
        )
    amount_el = parse(xml).find("body/amount_paid")
    assert amount_el.get("currency") == "eur"


def test_payment_xsd_validation_error_raises() -> None:
    with patch("src.services.rabbitmq_sender.validate_xml", return_value=(False, "bad field")):
        with pytest.raises(ValueError, match="XSD validation failed"):
            build_payment_confirmed_xml(
                invoice_id=INVOICE_ID,
                customer_id=CUSTOMER_ID,
                amount="100.00",
                currency="eur",
                payment_method="online",
                correlation_id=CORRELATION_ID,
            )
