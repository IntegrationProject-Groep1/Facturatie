import pika
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
import os

# Load environment variables from the .env file
load_dotenv()


def get_connection() -> pika.BlockingConnection:
    # Build credentials using the RabbitMQ username and password from environment variables
    credintials = pika.PlainCredentials(
        os.getenv('RABBITMQ_USER'),
        os.getenv('RABBITMQ_PASSWORD')
    )
    # Set up connection parameters with host, port, and credentials
    parameters = pika.ConnectionParameters(
        host=os.getenv('RABBITMQ_HOST'),
        port=int(os.getenv('RABBITMQ_PORT', 5672)),  # pika expects an integer port
        credentials=credintials
    )
    # Open and return a blocking connection to the RabbitMQ broker
    return pika.BlockingConnection(parameters)


def build_consumption_order_xml(
    customer_id: str,
    items: list[dict],
    is_company_linked: bool = False,
    company_id: str = "",
    company_name: str = "",
) -> str:
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build optional company fields — only included when is_company_linked is True
    company_linked_str = "true" if is_company_linked else "false"
    company_fields = ""
    if is_company_linked:
        company_fields = f"""
            <company_id>{company_id}</company_id>
            <company_name>{company_name}</company_name>"""

    # Build the XML for each item in the order
    # unit_price uses lowercase currency attribute per XML Naming Standard
    items_xml = ""
    for item in items:
        items_xml += f"""
        <item>
            <id>{item['id']}</id>
            <description>{item['description']}</description>
            <quantity>{item['quantity']}</quantity>
            <unit_price currency="eur">{item['unit_price']}</unit_price>
            <vat_rate>{item['vat_rate']}</vat_rate>
        </item>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
    <header>
        <message_id>{message_id}</message_id>
        <version>2.0</version>
        <type>consumption_order</type>
        <timestamp>{timestamp}</timestamp>
        <source>kassa_bar_01</source>
    </header>
    <body>
        <customer>
            <id>{customer_id}</id>
            <is_company_linked>{company_linked_str}</is_company_linked>{company_fields}
        </customer>
        <items>{items_xml}
        </items>
    </body>
</message>"""


def send_message(xml_message: str, routing_key: str = "facturatie") -> None:
    # Open a connection and declare the queue as durable (survives RabbitMQ restarts)
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=routing_key, durable=True)
    # Publish the message with delivery_mode=2 so it is persisted to disk
    channel.basic_publish(
        exchange="",
        routing_key=routing_key,
        body=xml_message.encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml"
        )
    )
    print(f"[SENDER] Message sent to queue '{routing_key}'")
    connection.close()


if __name__ == "__main__":
    items = [
        {
            "id": "BEV-001",
            "description": "Coffee",
            "quantity": 2,
            "unit_price": "2.50",
            "vat_rate": 21
        }
    ]
    xml = build_consumption_order_xml(
        customer_id="12345",
        items=items,
        is_company_linked=True,
        company_id="FOSS-CUST-102",
        company_name="Bedrijf NV",
    )
    print("[SENDER] XML:\n", xml)
    send_message(xml)