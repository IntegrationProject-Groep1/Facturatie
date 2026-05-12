"""
Shared RabbitMQ utilities and patterns.

This module provides centralized functions for:
- Creating RabbitMQ connections
- Reconnecting with exponential backoff
- Forwarding messages to Dead Letter Queue
- Shared validation patterns
"""
import logging
import os
import re
import time
import pika
import pika.channel
from dotenv import load_dotenv

load_dotenv()

__all__ = ["get_connection", "get_connection_with_retry", "send_to_dlq", "ISO8601_UTC_PATTERN"]

# ISO-8601 UTC pattern: YYYY-MM-DDTHH:MM:SSZ
ISO8601_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_RECONNECT_DELAYS = [2, 4, 8, 16, 32]


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
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=30,
    )

    return pika.BlockingConnection(parameters)


def get_connection_with_retry(max_attempts: int = 5) -> pika.BlockingConnection:
    """
    Creates a RabbitMQ connection with exponential backoff on failure.
    Retries up to max_attempts times before raising the last exception.

    Args:
        max_attempts: Maximum number of connection attempts (default: 5)

    Returns:
        pika.BlockingConnection: Active connection to RabbitMQ

    Raises:
        Exception: If all attempts fail
    """
    last_error = None
    delays = (_RECONNECT_DELAYS + [_RECONNECT_DELAYS[-1]] * max_attempts)[:max_attempts]
    for attempt, delay in enumerate(delays, start=1):
        try:
            connection = get_connection()
            if attempt > 1:
                logging.info("[RABBITMQ] Connected on attempt %d", attempt)
            return connection
        except Exception as e:
            last_error = e
            logging.warning(
                "[RABBITMQ] Connection attempt %d/%d failed: %s — retrying in %ds",
                attempt, max_attempts, e, delay,
            )
            time.sleep(delay)

    raise Exception(f"[RABBITMQ] Could not connect after {max_attempts} attempts: {last_error}")


def send_to_dlq(
    channel: pika.channel.Channel,
    body: bytes,
    errors: list[str],
    dlq_name: str = os.getenv("QUEUE_DLQ", "errors.facturatie")
) -> None:
    """
    Forwards an invalid or failed message to the Dead Letter Queue.

    Args:
        channel: RabbitMQ channel
        body: Message body (bytes)
        errors: List of error messages explaining why the message was rejected
        dlq_name: DLQ queue name (default will be overridden by QUEUE_DLQ env var if set)
    """

    channel.queue_declare(queue=dlq_name, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=dlq_name,
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/xml",
            headers={"errors": "; ".join(errors)},
        )
    )

    logging.info("[DLQ] Message forwarded to DLQ. Errors: %s", errors)
