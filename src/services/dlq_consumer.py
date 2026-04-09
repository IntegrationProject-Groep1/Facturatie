import logging
import os
import defusedxml.ElementTree as ET

import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv

from src.services.rabbitmq_utils import get_connection

load_dotenv()

logger = logging.getLogger(__name__)


def process_dlq_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    properties: pika.spec.BasicProperties,
    body: bytes,
) -> None:
    logger.info("[DLQ] Dead-letter message received")

    # --- Step 2: read x-death / errors metadata ---
    headers = properties.headers or {}

    # This project forwards messages to the DLQ via basic_publish (see send_to_dlq),
    # attaching an 'errors' header — not via a native RabbitMQ dead-letter exchange.
    errors_header: str = headers.get("errors", "")

    # Native RabbitMQ x-death metadata (present when a DLX is configured)
    x_death = headers.get("x-death")
    original_queue: str = "unknown"
    if x_death and isinstance(x_death, list) and x_death:
        entry = x_death[0]
        original_queue = entry.get("queue", "unknown")

    # --- Step 2: parse XML (best-effort) ---
    msg_type: str = "unknown"
    msg_id: str = "unknown"
    correlation_id: str = "unknown"
    xml_error: str | None = None

    try:
        xml_str = body.decode("utf-8")
        root = ET.fromstring(xml_str)
        msg_type = root.findtext("header/type") or "unknown"
        msg_id = root.findtext("header/message_id") or "unknown"
        correlation_id = root.findtext("header/correlation_id") or "unknown"
    except (ET.ParseError, UnicodeDecodeError) as e:
        xml_error = str(e)

    # --- Step 3: log ---
    logger.info("[DLQ] message_type     : %s", msg_type)
    logger.info("[DLQ] message_id       : %s", msg_id)
    logger.info("[DLQ] correlation_id   : %s", correlation_id)
    logger.info("[DLQ] original_queue   : %s", original_queue)

    if xml_error:
        logger.warning("[DLQ] xml_parse_error  : %s", xml_error)
    if errors_header:
        logger.warning("[DLQ] rejection_errors : %s", errors_header)

    # --- Step 4: alert monitoring ---
    logger.error(
        "[ALERT][DLQ] Unprocessed message | queue=%s | type=%s | message_id=%s | reason=%s",
        original_queue,
        msg_type,
        msg_id,
        errors_header or xml_error or "unknown",
    )

    # --- Step 5: ack — drain the queue ---
    channel.basic_ack(delivery_tag=method.delivery_tag)
    logger.info("[DLQ] Message acknowledged (ack)")


def start_dlq_consumer(queue: str | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if queue is None:
        queue = os.getenv("QUEUE_DLQ", "facturatie.dlq")

    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_dlq_message)

    logger.info("[DLQ] Listening on queue '%s'... (CTRL+C to stop)", queue)

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("[DLQ] Consumer stopped.")
    finally:
        connection.close()


if __name__ == "__main__":
    start_dlq_consumer()
