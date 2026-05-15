"""
Test script for the consumption_order → invoice_request → event_ended flow.

Sends messages in the correct order:
  1. consumption_order per badge (items are saved in MySQL)
  2. invoice_request for one specific consumption_order (invoice is created)
  3. event_ended (remaining items without invoice_request are invoiced)

Run:
    python -m scripts.send_test_consumption_flow

Requirements:
  - RabbitMQ is running
  - Receiver is running (python -m src.main)
  - MySQL is running
  - .env correctly configured
"""

import time
import uuid
from datetime import datetime, timezone
from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── XML builders ──────────────────────────────────────────────────────────────

def build_consumption_order(
    message_id: str,
    customer_id: str,
    identity_uuid: str,
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
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>kassa</source>\n"
        "    <type>consumption_order</type>\n"
        "    <version>2.0</version>\n"
        "  </header>\n"
        "  <body>\n"
        "    <is_anonymous>false</is_anonymous>\n"
        "    <customer>\n"
        f"      <id>{customer_id}</id>\n"
        f"      <identity_uuid>{identity_uuid}</identity_uuid>\n"
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
    identity_uuid: str,
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
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>crm</source>\n"
        "    <type>invoice_request</type>\n"
        "    <version>2.0</version>\n"
        f"    <correlation_id>{consumption_order_message_id}</correlation_id>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <identity_uuid>{identity_uuid}</identity_uuid>\n"
        "    <invoice_data>\n"
        "      <contact>\n"
        f"        <first_name>{first_name}</first_name>\n"
        f"        <last_name>{last_name}</last_name>\n"
        "      </contact>\n"
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
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>frontend</source>\n"
        "    <type>event_ended</type>\n"
        "    <version>2.0</version>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <session_id>{session_id}</session_id>\n"
        f"    <ended_at>{ts()}</ended_at>\n"
        "  </body>\n"
        "</message>"
    )


# ── Test data ─────────────────────────────────────────────────────────────────
#
# Grouping test: BADGE-001 and BADGE-002 both order "Coffee" for the same
# company. On the invoice that should be merged into one Coffee ×5 line.
#
# Company NV (bedrijf-nv-001):
#   BADGE-001: Coca-Cola ×2 (€2.50), Coffee ×2 (€1.50)  ← Coffee is grouped
#   BADGE-002: Coffee ×3 (€1.50), Fanta ×1 (€2.50)      ← Coffee is grouped
#   → expected invoice lines via invoice_request: Coca-Cola ×2, Coffee ×5, Fanta ×1
#
# Tech Corp (tech-corp-001):
#   BADGE-003: Cola ×1 (€2.50), Coffee ×2 (€1.50)
#   → expected invoice lines via event_ended: Cola ×1, Coffee ×2

ORDER_ID_BADGE_001 = str(uuid.uuid4())
USER_ID_BADGE_001 = str(uuid.uuid4())

ORDER_ID_BADGE_002 = str(uuid.uuid4())
USER_ID_BADGE_002 = str(uuid.uuid4())

ORDER_ID_BADGE_003 = str(uuid.uuid4())
USER_ID_BADGE_003 = str(uuid.uuid4())


# ── Step 1: send consumption_orders ──────────────────────────────────────────

print("=" * 60)
print("STEP 1 — send consumption_orders")
print("=" * 60)

xml = build_consumption_order(
    message_id=ORDER_ID_BADGE_001,
    customer_id="bedrijf-nv-001",
    identity_uuid=USER_ID_BADGE_001,
    email="jan.peeters@bedrijf.com",
    items=[
        {"description": "Coca-Cola", "price": "2.50", "quantity": 2, "vat_rate": 21},
        {"description": "Coffee",    "price": "1.50", "quantity": 2, "vat_rate": 21},
    ],
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] consumption_order BADGE-001 | Coca-Cola ×2, Coffee ×2 | message_id={ORDER_ID_BADGE_001}")

time.sleep(1)

xml = build_consumption_order(
    message_id=ORDER_ID_BADGE_002,
    customer_id="bedrijf-nv-001",
    identity_uuid=USER_ID_BADGE_002,
    email="marie.janssen@bedrijf.com",
    items=[
        {"description": "Coffee", "price": "1.50", "quantity": 3, "vat_rate": 21},
        {"description": "Fanta",  "price": "2.50", "quantity": 1, "vat_rate": 21},
    ],
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] consumption_order BADGE-002 | Coffee ×3, Fanta ×1 | message_id={ORDER_ID_BADGE_002}")
print("     (Coffee from BADGE-001 and BADGE-002 should be merged → ×5)")

time.sleep(1)

xml = build_consumption_order(
    message_id=ORDER_ID_BADGE_003,
    customer_id="tech-corp-001",
    identity_uuid=USER_ID_BADGE_003,
    email="piet.janssen@techcorp.be",
    items=[
        {"description": "Cola",   "price": "2.50", "quantity": 1, "vat_rate": 21},
        {"description": "Coffee", "price": "1.50", "quantity": 2, "vat_rate": 21},
    ],
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] consumption_order BADGE-003 | Cola ×1, Coffee ×2 | message_id={ORDER_ID_BADGE_003}")

print()
print("Waiting 3 seconds for the receiver to save all orders...")
time.sleep(3)

# ── Step 2: invoice_request for Company NV (via BADGE-001) ────────────────────
#
# get_items_by_correlation_id picks up ALL items for bedrijf-nv-001
# (so both BADGE-001 and BADGE-002). The Coffee lines should be grouped.

print("=" * 60)
print("STEP 2 — invoice_request for Company NV (via BADGE-001)")
print("         Expected on invoice: Coca-Cola ×2, Coffee ×5 [grouped!], Fanta ×1")
print("=" * 60)

xml = build_invoice_request(
    consumption_order_message_id=ORDER_ID_BADGE_001,
    identity_uuid=USER_ID_BADGE_001,
    first_name="Jan",
    last_name="Peeters",
    email="jan.peeters@bedrijf.com",
    company_name="Bedrijf NV",
    vat_number="BE0123456789",
)
send_message(xml, routing_key=QUEUE)
print(f"[OK] invoice_request for Company NV | correlation_id={ORDER_ID_BADGE_001}")

print()
print("Waiting 3 seconds for the receiver to create the invoice...")
time.sleep(3)

# ── Step 3: event_ended ───────────────────────────────────────────────────────
#
# Company NV has already been fully processed in step 2.
# Tech Corp (BADGE-003) has not had an invoice_request yet → is invoiced here.

print("=" * 60)
print("STEP 3 — send event_ended")
print("         Expected: invoice for Tech Corp | Cola ×1, Coffee ×2")
print("         Company NV: already processed in step 2, no new invoice expected")
print("=" * 60)

xml = build_event_ended()
send_message(xml, routing_key=QUEUE)
print("[OK] event_ended sent")

print()
print("=" * 60)
print("Done. Check:")
print("  - FossBilling: 2 invoices expected")
print("    • Company NV → Coca-Cola ×2, Coffee ×5 [BADGE-001+002 grouped], Fanta ×1")
print("    • Tech Corp  → Cola ×1, Coffee ×2  (via event_ended)")
print("  - RabbitMQ facturatie.to.mailing: 2 mailing messages expected")
print("  - MySQL pending_consumptions: empty after processing")
print("=" * 60)
