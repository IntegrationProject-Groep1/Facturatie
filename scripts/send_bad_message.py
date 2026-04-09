"""
Sends an invalid XML message to crm.to.facturatie to trigger the DLQ consumer.
Usage: python scripts/send_bad_message.py
"""
import pika
import os
from dotenv import load_dotenv

load_dotenv()

creds = pika.PlainCredentials(
    os.getenv("RABBITMQ_USER"),
    os.getenv("RABBITMQ_PASSWORD")
)
params = pika.ConnectionParameters(
    host=os.getenv("RABBITMQ_HOST"),
    port=int(os.getenv("RABBITMQ_PORT", 5672)),
    virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
    credentials=creds
)

conn = pika.BlockingConnection(params)
ch = conn.channel()
queue = os.getenv("QUEUE_INCOMING", "facturatie.incoming")
ch.queue_declare(queue=queue, durable=True)
ch.basic_publish(
    exchange="",
    routing_key=queue,
    body=b"<<this is not valid xml>>",
    properties=pika.BasicProperties(delivery_mode=2)
)
print(f"Bad message sent to {queue} — check the DLQ consumer output.")
conn.close()
