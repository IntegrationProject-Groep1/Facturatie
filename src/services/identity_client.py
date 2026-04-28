import uuid
import xml.etree.ElementTree as ET
from defusedxml.ElementTree import fromstring as defused_fromstring
from src.services.rabbitmq_utils import get_connection
import pika

REQUEST_QUEUE = "identity.user.create.request"


def request_master_uuid(email: str) -> str:
    """
    Sends an RPC request to the identity-service to get or create a master UUID.
    Returns the master_uuid string.
    """
    connection = get_connection()
    channel = connection.channel()

    try:
        # Declare a temporary reply queue
        result = channel.queue_declare(queue="", exclusive=True)
        reply_queue = result.method.queue

        correlation_id = str(uuid.uuid4())

        # Build XML request
        root = ET.Element("identity_request")
        ET.SubElement(root, "email").text = email.strip().lower()
        ET.SubElement(root, "source_system").text = "facturatie"
        xml_message = ET.tostring(root, encoding="unicode")

        # Send request
        channel.basic_publish(
            exchange="",
            routing_key=REQUEST_QUEUE,
            body=xml_message.encode("utf-8"),
            properties=pika.BasicProperties(
                reply_to=reply_queue,
                correlation_id=correlation_id,
                content_type="application/xml",
            )
        )

        # Wait for response (timeout after 5 seconds)
        master_uuid = None
        for method_frame, props, body in channel.consume(reply_queue, inactivity_timeout=5):
            if method_frame is None:
                raise TimeoutError("No response from identity-service within 5 seconds")

            if props.correlation_id == correlation_id:
                response_root = defused_fromstring(body.decode("utf-8"))
                status = response_root.findtext("status")
                if status != "ok":
                    raise Exception(f"identity-service returned status '{status}'")
                master_uuid = response_root.findtext("user/master_uuid")
                channel.basic_ack(method_frame.delivery_tag)
                break

    finally:
        if connection and connection.is_open:
            connection.close()

    return master_uuid
