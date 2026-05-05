"""
Testscript voor de consumption_order → invoice_request → event_ended flow.

Stuurt berichten in de juiste volgorde:
  1. consumption_order per badge (items worden opgeslagen in MySQL)
  2. invoice_request voor één specifieke consumption_order (factuur wordt aangemaakt)
  3. event_ended (resterende items zonder invoice_request worden gefactureerd)

Run:
    python -m scripts.send_test_consumption_flow

Vereisten:
  - RabbitMQ draait
  - Receiver draait (python -m src.services.rabbitmq_receiver)
  - MySQL draait
  - .env correct ingesteld
"""

import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── XML builders ──────────────────────────────────────────────────────────────

def build_consumption_order(
    message_id: str,
    customer_id: str,
    user_id: str,
    email: str,
    items: list[dict],
    customer_type: str = "company",
) -> str:
    items_xml = ""
    for i, item in enumerate(items):
        total = float(item["price"]) * item["quantity"]
        items_xml += f"""
    <item>
      <id>LINE-{i + 1:04d}</id>
      <sku>{item.get("sku", f"SKU-{i+1:03d}")}</sku>
      <description>{item["description"]}</description>
      <quantity>{item["quantity"]}</quantity>
      <unit_price currency="eur">{item["price"]}</unit_price>
      <vat_rate>{item["vat_rate"]}</vat_rate>
      <total_amount currency="eur">{total:.2f}</total_amount>
    </item>"""

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<message>\n"
        "  <header>\n"
        f"    <message_id>{message_id}</message_id>\n"
        "    <type>consumption_order</type>\n"
        f"    <source>crm</source>\n"
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <version>2.0</version>\n"
        "  </header>\n"
        "  <body>\n"
        "    <is_anonymous>false</is_anonymous>\n"
        "    <customer>\n"
        f"      <id>{customer_id}</id>\n"
        f"      <user_id>{user_id}</user_id>\n"
        f"      <type>{customer_type}</type>\n"
        f"      <email>{email}</email>\n"
        "    </customer>\n"
        f"    <items>{items_xml}\n"
        "    </items>\n"
        "  </body>\n"
        "</message>"
    )
    return xml


def build_invoice_request(
    consumption_order_message_id: str,
    user_id: str,
    first_name: str,
    last_name: str,
    email: str,
    company_name: str,
    vat_number: str = "",
    street: str = "Teststraat",
    number: str = "1",
    postal_code: str = "1000",
    city: str = "Brussel",
    country: str = "BE",
) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<message>\n"
        "  <header>\n"
        f"    <message_id>{uuid.uuid4()}</message_id>\n"
        "    <type>invoice_request</type>\n"
        "    <source>crm</source>\n"
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <version>2.0</version>\n"
        f"    <correlation_id>{consumption_order_message_id}</correlation_id>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <user_id>{user_id}</user_id>\n"
        "    <invoice_data>\n"
        f"      <first_name>{first_name}</first_name>\n"
        f"      <last_name>{last_name}</last_name>\n"
        f"      <email>{email}</email>\n"
        "      <address>\n"
        f"        <street>{street}</street>\n"
        f"        <number>{number}</number>\n"
        f"        <postal_code>{postal_code}</postal_code>\n"
        f"        <city>{city}</city>\n"
        f"        <country>{country}</country>\n"
        "      </address>\n"
        f"      <company_name>{company_name}</company_name>\n"
        f"      <vat_number>{vat_number}</vat_number>\n"
        "    </invoice_data>\n"
        "  </body>\n"
        "</message>"
    )


def build_event_ended() -> str:
    session_id = f"SESSION-{uuid.uuid4().hex[:8].upper()}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<message>\n"
        "  <header>\n"
        f"    <message_id>{uuid.uuid4()}</message_id>\n"
        "    <version>2.0</version>\n"
        "    <type>event_ended</type>\n"
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>frontend</source>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <session_id>{session_id}</session_id>\n"
        f"    <ended_at>{ts()}</ended_at>\n"
        "  </body>\n"
        "</message>"
    )


# ── Test data ─────────────────────────────────────────────────────────────────

# Bedrijf NV — BADGE-001: krijgt een invoice_request (directe facturatie)
ORDER_ID_BADGE_001 = str(uuid.uuid4())
USER_ID_BADGE_001 = str(uuid.uuid4())

