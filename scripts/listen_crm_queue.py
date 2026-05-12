"""
Listener script for the crm.incoming queue.

Prints every message that arrives — useful for verifying that outgoing
invoice_status and payment_registered messages are sent correctly.

Run in a separate terminal before sending a trigger message:
    python -m scripts.listen_crm_queue

Then in another terminal send a trigger, e.g.:
    python -m scripts.send_test_registration
    python -m scripts.send_test_payment

Press CTRL+C to stop.
"""
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from src.services.rabbitmq_utils import get_connection

load_dotenv()

QUEUE = "crm.incoming"


def handle(channel, method, props, body):
    print("\n" + "=" * 60)
    try:
        root = ET.fromstring(body.decode("utf-8"))
        msg_type = root.findtext("header/type") or "unknown"
        msg_id = root.findtext("header/message_id") or "-"
        source = root.findtext("header/source") or "-"
        correlation_id = root.findtext("header/correlation_id") or "-"

        print(f"[LISTENER] type={msg_type} | source={source}")
        print(f"           message_id={msg_id}")
        print(f"           correlation_id={correlation_id}")

        if msg_type == "invoice_status":
            print(f"           invoice_id={root.findtext('body/invoice_id')}")
            print(f"           identity_uuid={root.findtext('body/identity_uuid')}")
            print(f"           status={root.findtext('body/status')}")
            amount_el = root.find("body/amount")
            if amount_el is not None:
                print(f"           amount={amount_el.text} {amount_el.get('currency', '')}")

        elif msg_type == "payment_registered":
            print(f"           identity_uuid={root.findtext('body/identity_uuid')}")
            invoice_el = root.find("body/invoice")
            if invoice_el is not None:
                print(f"           invoice_id={invoice_el.findtext('id')}")
                print(f"           status={invoice_el.findtext('status')}")
                amount_el = invoice_el.find("amount_paid")
                if amount_el is not None:
                    print(f"           amount={amount_el.text} {amount_el.get('currency', '')}")

        elif msg_type == "invoice_cancelled":
            print(f"           invoice_id={root.findtext('body/invoice_id')}")
            print(f"           customer_id={root.findtext('body/customer_id')}")

        else:
            ET.indent(root, space="  ")
            print(ET.tostring(root, encoding="unicode"))

    except Exception as e:
        print(f"[LISTENER] Could not parse message: {e}")
        print(f"           Raw: {body[:200]}")

    print("=" * 60)
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main():
    connection = get_connection()
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE, passive=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=QUEUE, on_message_callback=handle)

    print(f"[LISTENER] Listening on '{QUEUE}' — press CTRL+C to stop\n")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[LISTENER] Stopped.")
    finally:
        if connection.is_open:
            connection.close()


if __name__ == "__main__":
    main()
