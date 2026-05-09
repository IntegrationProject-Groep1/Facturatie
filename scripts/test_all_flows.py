"""
Alles-in-één testscript voor alle Facturatie flows.

Flows:
  registration  — new_registration (privé + bedrijf)
  consumption   — consumption_order → invoice_request → event_ended
  payment       — payment_registered (kassa betaling)
  cancel        — invoice_cancelled

Run:
    python -m scripts.test_all_flows                         # registration + consumption
    python -m scripts.test_all_flows --flow registration
    python -m scripts.test_all_flows --flow consumption
    python -m scripts.test_all_flows --flow payment  --invoice-id 14
    python -m scripts.test_all_flows --flow cancel   --invoice-id 14

Vereisten:
  - RabbitMQ draait
  - Receiver draait (python -m src.services.rabbitmq_receiver)
  - .env correct ingesteld
  - Voor consumption/payment/cancel: FossBilling draait
"""

import argparse
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message
from src.utils.xml_validator import validate_xml

QUEUE = "facturatie.incoming"


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def send_validated(xml_str: str, schema_name: str, description: str) -> bool:
    is_valid, error = validate_xml(xml_str, schema_name)
    if not is_valid:
        print(f"[FOUT] XSD-validatie mislukt voor '{description}':\n  {error}")
        return False
    send_message(xml_str, routing_key=QUEUE)
    print(f"[OK] {description} verstuurd")
    return True


# ── Builders ──────────────────────────────────────────────────────────────────

def build_new_registration(
    identity_uuid: str,
    email: str,
    first_name: str,
    last_name: str,
    date_of_birth: str,
    session_id: str,
    amount: str,
    customer_type: str = "private",
    company_name: str = "",
    vat_number: str = "",
    company_id: str = "",
) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = str(uuid.uuid4())
    ET.SubElement(header, "timestamp").text = ts()
    ET.SubElement(header, "source").text = "crm"
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "correlation_id").text = str(uuid.uuid4())

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "identity_uuid").text = identity_uuid
    ET.SubElement(customer, "email").text = email
    ET.SubElement(customer, "date_of_birth").text = date_of_birth
    contact = ET.SubElement(customer, "contact")
    ET.SubElement(contact, "first_name").text = first_name
    ET.SubElement(contact, "last_name").text = last_name
    ET.SubElement(customer, "type").text = customer_type
    if company_name:
        ET.SubElement(customer, "company_name").text = company_name
    if vat_number:
        ET.SubElement(customer, "vat_number").text = vat_number
    if company_id:
        ET.SubElement(customer, "company_id").text = company_id
    ET.SubElement(customer, "session_id").text = session_id
    payment_due = ET.SubElement(customer, "payment_due")
    amount_el = ET.SubElement(payment_due, "amount")
    amount_el.text = amount
    amount_el.set("currency", "eur")
    ET.SubElement(payment_due, "status").text = "unpaid"

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def build_consumption_order(
    message_id: str,
    customer_id: str,
    identity_uuid: str,
    email: str,
    items: list[dict],
    customer_type: str = "company",
) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = message_id
    ET.SubElement(header, "timestamp").text = ts()
    ET.SubElement(header, "source").text = "kassa"
    ET.SubElement(header, "type").text = "consumption_order"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "is_anonymous").text = "false"
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = customer_id
    ET.SubElement(customer, "identity_uuid").text = identity_uuid
    ET.SubElement(customer, "type").text = customer_type
    ET.SubElement(customer, "email").text = email

    items_el = ET.SubElement(body, "items")
    for i, item in enumerate(items):
        total = float(item["price"]) * item["quantity"]
        item_el = ET.SubElement(items_el, "item")
        ET.SubElement(item_el, "id").text = f"LINE-{i + 1:04d}"
        ET.SubElement(item_el, "sku").text = item.get("sku", f"SKU-{i + 1:03d}")
        ET.SubElement(item_el, "description").text = str(item["description"])
        ET.SubElement(item_el, "quantity").text = str(item["quantity"])
        unit_price_el = ET.SubElement(item_el, "unit_price")
        unit_price_el.text = str(item["price"])
        unit_price_el.set("currency", "eur")
        ET.SubElement(item_el, "vat_rate").text = str(item["vat_rate"])
        total_el = ET.SubElement(item_el, "total_amount")
        total_el.text = f"{total:.2f}"
        total_el.set("currency", "eur")

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def build_invoice_request(
    consumption_order_id: str,
    identity_uuid: str,
    first_name: str,
    last_name: str,
    email: str,
    street: str = "Teststraat",
    number: str = "1",
    postal_code: str = "1000",
    city: str = "Brussel",
    country: str = "BE",
    company_name: str = "",
    vat_number: str = "",
) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = str(uuid.uuid4())
    ET.SubElement(header, "timestamp").text = ts()
    ET.SubElement(header, "source").text = "crm"
    ET.SubElement(header, "type").text = "invoice_request"
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "correlation_id").text = consumption_order_id

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "identity_uuid").text = identity_uuid
    invoice_data = ET.SubElement(body, "invoice_data")
    contact = ET.SubElement(invoice_data, "contact")
    ET.SubElement(contact, "first_name").text = first_name
    ET.SubElement(contact, "last_name").text = last_name
    ET.SubElement(invoice_data, "email").text = email
    address = ET.SubElement(invoice_data, "address")
    ET.SubElement(address, "street").text = street
    ET.SubElement(address, "number").text = number
    ET.SubElement(address, "postal_code").text = postal_code
    ET.SubElement(address, "city").text = city
    ET.SubElement(address, "country").text = country
    if company_name:
        ET.SubElement(invoice_data, "company_name").text = company_name
    if vat_number:
        ET.SubElement(invoice_data, "vat_number").text = vat_number

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def build_event_ended(session_id: str) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = str(uuid.uuid4())
    ET.SubElement(header, "timestamp").text = ts()
    ET.SubElement(header, "source").text = "frontend"
    ET.SubElement(header, "type").text = "event_ended"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "session_id").text = session_id
    ET.SubElement(body, "ended_at").text = ts()

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def build_payment_registered(
    invoice_id: str,
    amount: str,
    identity_uuid: str,
    payment_method: str,
    payment_context: str = "consumption",
) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = str(uuid.uuid4())
    ET.SubElement(header, "timestamp").text = ts()
    ET.SubElement(header, "source").text = "kassa"
    ET.SubElement(header, "type").text = "payment_registered"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "identity_uuid").text = identity_uuid
    invoice = ET.SubElement(body, "invoice")
    ET.SubElement(invoice, "id").text = invoice_id
    amount_el = ET.SubElement(invoice, "amount_paid")
    amount_el.text = amount
    amount_el.set("currency", "eur")
    ET.SubElement(invoice, "status").text = "paid"
    ET.SubElement(body, "payment_context").text = payment_context
    transaction = ET.SubElement(body, "transaction")
    ET.SubElement(transaction, "id").text = str(uuid.uuid4())
    ET.SubElement(transaction, "payment_method").text = payment_method

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def build_invoice_cancelled(
    invoice_id: str,
    identity_uuid: str,
    reason: str = "",
) -> str:
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = str(uuid.uuid4())
    ET.SubElement(header, "timestamp").text = ts()
    ET.SubElement(header, "source").text = "crm"
    ET.SubElement(header, "type").text = "invoice_cancelled"
    ET.SubElement(header, "version").text = "2.0"

    body = ET.SubElement(root, "body")
    ET.SubElement(body, "invoice_id").text = invoice_id
    ET.SubElement(body, "identity_uuid").text = identity_uuid
    if reason:
        ET.SubElement(body, "reason").text = reason

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ── Flows ─────────────────────────────────────────────────────────────────────

