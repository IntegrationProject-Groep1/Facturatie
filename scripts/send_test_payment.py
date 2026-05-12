"""
Testscript voor de payment_registered flow.

Stuurt een payment_registered bericht naar de receiver die dan:
  1. De betaling registreert in FossBilling (factuur op 'paid' zetten)
  2. Een bevestiging stuurt naar facturatie.to.crm

Pas INVOICE_ID aan naar een bestaande factuur-ID uit FossBilling.

Run:
    python -m scripts.send_test_payment
"""

import uuid
from datetime import datetime, timezone
from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"

# ── Pas dit aan ───────────────────────────────────────────────────────────────
INVOICE_ID = "14"               # ID van een bestaande factuur in FossBilling
AMOUNT = "150.00"               # Bedrag dat betaald wordt
IDENTITY_UUID = str(uuid.uuid4())  # Optioneel — klant UUID (laat leeg voor anoniem)
# ─────────────────────────────────────────────────────────────────────────────

PAYMENT_METHOD = "on_site"   # on_site | online | company_link


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payment_registered(
    invoice_id: str,
    amount: str,
    identity_uuid: str,
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
        f"    <timestamp>{ts()}</timestamp>\n"
        "    <source>kassa</source>\n"
        "    <type>payment_registered</type>\n"
        "    <version>2.0</version>\n"
        "  </header>\n"
        "  <body>\n"
        f"    <identity_uuid>{identity_uuid}</identity_uuid>\n"
        "    <invoice>\n"
        f"      <id>{invoice_id}</id>\n"
        f"      <amount_paid currency=\"eur\">{amount}</amount_paid>\n"
        "      <status>paid</status>\n"
        f"      <due_date>{due_date}</due_date>\n"
        "    </invoice>\n"
        "    <payment_context>consumption</payment_context>\n"
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
        identity_uuid=IDENTITY_UUID,
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