# Bedrijf NV — BADGE-002: geen invoice_request, wordt meegenomen in event_ended
ORDER_ID_BADGE_002 = str(uuid.uuid4())
USER_ID_BADGE_002 = str(uuid.uuid4())

# Tech Corp — BADGE-003: krijgt ook een invoice_request
ORDER_ID_BADGE_003 = str(uuid.uuid4())
USER_ID_BADGE_003 = str(uuid.uuid4())


# ── Stap 1: consumption_orders versturen ──────────────────────────────────────

print("=" * 60)
print("STAP 1 — consumption_orders versturen")
print("=" * 60)

xml = build_consumption_order(
    message_id=ORDER_ID_BADGE_001,
    customer_id="bedrijf-nv-001",
    user_id=USER_ID_BADGE_001,
    email="jan.peeters@bedrijf.com",
    items=[
        {"description": "Coca-Cola", "price": "2.50", "quantity": 2, "vat_rate": 21},
        {"description": "Water",     "price": "1.50", "quantity": 1, "vat_rate": 6},
    ],
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] consumption_order BADGE-001 | message_id={ORDER_ID_BADGE_001}")

time.sleep(1)

xml = build_consumption_order(
    message_id=ORDER_ID_BADGE_002,
    customer_id="bedrijf-nv-001",
    user_id=USER_ID_BADGE_002,
    email="marie.janssen@bedrijf.com",
    items=[
        {"description": "Fanta", "price": "2.50", "quantity": 3, "vat_rate": 21},
    ],
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] consumption_order BADGE-002 | message_id={ORDER_ID_BADGE_002}")

time.sleep(1)

xml = build_consumption_order(
    message_id=ORDER_ID_BADGE_003,
    customer_id="tech-corp-001",
    user_id=USER_ID_BADGE_003,
    email="piet.janssen@techcorp.be",
    items=[
        {"description": "Koffie", "price": "1.50", "quantity": 2, "vat_rate": 21},
        {"description": "Cola",   "price": "2.50", "quantity": 1, "vat_rate": 21},
    ],
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] consumption_order BADGE-003 | message_id={ORDER_ID_BADGE_003}")

print()
print("Wachten 3 seconden zodat de receiver alle orders kan opslaan...")
time.sleep(3)

# ── Stap 2: invoice_requests voor BADGE-001 en BADGE-003 ─────────────────────

print("=" * 60)
print("STAP 2 — invoice_requests versturen (voor BADGE-001 en BADGE-003)")
print("         BADGE-002 krijgt geen invoice_request → wordt opgepikt door event_ended")
print("=" * 60)

xml = build_invoice_request(
    consumption_order_message_id=ORDER_ID_BADGE_001,
    user_id=USER_ID_BADGE_001,
    first_name="Jan",
    last_name="Peeters",
    email="jan.peeters@bedrijf.com",
    company_name="Bedrijf NV",
    vat_number="BE0123456789",
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] invoice_request voor BADGE-001 | correlation_id={ORDER_ID_BADGE_001}")

time.sleep(1)

xml = build_invoice_request(
    consumption_order_message_id=ORDER_ID_BADGE_003,
    user_id=USER_ID_BADGE_003,
    first_name="Piet",
    last_name="Janssen",
    email="piet.janssen@techcorp.be",
    company_name="Tech Corp",
    vat_number="BE0987654321",
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] invoice_request voor BADGE-003 | correlation_id={ORDER_ID_BADGE_003}")

print()
print("Wachten 3 seconden zodat de receiver de facturen kan aanmaken...")
time.sleep(3)

# ── Stap 3: event_ended ───────────────────────────────────────────────────────

print("=" * 60)
print("STAP 3 — event_ended versturen")
print("         Verwacht: factuur voor BADGE-002 (Fanta) onder Bedrijf NV")
print("=" * 60)

xml = build_event_ended()
send_message(xml, routing_key=QUEUE)
print("[OK] event_ended verstuurd")

print()
print("=" * 60)
print("Klaar. Controleer:")
print("  - FossBilling: 3 facturen verwacht")
print("    • Bedrijf NV billing account → Coca-Cola + Water (BADGE-001)")
print("    • Tech Corp billing account  → Koffie + Cola (BADGE-003)")
print("    • Bedrijf NV billing account → Fanta (BADGE-002, via event_ended)")
print("  - RabbitMQ crm.to.mailing: 3 mailing berichten verwacht")
print("  - MySQL pending_consumptions: leeg na verwerking")
print("=" * 60)