def flow_registration():
    suffix = uuid.uuid4().hex[:6]
    print("\n" + "=" * 60)
    print("FLOW: new_registration")
    print("=" * 60)

    xml = build_new_registration(
        identity_uuid=str(uuid.uuid4()),
        email=f"jan.peeters-{suffix}@voorbeeld.be",
        first_name="Jan",
        last_name="Peeters",
        date_of_birth="1990-06-15",
        session_id="sess-keynote-001",
        amount="150.00",
        customer_type="private",
    )
    send_validated(xml, "new_registration", "new_registration (privé)")
    time.sleep(1)

    xml = build_new_registration(
        identity_uuid=str(uuid.uuid4()),
        email=f"marie.janssen-{suffix}@bedrijf.be",
        first_name="Marie",
        last_name="Janssen",
        date_of_birth="1985-04-20",
        session_id="sess-keynote-001",
        amount="250.00",
        customer_type="company",
        company_name="Bedrijf NV",
        vat_number="BE0123456789",
        company_id=f"CRM-COMP-{suffix.upper()}",
    )
    send_validated(xml, "new_registration", "new_registration (bedrijf)")

    print("\nControleer:")
    print("  - FossBilling: 2 nieuwe klanten + facturen aangemaakt")
    print("  - RabbitMQ facturatie.to.mailing: 2 mailing berichten verwacht")


