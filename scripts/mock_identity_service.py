"""
Mock identity-service voor lokaal testen.
Luistert op de RabbitMQ queue 'identity.user.create.request' en beantwoordt
RPC-verzoeken met een nep master_uuid — exact hetzelfde protocol als de echte service.

Draai dit in een APARTE terminal voor je de receiver opstart:
    python -m scripts.mock_identity_service

Volgorde:
    Terminal 1: python -m scripts.mock_identity_service
    Terminal 2: python -m src.services.rabbitmq_receiver
    Terminal 3: python -m scripts.send_test_registration
"""
import uuid
import xml.etree.ElementTree as ET
import pika
from dotenv import load_dotenv

load_dotenv()

from src.services.rabbitmq_utils import get_connection

REQUEST_QUEUE = "identity.user.create.request"


def handle_request(channel, method, props, body):
    """
    Verwerkt een inkomend identity RPC-verzoek en stuurt een XML-antwoord
    terug op de reply_to queue met hetzelfde correlation_id.
    """
    try:
        root = ET.fromstring(body.decode("utf-8"))
        email = root.findtext("email") or "onbekend"
    except Exception:
        email = "onbekend"

    # Deterministisch: zelfde email → zelfde uuid, handig voor herhaalde tests
    fake_master_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))

    # XML-antwoord conform het protocol van de echte identity-service
    response_root = ET.Element("identity_response")
    ET.SubElement(response_root, "status").text = "ok"
    user_el = ET.SubElement(response_root, "user")
    ET.SubElement(user_el, "master_uuid").text = fake_master_uuid
    ET.SubElement(user_el, "email").text = email

    response_xml = ET.tostring(response_root, encoding="unicode")

    channel.basic_publish(
        exchange="",
        routing_key=props.reply_to,
        body=response_xml.encode("utf-8"),
        properties=pika.BasicProperties(
            correlation_id=props.correlation_id,
            content_type="application/xml",
        ),
    )
    channel.basic_ack(delivery_tag=method.delivery_tag)

    print(f"[MOCK] '{email}' → master_uuid: {fake_master_uuid}")


def start_mock():
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=REQUEST_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=REQUEST_QUEUE, on_message_callback=handle_request)

    print(f"[MOCK] Identity-service luistert op queue '{REQUEST_QUEUE}'")
    print("[MOCK] Stoppen: CTRL+C\n")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[MOCK] Gestopt.")
    finally:
        if connection.is_open:
            connection.close()


if __name__ == "__main__":
    start_mock()
