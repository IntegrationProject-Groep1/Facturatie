"""
Test the 3 scenarios for invoice_cancelled:

  Scenario 1: Paid registration invoice  → credit note created in FossBilling
  Scenario 2: Paid consumption invoice   → blocked (consumption_invoice_cannot_be_cancelled)
  Scenario 3: Unpaid invoice             → invoice cancelled in FossBilling

Adjust the INVOICE IDs to existing invoices in FossBilling before testing.

Run:
    python -m scripts.send_test_invoice_cancelled
"""

import uuid
from datetime import datetime, timezone
from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"

# Scenario 1: a PAID invoice with item "Inschrijvingskosten" (registration)
PAID_REGISTRATION_INVOICE_ID = "185"

# Scenario 2: a PAID consumption invoice (e.g. Duvel/Bitterballen items)
PAID_CONSUMPTION_INVOICE_ID = "181"

# Scenario 3: an UNPAID registration invoice
UNPAID_INVOICE_ID = "184"

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
    print(f"[OK] Sent to {QUEUE}")


if __name__ == "__main__":
    print("Invoice Cancelled Test — 3 scenarios")

    run_scenario(
        1,
        "Paid registration invoice → expected: credit note in FossBilling",
        PAID_REGISTRATION_INVOICE_ID,
        reason="Participant unregistered",
    )

    input("\nPress Enter for scenario 2...")

    run_scenario(
        2,
        "Paid consumption invoice → expected: cancellation_failed (consumption_invoice_cannot_be_cancelled)",
        PAID_CONSUMPTION_INVOICE_ID,
        reason="Test consumption blocking",
    )

    input("\nPress Enter for scenario 3...")

    run_scenario(
        3,
        "Unpaid invoice → expected: invoice cancelled in FossBilling",
        UNPAID_INVOICE_ID,
        reason="Test cancellation",
    )

    print("\n" + "="*60)
    print("Done. Check:")
    print("  Scenario 1 → FossBilling: new credit note invoice visible")
    print("  Scenario 2 → Receiver log: 'consumption_invoice_cannot_be_cancelled'")
    print(f"  Scenario 3 → FossBilling: invoice {UNPAID_INVOICE_ID} status = cancelled")
    print("="*60)
