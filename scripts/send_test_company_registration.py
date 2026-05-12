"""
Test script for a company registration (new_registration with type=company).

Difference from a private registration:
  - type = company
  - company_id and company_name are present
  - vat_number optional but included
  - FossBilling creates the client with company name

Run:
    python -m scripts.send_test_company_registration

Requirements:
    - .env file with RabbitMQ connection details
    - Mock identity service running (python -m scripts.mock_identity_service)
    - Receiver running and listening on QUEUE_INCOMING
"""
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dotenv import load_dotenv
from src.services.rabbitmq_sender import send_message
from src.utils.xml_validator import validate_xml

load_dotenv()

# ── Test data — adjust as needed ─────────────────────────────────────────────

UNIQUE_SUFFIX = uuid.uuid4().hex[:6]
MSG_ID = str(uuid.uuid4())
CORRELATION_ID = str(uuid.uuid4())
TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

IDENTITY_UUID = str(uuid.uuid4())
EMAIL = f"jan.peeters-{UNIQUE_SUFFIX}@company.be"
FIRST_NAME = "Jan"
LAST_NAME = "Peeters"
DATE_OF_BIRTH = "1985-04-20"

COMPANY_ID = f"CRM-COMP-{UNIQUE_SUFFIX.upper()}"
COMPANY_NAME = "Bedrijf NV"
VAT_NUMBER = "BE0123456789"

REGISTRATION_FEE = "250.00"
FEE_CURRENCY = "eur"

QUEUE = "facturatie.incoming"

# ─────────────────────────────────────────────────────────────────────────────


def build_xml() -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = MSG_ID
    ET.SubElement(header, "timestamp").text = TIMESTAMP
    ET.SubElement(header, "source").text = "crm"
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "correlation_id").text = CORRELATION_ID

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")

    ET.SubElement(customer, "identity_uuid").text = IDENTITY_UUID
    ET.SubElement(customer, "email").text = EMAIL
    ET.SubElement(customer, "date_of_birth").text = DATE_OF_BIRTH

    contact = ET.SubElement(customer, "contact")
    ET.SubElement(contact, "first_name").text = FIRST_NAME
    ET.SubElement(contact, "last_name").text = LAST_NAME

    ET.SubElement(customer, "type").text = "company"
    ET.SubElement(customer, "company_name").text = COMPANY_NAME
    ET.SubElement(customer, "vat_number").text = VAT_NUMBER
    ET.SubElement(customer, "company_id").text = COMPANY_ID

    payment_due = ET.SubElement(customer, "payment_due")
    amount_el = ET.SubElement(payment_due, "amount")
    amount_el.text = REGISTRATION_FEE
    amount_el.set("currency", FEE_CURRENCY)
    ET.SubElement(payment_due, "status").text = "unpaid"

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


if __name__ == "__main__":
    xml = build_xml()

    print("=" * 60)
    print("[TEST] Generated XML:")
    print("=" * 60)
    print(xml)
    print("=" * 60)

    is_valid, error = validate_xml(xml, "new_registration")
    if not is_valid:
        print(f"\n[ERROR] XSD validation failed — message NOT sent:\n  {error}")
        exit(1)

    print("\n[OK] XSD validation passed")
    print(f"[TEST] Sending to queue: '{QUEUE}'")
    print(f"[TEST] Identity UUID: {IDENTITY_UUID}")
    print(f"[TEST] Email:         {EMAIL}")
    print(f"[TEST] Name:          {FIRST_NAME} {LAST_NAME}")
    print(f"[TEST] Date of birth: {DATE_OF_BIRTH}")
    print(f"[TEST] Company:       {COMPANY_NAME} ({COMPANY_ID})")
    print(f"[TEST] VAT number:    {VAT_NUMBER}")
    print(f"[TEST] Amount:        {REGISTRATION_FEE} {FEE_CURRENCY.upper()}")
    print(f"[TEST] message_id:    {MSG_ID}")

    send_message(xml, routing_key=QUEUE)

    print("\n[OK] Message sent. Check:")
    print(f"  - FossBilling: client created with company name '{COMPANY_NAME}'")
    print("  - RabbitMQ facturatie.to.mailing: send_mailing message expected")
    print("  - Receiver logs: no errors")
