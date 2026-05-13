import uuid
import time
from src.services.rabbitmq_sender import send_message

QUEUE = "facturatie.incoming"
COMPANY_ID = "COMP-TEST-001"
ORDER_ID = str(uuid.uuid4())


def send_consumption_order():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
    <header>
        <message_id>{uuid.uuid4()}</message_id>
        <timestamp>2026-05-10T10:00:00Z</timestamp>
        <source>kassa</source>
        <type>consumption_order</type>
        <version>2.0</version>
        <correlation_id>{ORDER_ID}</correlation_id>
    </header>
    <body>
        <customer>
            <id>{COMPANY_ID}</id>
            <identity_uuid>{uuid.uuid4()}</identity_uuid>
            <type>company</type>
            <email>test-bedrijf@example.com</email>
        </customer>
        <items>
            <item>
                <id>ITEM-001</id>
                <sku>DUVEL-33CL</sku>
                <description>Duvel</description>
                <quantity>3</quantity>
                <unit_price currency="eur">4.50</unit_price>
                <vat_rate>21</vat_rate>
                <total_amount currency="eur">13.50</total_amount>
            </item>
            <item>
                <id>ITEM-002</id>
                <sku>BITTER-PORTIE</sku>
                <description>Bitterballen</description>
                <quantity>1</quantity>
                <unit_price currency="eur">8.00</unit_price>
                <vat_rate>21</vat_rate>
                <total_amount currency="eur">8.00</total_amount>
            </item>
        </items>
    </body>
</message>"""
    send_message(xml, routing_key=QUEUE)
    print(f"[1/2] consumption_order sent | company={COMPANY_ID} | order_id={ORDER_ID}")


def send_event_ended():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
    <header>
        <message_id>{uuid.uuid4()}</message_id>
        <timestamp>2026-05-10T10:05:00Z</timestamp>
        <source>frontend</source>
        <type>event_ended</type>
        <version>2.0</version>
    </header>
    <body>
        <session_id>SESSION-TEST-001</session_id>
        <ended_at>2026-05-10T10:05:00Z</ended_at>
    </body>
</message>"""
    send_message(xml, routing_key=QUEUE)
    print("[2/2] event_ended sent")


if __name__ == "__main__":
    send_consumption_order()
    time.sleep(2)
    send_event_ended()
    print("\nDone. Check FossBilling at https://facturatie.desiderius.me/admin")
    print(f"Search for client: {COMPANY_ID} — invoice should be visible under Invoices")
