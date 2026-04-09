import os
import xml.etree.ElementTree as ET

import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv

from src.services.rabbitmq_utils import get_connection

load_dotenv()


def process_dlq_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    properties: pika.spec.BasicProperties,
    body: bytes,
) -> None:
    print("\n[DLQ] Dead-letter message received")

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
    print(f"[DLQ] message_type     : {msg_type}")
    print(f"[DLQ] message_id       : {msg_id}")
    print(f"[DLQ] correlation_id   : {correlation_id}")
    print(f"[DLQ] original_queue   : {original_queue}")

    if xml_error:
        print(f"[DLQ] xml_parse_error  : {xml_error}")
    if errors_header:
        print(f"[DLQ] rejection_errors : {errors_header}")

    # --- Step 4: alert monitoring ---
    print(
        f"[ALERT][DLQ] Unprocessed message"
        f" | queue={original_queue}"
        f" | type={msg_type}"
        f" | message_id={msg_id}"
        f" | reason={errors_header or xml_error or 'unknown'}"
    )

    # --- Step 5: ack — drain the queue ---
    channel.basic_ack(delivery_tag=method.delivery_tag)
    print("[DLQ] Message acknowledged (ack)")


def start_dlq_consumer(queue: str | None = None) -> None:
    if queue is None:
        queue = os.getenv("QUEUE_DLQ", "facturatie.dlq")

    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_dlq_message)

    print(f"[DLQ] Listening on queue '{queue}'... (CTRL+C to stop)")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[DLQ] Consumer stopped.")
    finally:
        connection.close()


if __name__ == "__main__":
    start_dlq_consumer()
