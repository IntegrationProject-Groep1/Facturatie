"""
Handmatig testscript — stuurt een new_registration bericht naar RabbitMQ.

Gebruik:
    python scripts/send_test_registration.py

Vereisten:
    - .env bestand met RabbitMQ connectiegegevens
    - Receiver draait en luistert op QUEUE_INCOMING
"""
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dotenv import load_dotenv
from src.services.rabbitmq_sender import send_message
from src.utils.xml_validator import validate_xml

load_dotenv()

# ── Testdata — pas hier aan naar wens ────────────────────────────────────────

MSG_ID = str(uuid.uuid4())
CORRELATION_ID = str(uuid.uuid4())
TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
UNIQUE_SUFFIX = uuid.uuid4().hex[:6]

IDENTITY_UUID = str(uuid.uuid4())
EMAIL = f"testklant-{UNIQUE_SUFFIX}@voorbeeld.be"
FIRST_NAME = "John"
LAST_NAME = "Smith"
DATE_OF_BIRTH = "1990-06-15"
IS_COMPANY = False          # True = bedrijfsklant, False = particulier
COMPANY_ID = ""             # Enkel nodig als IS_COMPANY = True
COMPANY_NAME = ""           # Enkel nodig als IS_COMPANY = True
VAT_NUMBER = ""             # Optioneel

REGISTRATION_FEE = "150.00"
FEE_CURRENCY = "eur"

QUEUE = "crm.to.facturatie"

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

    ET.SubElement(customer, "type").text = "company" if IS_COMPANY else "private"

    if IS_COMPANY:
        ET.SubElement(customer, "company_name").text = COMPANY_NAME
        if VAT_NUMBER:
            ET.SubElement(customer, "vat_number").text = VAT_NUMBER
        ET.SubElement(customer, "company_id").text = COMPANY_ID

    payment_due = ET.SubElement(customer, "payment_due")
    amount_el = ET.SubElement(payment_due, "amount")
    amount_el.text = REGISTRATION_FEE
    amount_el.set("currency", FEE_CURRENCY)
    ET.SubElement(payment_due, "status").text = "unpaid"

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ── Uitvoering ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    xml = build_xml()

    print("=" * 60)
    print("[TEST] Gegenereerde XML:")
    print("=" * 60)
    print(xml)
    print("=" * 60)

    # Valideer tegen XSD voor verzending
    is_valid, error = validate_xml(xml, "new_registration")
    if not is_valid:
        print(f"\n[FOUT] XSD-validatie mislukt — bericht NIET verzonden:\n  {error}")
        exit(1)

    print("\n[OK] XSD-validatie geslaagd")
    print(f"[TEST] Versturen naar queue: '{QUEUE}'")
    print(f"[TEST] Identity UUID: {IDENTITY_UUID}")
    print(f"[TEST] Email:         {EMAIL}")
    print(f"[TEST] Naam:          {FIRST_NAME} {LAST_NAME}")
    print(f"[TEST] Geboortedatum: {DATE_OF_BIRTH}")
    print(f"[TEST] Bedrijf:       {'ja — ' + COMPANY_NAME if IS_COMPANY else 'nee'}")
    print(f"[TEST] Bedrag:        {REGISTRATION_FEE} {FEE_CURRENCY.upper()}")
    print(f"[TEST] message_id:    {MSG_ID}")

    send_message(xml, routing_key=QUEUE)

    print("\n[OK] Bericht verzonden — controleer nu FossBilling en de receiver logs.")
