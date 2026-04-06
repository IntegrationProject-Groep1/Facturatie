"""
Shared RabbitMQ utilities and patterns.

This module provides centralized functions for:
- Creating RabbitMQ connections
- Forwarding messages to Dead Letter Queue
- Shared validation patterns
"""
import os
import re
import pika
import pika.channel
from dotenv import load_dotenv

load_dotenv()

__all__ = ["get_connection", "send_to_dlq", "ISO8601_UTC_PATTERN"]

# ISO-8601 UTC pattern: YYYY-MM-DDTHH:MM:SSZ
ISO8601_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def get_connection() -> pika.BlockingConnection:
    """
    Creates a RabbitMQ connection using environment variables.

    Environment variables:
    - RABBITMQ_USER: RabbitMQ username
    - RABBITMQ_PASSWORD: RabbitMQ password
    - RABBITMQ_HOST: RabbitMQ host
    - RABBITMQ_PORT: RabbitMQ port (default: 5672)
    - RABBITMQ_VHOST: Virtual host (default: /)

    Returns:
        pika.BlockingConnection: Active connection to RabbitMQ
    """
    credentials = pika.PlainCredentials(
        os.getenv("RABBITMQ_USER"),
        os.getenv("RABBITMQ_PASSWORD")
    )
    parameters = pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST"),
        port=int(os.getenv("RABBITMQ_PORT", 5672)),
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
        credentials=credentials
    )
    return pika.BlockingConnection(parameters)


def send_to_dlq(
    channel: pika.channel.Channel,
    body: bytes,
    errors: list[str],
    dlq_name: str = "facturatie.dlq"
) -> None:
    """
    Forwards an invalid or failed message to the Dead Letter Queue.

    Args:
        channel: RabbitMQ channel
        body: Message body (bytes)
        errors: List of error messages explaining why the message was rejected
        dlq_name: DLQ queue name (default will be overridden by QUEUE_DLQ env var if set)
    """
    dlq = os.getenv("QUEUE_DLQ", dlq_name)
    channel.queue_declare(queue=dlq, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=dlq,
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml",
            headers={"errors": "; ".join(errors)},
        )
    )
    print(f"[DLQ] Message forwarded to DLQ. Errors: {errors}")