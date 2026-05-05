"""
Testscript voor de payment_registered flow.

Stuurt een payment_registered bericht naar de receiver die dan:
  1. De betaling registreert in FossBilling (factuur op 'paid' zetten)
  2. Een bevestiging stuurt naar facturatie.to.crm

Pas INVOICE_ID aan naar een bestaande factuur-ID uit FossBilling.

Run:
    python -m scripts.send_test_payment
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"

# ── Pas dit aan ───────────────────────────────────────────────────────────────
INVOICE_ID = "14"          # ID van een bestaande factuur in FossBilling
AMOUNT     = "150.00"      # Bedrag dat betaald wordt
USER_ID    = str(uuid.uuid4())  # Optioneel — klant-ID
# ─────────────────────────────────────────────────────────────────────────────

PAYMENT_METHOD = "on_site"   # on_site | online | company_link


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payment_registered(
    invoice_id: str,
    amount: str,
    user_id: str,
    payment_method: str,
) -> str:
    msg_id = str(uuid.uuid4())
    transaction_id = f"TRANS-{uuid.uuid4().hex[:8].upper()}"
    due_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<message>\n"
        "  <header>\n"
        f"    <message_id>{msg_id}</message_id>\n"
        "    <version>2.0</version>\n"
        "    <type>payment_registered</type>\n"
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>kassa</source>\n"
        "  </header>\n"
        "  <body>\n"
        "    <payment_context>consumption</payment_context>\n"
        f"    <user_id>{user_id}</user_id>\n"
        "    <invoice>\n"
        f"      <id>{invoice_id}</id>\n"
        "      <status>paid</status>\n"
        f"      <amount_paid currency=\"eur\">{amount}</amount_paid>\n"
        f"      <due_date>{due_date}</due_date>\n"
        "    </invoice>\n"
        "    <transaction>\n"
        f"      <id>{transaction_id}</id>\n"
        f"      <payment_method>{payment_method}</payment_method>\n"
        "    </transaction>\n"
        "  </body>\n"
        "</message>"
    )


if __name__ == "__main__":
    xml = build_payment_registered(
        invoice_id=INVOICE_ID,
        amount=AMOUNT,
        user_id=USER_ID,
        payment_method=PAYMENT_METHOD,
    )

    print("=" * 60)
    print("[TEST] Versturen payment_registered")
    print(f"[TEST] Invoice ID:      {INVOICE_ID}")
    print(f"[TEST] Bedrag:          {AMOUNT} EUR")
    print(f"[TEST] Betaalmethode:   {PAYMENT_METHOD}")
    print(f"[TEST] Queue:           {QUEUE}")
    print("=" * 60)
    print(xml)
    print("=" * 60)

    send_message(xml, routing_key=QUEUE)

    print("\n[OK] Bericht verstuurd. Controleer:")
    print(f"  - FossBilling: factuur {INVOICE_ID} moet status 'paid' hebben")
    print("  - RabbitMQ facturatie.to.crm: bevestigingsbericht verwacht")
    print("  - Receiver logs: geen errors")
