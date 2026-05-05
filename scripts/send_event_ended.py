"""
Sends an event_ended message to the RabbitMQ incoming queue.
This triggers the facturatie service to consolidate all pending
consumptions into one invoice per company in FossBilling.

Run:
    python scripts/send_event_ended.py
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message  # noqa: E402

QUEUE = "crm.to.facturatie"

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>{uuid.uuid4()}</message_id>
    <version>2.0</version>
    <type>event_ended</type>
    <timestamp>{ts}</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <session_id>SESSION-TEST-001</session_id>
    <ended_at>{ts}</ended_at>
  </body>
</message>"""

send_message(xml, routing_key=QUEUE)
print("Sent event_ended — facturatie service will now create invoices.")
