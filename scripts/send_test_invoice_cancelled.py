"""
Testscript voor de invoice_cancelled flow.

Stuurt een invoice_cancelled bericht naar de receiver die dan:
  1. De status van de factuur controleert in FossBilling
  2a. Als de factuur annuleerbaar is: zet hem op 'cancelled' en stuurt bevestiging naar facturatie.to.crm
  2b. Als de factuur al betaald/geannuleerd is: stuurt een failed notificatie naar facturatie.to.crm
  2c. Als de factuur niet bestaat: stuurt een failed notificatie naar facturatie.to.crm

Pas INVOICE_ID en CUSTOMER_ID aan voor je test.

Run:
    python -m scripts.send_test_invoice_cancelled
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"

# ── Pas dit aan ───────────────────────────────────────────────────────────────
INVOICE_ID  = "10"      # ID van een bestaande factuur in FossBilling
CUSTOMER_ID = "12345"   # Klant-ID
REASON      = "Customer requested cancellation"  # Optioneel
# ─────────────────────────────────────────────────────────────────────────────


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_invoice_cancelled(
    invoice_id: str,
    customer_id: str,
    reason: str = "",
) -> str:
    msg_id = str(uuid.uuid4())
    reason_xml = f"\n    <reason>{reason}</reason>" if reason else ""

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<message>\n"
        "  <header>\n"
        f"    <message_id>{msg_id}</message_id>\n"
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>crm</source>\n"
        "    <type>invoice_cancelled</type>\n"
        "    <version>2.0</version>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <invoice_id>{invoice_id}</invoice_id>\n"
        f"    <user_id>{customer_id}</user_id>"
        f"{reason_xml}\n"
        "  </body>\n"
        "</message>"
    )


if __name__ == "__main__":
    xml = build_invoice_cancelled(
        invoice_id=INVOICE_ID,
        customer_id=CUSTOMER_ID,
        reason=REASON,
    )

    print("=" * 60)
    print("[TEST] Versturen invoice_cancelled")
    print(f"[TEST] Invoice ID:   {INVOICE_ID}")
    print(f"[TEST] Customer ID:  {CUSTOMER_ID}")
    print(f"[TEST] Reden:        {REASON or '(geen)'}")
    print(f"[TEST] Queue:        {QUEUE}")
    print("=" * 60)
    print(xml)
    print("=" * 60)

    send_message(xml, routing_key=QUEUE)

    print("\n[OK] Bericht verstuurd. Controleer:")
    print(f"  - FossBilling: factuur {INVOICE_ID} moet status 'cancelled' hebben")
    print("    (tenzij al betaald/geannuleerd — dan zie je een failed notificatie)")
    print("  - RabbitMQ facturatie.to.crm: bevestigings- of failed bericht verwacht")
    print("  - Receiver logs: geen errors")
    print()
    print("Scenario's om te testen:")
    print("  1. Gebruik een factuur met status 'unpaid'  → wordt geannuleerd")
    print("  2. Gebruik een factuur met status 'paid'    → blocked, failed notificatie")
    print("  3. Gebruik een niet-bestaand invoice ID     → failed notificatie")
