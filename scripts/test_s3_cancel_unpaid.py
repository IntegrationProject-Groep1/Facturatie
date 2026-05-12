"""
Scenario 3 — Unpaid registration invoice cancelled.

Sends invoice_cancelled for invoice 184 (unpaid registration: Inschrijvingskosten).
Expected result:
  - FossBilling: invoice 184 status → cancelled
  - crm.incoming: type=invoice_status with status=cancelled
  - crm.incoming: type=invoice_cancelled

Run:
    python -m scripts.test_s3_cancel_unpaid

Make sure the listener is active first:
    python -m scripts.listen_crm_queue
"""
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from src.services.rabbitmq_sender import send_message

load_dotenv()

QUEUE = "facturatie.incoming"
INVOICE_ID = "184"
IDENTITY_UUID = str(uuid.uuid4())


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    msg_id = str(uuid.uuid4())
    xml = (
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
        f"    <invoice_id>{INVOICE_ID}</invoice_id>\n"
        f"    <identity_uuid>{IDENTITY_UUID}</identity_uuid>\n"
        "    <reason>Test scenario 3 — annuleer onbetaalde registratie</reason>\n"
        "  </body>\n"
        "</message>"
    )

    print("=" * 60)
    print("Scenario 3: unpaid registration invoice cancelled")
    print(f"  invoice_id:    {INVOICE_ID}  (Inschrijvingskosten — unpaid)")
    print(f"  identity_uuid: {IDENTITY_UUID}")
    print(f"  message_id:    {msg_id}")
    print("=" * 60)
    send_message(xml, routing_key=QUEUE)
    print("[OK] Sent to", QUEUE)
    print()
    print("Expected in listener (crm.incoming):")
    print("  type=invoice_cancelled  (confirmation)")
    print("  type=invoice_status, status=cancelled")
    print()
    print("Expected in FossBilling:")
    print("  Invoice 184 → status = cancelled")
