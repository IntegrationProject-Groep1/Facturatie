"""
Test script for the payment_registered flow.

Sends a payment_registered message to the receiver which then:
  1. Registers the payment in FossBilling (sets invoice to 'paid')
  2. Sends a confirmation to crm.incoming

Change INVOICE_ID to an existing invoice ID in FossBilling.

Run:
    python -m scripts.send_test_payment
"""

import uuid
from datetime import datetime, timezone
from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"

# ── Adjust this ───────────────────────────────────────────────────────────────
INVOICE_ID = "185"               # ID of an existing invoice in FossBilling
AMOUNT = "150.00"               # Amount being paid
IDENTITY_UUID = str(uuid.uuid4())  # Optional — customer UUID (leave empty for anonymous)
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
    print("[TEST] Sending payment_registered")
    print(f"[TEST] Invoice ID:      {INVOICE_ID}")
    print(f"[TEST] Amount:          {AMOUNT} EUR")
    print(f"[TEST] Payment method:  {PAYMENT_METHOD}")
    print(f"[TEST] Queue:           {QUEUE}")
    print("=" * 60)
    print(xml)
    print("=" * 60)

    send_message(xml, routing_key=QUEUE)

    print("\n[OK] Message sent. Check:")
    print(f"  - FossBilling: invoice {INVOICE_ID} should have status 'paid'")
    print("  - RabbitMQ crm.incoming: confirmation message expected")
    print("  - Receiver logs: no errors")
