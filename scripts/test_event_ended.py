import uuid
import time
from src.services import consumption_store
from src.services.rabbitmq_sender import send_message, build_invoice_created_notification_xml

# Configuratie
TEST_COMPANY_ID = "COMP-999"
TEST_EMAIL = "test-klant@example.com"
TEST_QUEUE = "facturatie.incoming"

def simulate_event_flow():
    print("🚀 Start Test Flow: Event Ended & Invoicing")

    # 1. Database voorbereiden: Voeg wat 'consumpties' toe
    print("--- Stap 1: Consumpties toevoegen aan de database ---")
    items = [
        {"description": "Duvel", "price": 4.50, "quantity": 3, "vat_rate": "21"},
        {"description": "Bitterballen (portie)", "price": 8.00, "quantity": 1, "vat_rate": "21"}
    ]
    
    consumption_store.save_items(
        company_id=TEST_COMPANY_ID,
        badge_id="BADGE-001",
        master_uuid=str(uuid.uuid4()),
        items=items,
        email=TEST_EMAIL,
        company_name="Test Bedrijf NV"
    )
    print(f"✅ Items opgeslagen voor {TEST_COMPANY_ID}")

    # 2. Trigger het 'event_ended' bericht
    print("\n--- Stap 2: 'event_ended' bericht sturen naar RabbitMQ ---")
    event_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
    <header>
        <message_id>{uuid.uuid4()}</message_id>
        <version>2.0</version>
        <type>event_ended</type>
        <timestamp>2024-01-01T12:00:00Z</timestamp>
        <source>kassa_systeem</source>
    </header>
    <body>
        <session_id>SESSION-123</session_id>
        <ended_at>2024-01-01T12:00:00Z</ended_at>
    </body>
</message>"""

    send_message(event_xml, routing_key=TEST_QUEUE)
    print("✅ event_ended bericht verzonden.")

    print("\n💡 Controleer nu de logs van je rabbitmq_receiver.py!")
    print("Als alles goed gaat, zie je dat de receiver:")
    print(f"  1. De items voor {TEST_COMPANY_ID} ophaalt.")
    print("  2. FossBilling aanroept (of een error geeft als de API niet draait).")
    print("  3. Een bericht stuurt naar 'crm.to.mailing'.")

def simulate_new_registration():
    print("\n🚀 Start Test Flow: New Registration")
    
    reg_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
    <header>
        <message_id>{uuid.uuid4()}</message_id>
        <version>2.0</version>
        <type>new_registration</type>
        <timestamp>2024-01-01T12:00:00Z</timestamp>
        <source>crm_system</source>
    </header>
    <body>
        <customer>
            <customer_id>CUST-456</customer_id>
            <email>{TEST_EMAIL}</email>
            <contact>
                <first_name>Jan</first_name>
                <last_name>Test</last_name>
            </contact>
            <address>
                <street>Kerkstraat</street>
                <number>10</number>
                <postal_code>1000</postal_code>
                <city>Brussel</city>
                <country>BE</country>
            </address>
        </customer>
        <registration_fee currency="eur">50.00</registration_fee>
    </body>
</message>"""

    send_message(reg_xml, routing_key=TEST_QUEUE)
    print("✅ new_registration bericht verzonden.")

if __name__ == "__main__":
    # Zorg dat de database tabellen bestaan
    consumption_store.init_db()
    
    # Kies welke flow je wilt testen
    simulate_event_flow()
    # simulate_new_registration()