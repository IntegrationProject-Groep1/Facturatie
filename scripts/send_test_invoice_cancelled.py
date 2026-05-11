"""
Test de 3 scenario's voor invoice_cancelled:

  Scenario 1: Paid registratiefactuur  → creditnota aangemaakt in FossBilling
  Scenario 2: Paid consumptiefactuur   → geblokkeerd (consumption_invoice_already_paid)
  Scenario 3: Unpaid factuur           → factuur geannuleerd in FossBilling

Pas de INVOICE IDs aan naar bestaande facturen in FossBilling voor je test.

Run:
    python -m scripts.send_test_invoice_cancelled
"""

import uuid
from datetime import datetime, timezone
from src.services.rabbitmq_sender import send_message

QUEUE = "crm.to.facturatie"

# Scenario 1: een PAID factuur met item "Inschrijvingskosten" (registratie)
PAID_REGISTRATION_INVOICE_ID = "64"

# Scenario 2: een PAID consumptiefactuur (bv. Duvel/Bitterballen items)
PAID_CONSUMPTION_INVOICE_ID = "62"

# Scenario 3: een UNPAID registratiefactuur
UNPAID_INVOICE_ID = "68"

IDENTITY_UUID = str(uuid.uuid4())
# ─────────────────────────────────────────────────────────────────────────────


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_invoice_cancelled(invoice_id: str, reason: str = "") -> str:
    reason_xml = f"\n    <reason>{reason}</reason>" if reason else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<message>\n"
        "  <header>\n"
        f"    <message_id>{uuid.uuid4()}</message_id>\n"
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>crm</source>\n"
        "    <type>invoice_cancelled</type>\n"
        "    <version>2.0</version>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <invoice_id>{invoice_id}</invoice_id>\n"
        f"    <identity_uuid>{IDENTITY_UUID}</identity_uuid>"
        f"{reason_xml}\n"
        "  </body>\n"
        "</message>"
    )


def run_scenario(number: int, description: str, invoice_id: str, reason: str = ""):
    print(f"\n{'='*60}")
    print(f"Scenario {number}: {description}")
    print(f"Invoice ID: {invoice_id}")
    print("="*60)
    xml = build_invoice_cancelled(invoice_id, reason)
    send_message(xml, routing_key=QUEUE)
    print(f"[OK] Verstuurd naar {QUEUE}")


if __name__ == "__main__":
    print("Invoice Cancelled Test — 3 scenario's")

    run_scenario(
        1,
        "Paid registratiefactuur → verwacht: creditnota in FossBilling",
        PAID_REGISTRATION_INVOICE_ID,
        reason="Deelnemer uitgeschreven",
    )

    input("\nDruk Enter voor scenario 2...")

    run_scenario(
        2,
        "Paid consumptiefactuur → verwacht: cancellation_failed (consumption_invoice_cannot_be_cancelled)",
        PAID_CONSUMPTION_INVOICE_ID,
        reason="Test blokkering consumptie",
    )

    input("\nDruk Enter voor scenario 3...")

    run_scenario(
        3,
        "Unpaid factuur → verwacht: factuur geannuleerd in FossBilling",
        UNPAID_INVOICE_ID,
        reason="Test annulering",
    )

    print("\n" + "="*60)
    print("Klaar. Controleer:")
    print(f"  Scenario 1 → FossBilling: nieuwe creditnota factuur zichtbaar")
    print(f"  Scenario 2 → Receiver log: 'consumption_invoice_cannot_be_cancelled'")
    print(f"  Scenario 3 → FossBilling: factuur {UNPAID_INVOICE_ID} status = cancelled")
    print("="*60)
