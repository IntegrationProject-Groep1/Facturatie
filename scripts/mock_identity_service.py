"""
Mock identity service for local testing.
Listens on the RabbitMQ queue 'identity.user.create.request' and responds to
RPC requests with a fake master_uuid — exactly the same protocol as the real service.

Run this in a SEPARATE terminal before starting the receiver:
    python -m scripts.mock_identity_service

Order:
    Terminal 1: python -m scripts.mock_identity_service
    Terminal 2: python -m src.services.rabbitmq_receiver
    Terminal 3: python -m scripts.send_test_registration
"""
import uuid
import xml.etree.ElementTree as ET
import pika
from dotenv import load_dotenv
from src.services.rabbitmq_utils import get_connection

load_dotenv()

REQUEST_QUEUE = "identity.user.create.request"


def handle_request(channel, method, props, body):
    """
    Processes an incoming identity RPC request and sends an XML response
    back on the reply_to queue with the same correlation_id.
    """
    try:
        root = ET.fromstring(body.decode("utf-8"))
        email = root.findtext("email") or "unknown"
    except Exception:
        email = "unknown"

    # Deterministic: same email → same uuid, useful for repeated tests
    fake_master_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))

    # XML response conforming to the protocol of the real identity service
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

    print(f"[MOCK] Identity service listening on queue '{REQUEST_QUEUE}'")
    print("[MOCK] Stop: CTRL+C\n")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[MOCK] Stopped.")
    finally:
        if connection.is_open:
            connection.close()


if __name__ == "__main__":
    start_mock()
