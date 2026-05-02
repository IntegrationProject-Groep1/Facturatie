import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.rabbitmq_sender import send_message  # noqa: E402

xml = """<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>d34ef56a-78cd-9012-e345-6f78a9b0c123</message_id>
    <version>2.0</version>
    <type>invoice_cancelled</type>
    <timestamp>2026-03-31T10:00:00Z</timestamp>
    <source>frontend_system</source>
    <correlation_id>a23bc45d-89ef-1234-b567-1f03c3d4e580</correlation_id>
  </header>
  <body>
    <invoice_id>4</invoice_id>
    <customer_id>1</customer_id>
    <reason>Test cancellation via script</reason>
  </body>
</message>"""

send_message(xml)
print("Sent!")
