"""
Sends invoice_request messages for two companies to the RabbitMQ incoming queue:
  - Bedrijf NV: BADGE-001 (Coca-Cola, Water) and BADGE-002 (Fanta)
  - Tech Corp:  BADGE-003 (Koffie)

Run:
    python scripts/send_invoice_request.py
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message  # noqa: E402

QUEUE = "crm.to.facturatie"


def build_invoice_request(
    badge_id: str,
    master_uuid: str,
    items: list[dict],
    company_id: str,
    company_name: str,
    email: str,
    first_name: str = "Test",
    last_name: str = "Medewerker",
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items_xml = ""
    for item in items:
        items_xml += f"""
      <item>
        <description>{item['description']}</description>
        <quantity>{item['quantity']}</quantity>
        <unit_price currency="eur">{item['price']}</unit_price>
        <vat_rate>{item['vat_rate']}</vat_rate>
      </item>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>{uuid.uuid4()}</message_id>
    <master_uuid>{master_uuid}</master_uuid>
    <version>2.0</version>
    <type>invoice_request</type>
    <timestamp>{ts}</timestamp>
    <source>crm</source>
  </header>
  <body>
    <customer>
      <customer_id>{badge_id}</customer_id>
      <email>{email}</email>
      <first_name>{first_name}</first_name>
      <last_name>{last_name}</last_name>
      <is_company_linked>true</is_company_linked>
      <company_id>{company_id}</company_id>
      <company_name>{company_name}</company_name>
      <address>
        <street>Teststraat</street>
        <number>1</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>be</country>
      </address>
    </customer>
    <invoice>
      <description>Consumptions</description>
      <amount currency="eur">0.00</amount>
      <due_date>2026-12-31</due_date>
    </invoice>
    <items>{items_xml}
    </items>
  </body>
</message>"""


# --- Bedrijf NV ---

# BADGE-001: Coca-Cola + Water
xml1 = build_invoice_request(
    badge_id="BADGE-001",
    master_uuid=str(uuid.uuid4()),
    company_id="Bedrijf NV",
    company_name="Bedrijf NV",
    email="jan.peeters@bedrijf.com",
    first_name="Jan",
    last_name="Peeters",
    items=[
        {"description": "Coca-Cola", "price": "2.50", "quantity": 1, "vat_rate": "21"},
        {"description": "Water",     "price": "1.50", "quantity": 2, "vat_rate": "6"},
    ],
)
send_message(xml1, routing_key=QUEUE)
print("Sent invoice_request for BADGE-001 (Coca-Cola, Water) — Bedrijf NV")

# BADGE-002: Fanta
xml2 = build_invoice_request(
    badge_id="BADGE-002",
    master_uuid=str(uuid.uuid4()),
    company_id="Bedrijf NV",
    company_name="Bedrijf NV",
    email="marie.janssen@bedrijf.com",
    first_name="Marie",
    last_name="Janssen",
    items=[
        {"description": "Fanta", "price": "2.50", "quantity": 3, "vat_rate": "21"},
    ],
)
send_message(xml2, routing_key=QUEUE)
print("Sent invoice_request for BADGE-002 (Fanta) — Bedrijf NV")

# --- Tech Corp ---

# BADGE-003: Koffie
xml3 = build_invoice_request(
    badge_id="BADGE-003",
    master_uuid=str(uuid.uuid4()),
    company_id="Tech Corp",
    company_name="Tech Corp",
    email="piet.janssen@techcorp.be",
    first_name="Piet",
    last_name="Janssen",
    items=[
        {"description": "Koffie", "price": "1.50", "quantity": 2, "vat_rate": "21"},
    ],
)
send_message(xml3, routing_key=QUEUE)
print("Sent invoice_request for BADGE-003 (Koffie) — Tech Corp")

# BADGE-004: Sara Jan — ook van Tech Corp
xml4 = build_invoice_request(
    badge_id="BADGE-004",
    master_uuid=str(uuid.uuid4()),
    company_id="Tech Corp",
    company_name="Tech Corp",
    email="sara.jan@techcorp.be",
    first_name="Sara",
    last_name="Jan",
    items=[
        {"description": "Cola",  "price": "2.50", "quantity": 1, "vat_rate": "21"},
        {"description": "Fanta", "price": "2.50", "quantity": 1, "vat_rate": "21"},
    ],
)
send_message(xml4, routing_key=QUEUE)
print("Sent invoice_request for BADGE-004 (Cola, Fanta) — Tech Corp")

print("\nDone. Check MySQL — items should be saved for Bedrijf NV and Tech Corp.")