def flow_consumption():
    suffix = uuid.uuid4().hex[:6]
    order_id_a = str(uuid.uuid4())
    order_id_b = str(uuid.uuid4())
    uuid_a = str(uuid.uuid4())
    uuid_b = str(uuid.uuid4())
    session_id = f"SESSION-{suffix.upper()}"

    print("\n" + "=" * 60)
    print("FLOW: consumption_order → invoice_request → event_ended")
    print("=" * 60)

    print("\n[Stap 1] consumption_orders versturen...")
    xml = build_consumption_order(
        message_id=order_id_a,
        customer_id=f"bedrijf-nv-{suffix}",
        identity_uuid=uuid_a,
        email=f"jan-{suffix}@bedrijf.com",
        items=[
            {"description": "Coca-Cola", "price": "2.50", "quantity": 2, "vat_rate": 21},
            {"description": "Water",     "price": "1.50", "quantity": 1, "vat_rate": 6},
        ],
    )
    send_validated(xml, "consumption_order", f"consumption_order A ({order_id_a[:8]}...)")
    time.sleep(1)

    xml = build_consumption_order(
        message_id=order_id_b,
        customer_id=f"bedrijf-nv-{suffix}",
        identity_uuid=uuid_b,
        email=f"marie-{suffix}@bedrijf.com",
        items=[
            {"description": "Fanta", "price": "2.50", "quantity": 3, "vat_rate": 21},
        ],
    )
    send_validated(xml, "consumption_order", f"consumption_order B ({order_id_b[:8]}...) — gaat via event_ended")

    print("\nWachten zodat de receiver de orders opslaat...")
    time.sleep(3)

    print("\n[Stap 2] invoice_request voor order A...")
    xml = build_invoice_request(
        consumption_order_id=order_id_a,
        identity_uuid=uuid_a,
        first_name="Jan",
        last_name="Peeters",
        email=f"jan-{suffix}@bedrijf.com",
        company_name="Bedrijf NV",
        vat_number="BE0123456789",
    )
    send_validated(xml, "invoice_request", f"invoice_request (correlation_id={order_id_a[:8]}...)")

    print("\nWachten zodat de receiver de factuur aanmaakt...")
    time.sleep(3)

    print("\n[Stap 3] event_ended versturen (pikt order B op)...")
    xml = build_event_ended(session_id)
    send_validated(xml, "event_ended", f"event_ended (session_id={session_id})")

    print("\nControleer:")
    print("  - FossBilling: 2 facturen aangemaakt (A via invoice_request, B via event_ended)")
    print("  - RabbitMQ facturatie.to.mailing: 2 mailing berichten verwacht")
    print("  - MySQL pending_consumptions: leeg na verwerking")


def flow_payment(invoice_id: str):
    print("\n" + "=" * 60)
    print("FLOW: payment_registered (kassa)")
    print("=" * 60)

    xml = build_payment_registered(
        invoice_id=invoice_id,
        amount="150.00",
        identity_uuid=str(uuid.uuid4()),
        payment_method="on_site",
        payment_context="consumption",
    )
    send_validated(xml, "payment_registered", f"payment_registered (invoice_id={invoice_id})")

    print("\nControleer:")
    print(f"  - FossBilling: factuur {invoice_id} moet status 'paid' hebben")
    print("  - RabbitMQ crm.incoming: payment_registered bevestiging verwacht")


def flow_cancel(invoice_id: str):
    print("\n" + "=" * 60)
    print("FLOW: invoice_cancelled")
    print("=" * 60)

    xml = build_invoice_cancelled(
        invoice_id=invoice_id,
        identity_uuid=str(uuid.uuid4()),
        reason="Test annulering",
    )
    send_validated(xml, "invoice_cancelled", f"invoice_cancelled (invoice_id={invoice_id})")

    print("\nControleer:")
    print(f"  - FossBilling: factuur {invoice_id} moet status 'cancelled' hebben")
    print("    (of een failed notificatie als al betaald/geannuleerd)")
    print("  - RabbitMQ crm.incoming: bevestigings- of failed bericht verwacht")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Facturatie flow tester")
    parser.add_argument(
        "--flow",
        choices=["registration", "consumption", "payment", "cancel"],
        help="Specifieke flow om te testen (standaard: registration + consumption)",
    )
    parser.add_argument(
        "--invoice-id",
        help="FossBilling invoice ID (vereist voor --flow payment en --flow cancel)",
    )
    args = parser.parse_args()

    if args.flow == "payment":
        if not args.invoice_id:
            print("[FOUT] --invoice-id is vereist voor de payment flow.")
            print("  Gebruik: python -m scripts.test_all_flows --flow payment --invoice-id 14")
            sys.exit(1)
        flow_payment(args.invoice_id)
    elif args.flow == "cancel":
        if not args.invoice_id:
            print("[FOUT] --invoice-id is vereist voor de cancel flow.")
            print("  Gebruik: python -m scripts.test_all_flows --flow cancel --invoice-id 14")
            sys.exit(1)
        flow_cancel(args.invoice_id)
    elif args.flow == "registration":
        flow_registration()
    elif args.flow == "consumption":
        flow_consumption()
    else:
        flow_registration()
        time.sleep(2)
        flow_consumption()

    print("\n" + "=" * 60)
    print("Klaar. Check de receiver logs voor details:")
    print("  docker logs <container-naam>")
    print("=" * 60)
