import logging
import pika
import pika.channel
import pika.spec
from dotenv import load_dotenv
import os
import xml.etree.ElementTree as ET
from defusedxml.ElementTree import fromstring as defused_fromstring
from datetime import datetime, timezone

from .fossbilling_api import create_registration_invoice, pay_invoice
from .rabbitmq_sender import build_invoice_created_notification_xml, build_payment_confirmed_xml, send_message
from src.utils.xml_validator import validate_xml
from src.services.rabbitmq_utils import (
    get_connection, send_to_dlq
)
from src.services import fossbilling_api as fossbilling_client, crm_publisher
from src.services.identity_client import request_master_uuid
from src.services import consumption_store

# Valid values per XML Naming Standard (all lowercase snake_case)
VALID_TYPES: set[str] = {
    "payment_registered", "heartbeat", "new_registration",
    "invoice_request", "invoice_cancelled", "event_ended"
}
VALID_VAT_RATES: set[str] = {"6", "12", "21"}
VALID_PAYMENT_METHODS: set[str] = {"company_link", "on_site", "online"}

# In-memory set for duplicate detection based on header/message_id
# Note: persists only during runtime; will be migrated to MySQL in a later sprint.
seen_message_ids: set[str] = set()

load_dotenv()


def is_duplicate(msg_id: str, seen_ids: set[str]) -> bool:
    """Returns True if the message_id has already been processed."""
    return msg_id in seen_ids


def validate_invoice_cancelled(root: ET.Element) -> list[str]:
    """
    Validates an invoice_cancelled XML message.
    """
    errors: list[str] = []
    version = root.findtext("header/version")

    if not version or version != "2.0":
        errors.append(
            f"ERROR: invalid or missing version (expected 2.0, got '{version}')"
        )
    return errors


def extract_customer_data(root: ET.Element) -> dict:
    """
    Extracts customer and registration data from a new_registration XML message.
    """
    fee_el = root.find("body/registration_fee")
    return {
        "customer_id": root.findtext("body/customer/customer_id") or "",
        "email": root.findtext("body/customer/email"),
        "first_name": root.findtext("body/customer/contact/first_name") or "",
        "last_name": root.findtext("body/customer/contact/last_name") or "",
        "company_name": root.findtext("body/customer/company_name") or "",
        "address": {
            field: root.findtext(f"body/customer/address/{field}") or ""
            for field in ["street", "number", "postal_code", "city", "country"]
        },
        "registration_fee": root.findtext("body/registration_fee"),
        "fee_currency": fee_el.get("currency", "eur") if fee_el is not None else "eur",
    }


def extract_invoice_request_data(root: ET.Element) -> dict:
    """
    Extracts billing data from an invoice_request XML message (CRM → Facturatie).
    """
    invoice_data_el = root.find("body/invoice_data")
    address = {}
    if invoice_data_el is not None:
        addr_el = invoice_data_el.find("address")
        if addr_el is not None:
            address = {
                field: addr_el.findtext(field) or ""
                for field in ["street", "number", "postal_code", "city", "country"]
            }

    return {
        "user_id": root.findtext("body/user_id") or "",
        "correlation_id": root.findtext("header/correlation_id") or "",
        "customer": {
            # Namen zitten direct in <invoice_data> — geen <contact> wrapper (§11.1 uitzondering)
            "first_name": root.findtext("body/invoice_data/first_name") or "",
            "last_name": root.findtext("body/invoice_data/last_name") or "",
            "email": root.findtext("body/invoice_data/email") or "",
            "company_name": root.findtext("body/invoice_data/company_name") or "",
            "vat_number": root.findtext("body/invoice_data/vat_number") or "",
            "address": address,
        },
    }


def process_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    _properties: pika.spec.BasicProperties,
    body: bytes
) -> None:
    print("\n[RECEIVER] Message received")

    # Step 1: parse XML — catch both invalid XML and bad encodings
    try:
        xml_str = body.decode("utf-8")
        root = defused_fromstring(xml_str)
    except (ET.ParseError, UnicodeDecodeError) as e:
        print(f"[RECEIVER] ERROR: Invalid XML or encoding — {e}")
        send_to_dlq(channel, body, [f"ERROR: invalid_xml: {e}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 2: duplicate detection based on header/message_id
    msg_id = root.findtext("header/message_id")
    if msg_id and is_duplicate(msg_id, seen_message_ids):
        print(f"[RECEIVER] WARN: duplicate_message_id: '{msg_id}' — ignored")
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 3: validate message structure
    msg_type = root.findtext("header/type") or "unknown"
    is_valid, error_msg = validate_xml(xml_str, msg_type)

    if not is_valid:
        print(f"[RECEIVER] ERROR: xsd_validation_failed — {error_msg}")
        send_to_dlq(channel, body, [f"ERROR: xsd_validation: {error_msg}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 4: mark message_id as seen
    if msg_id:
        seen_message_ids.add(msg_id)

    print(
        f"[RECEIVER] Valid message received"
        f" | type={msg_type} | message_id={msg_id}"
    )

    # Process new customer registration
    if msg_type == "new_registration":
        customer_data = extract_customer_data(root)

        # Request master UUID from identity-service
        try:
            master_uuid = request_master_uuid(customer_data["email"])
            print(f"[RECEIVER] master_uuid received | email={customer_data['email']} | master_uuid={master_uuid}")
        except Exception as e:
            print(f"[RECEIVER] ERROR: master_uuid request failed — {e}")
            send_to_dlq(channel, body, [f"ERROR: identity_service_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            # Create registration invoice in FossBilling
            invoice_id = create_registration_invoice(customer_data)
        except Exception as e:
            # Handle failure and move to Dead Letter Queue
            send_to_dlq(channel, body, [f"ERROR: fossbilling_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        notification_xml = build_invoice_created_notification_xml(
            invoice_id=invoice_id,
            recipient_email=customer_data["email"],
            correlation_id=msg_id,
            first_name=customer_data.get("first_name", ""),
            last_name=customer_data.get("last_name", ""),
            customer_id=customer_data.get("customer_id", ""),
        )

        send_message(
            notification_xml,
            routing_key="crm.to.mailing",
            channel=channel
        )

        print(
            f"[RECEIVER] send_mailing sent | invoice_id={invoice_id}"
            f" | correlation_id={msg_id}"
        )

        channel.basic_ack(delivery_tag=method.delivery_tag)

    elif msg_type == "invoice_request":
        data = extract_invoice_request_data(root)
        user_id = data["user_id"]
        correlation_id = data["correlation_id"]
        customer = data["customer"]
        company_name = customer["company_name"]
        if not company_name:
            send_to_dlq(channel, body, ["ERROR: invoice_request requires company_name in invoice_data"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # Facturatie slaat de facturatiegegevens op; items komen via consumption_order (passthrough)
        try:
            consumption_store.save_invoice_request(
                user_id=user_id,
                correlation_id=correlation_id,
                customer=customer,
            )
            logging.info(
                "[RECEIVER] invoice_request saved | user_id=%s | correlation_id=%s",
                user_id, correlation_id,
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logging.error("[RECEIVER] ERROR: invoice_request_save_failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: invoice_request_save_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    elif msg_type == "event_ended":
        session_id = root.findtext("body/session_id") or ""
        logging.info("[RECEIVER] event_ended | session_id=%s", session_id)

        try:
            company_ids = consumption_store.get_pending_company_ids()
            if not company_ids:
                logging.info("[RECEIVER] event_ended: no pending consumptions")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            errors = []
            for company_id in company_ids:
                try:
                    items, row_ids = consumption_store.get_items_for_company(company_id)
                    if not items:
                        continue
                    meta = consumption_store.get_company_meta(company_id)
                    invoice_id = fossbilling_client.process_consumption_order(company_id, items)

                    consumption_store.clear_by_ids(row_ids)

                    try:
                        notification_xml = build_invoice_created_notification_xml(
                            invoice_id=invoice_id,
                            recipient_email=meta["email"],
                            correlation_id=msg_id,
                            first_name=meta.get("first_name", ""),
                            last_name=meta.get("last_name", ""),
                            customer_id=meta.get("customer_id", ""),
                            subject=f"Uw factuur {invoice_id} staat klaar",
                        )
                        send_message(notification_xml, routing_key="crm.to.mailing", channel=channel)
                    except Exception as mail_err:
                        # We loggen de mail fout, maar gaan door (de factuur is immers al klaar)
                        logging.warning("[RECEIVER] Invoice created but mail failed for %s: %s", company_id, mail_err)

                    logging.info(
                        "[RECEIVER] event_ended: invoice processed | company_id=%s | invoice_id=%s",
                        company_id, invoice_id,
                    )
                except Exception as e:
                    logging.error("[RECEIVER] event_ended: failed for company_id=%s: %s", company_id, e)
                    errors.append(f"company_id={company_id}: {e}")

            if errors:
                send_to_dlq(channel, body, [f"ERROR: event_ended partial failure: {'; '.join(errors)}"])
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            else:
                channel.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            logging.error("[RECEIVER] ERROR: event_ended_failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: event_ended_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    elif msg_type == "payment_registered":
        print("[RECEIVER] Handling payment_registered")

        try:
            # Extract invoice info — inkomend formaat (Kassa→Facturatie via CRM passthrough)
            invoice_el = root.find("body/invoice")
            if invoice_el is None:
                raise ValueError("Missing <invoice> element")

            invoice_id = invoice_el.findtext("id")
            if not invoice_id:
                raise ValueError("Missing invoice id in <invoice><id>")

            amount_el = invoice_el.find("amount_paid")
            amount = amount_el.text if amount_el is not None else None
            currency = amount_el.get("currency", "eur") if amount_el is not None else "eur"

            # Extract transaction info
            transaction_el = root.find("body/transaction")
            if transaction_el is None:
                raise ValueError("Missing <transaction> element")

            payment_method = transaction_el.findtext("payment_method") or ""
            transaction_id = transaction_el.findtext("id") or ""

            user_id = root.findtext("body/user_id") or ""

            print(
                f"[RECEIVER] Payment data extracted"
                f" | invoice_id={invoice_id} | amount={amount} {currency}"
                f" | method={payment_method} | transaction_id={transaction_id}"
            )

            success = pay_invoice(invoice_id, amount)
            if not success:
                raise Exception(f"Failed to register payment for invoice '{invoice_id}'")

            print(f"[RECEIVER] Payment registered in FossBilling | invoice_id={invoice_id}")

            # Stuur bevestiging naar CRM — outgoing formaat (Facturatie→CRM)
            paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            confirmation_xml = build_payment_confirmed_xml(
                invoice_id=invoice_id,
                customer_id=user_id,
                amount=amount,
                currency=currency,
                payment_method=payment_method,
                correlation_id=msg_id,
                paid_at=paid_at,
            )
            send_message(
                confirmation_xml,
                routing_key="facturatie.to.crm",
                channel=channel,
            )
            print(
                f"[RECEIVER] payment_registered confirmation sent"
                f" | invoice_id={invoice_id} | correlation_id={msg_id}"
            )

            channel.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            print(f"[RECEIVER] ERROR: payment_registered_failed: {e}")
            send_to_dlq(channel, body, [f"ERROR: payment_registered_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    elif msg_type == "invoice_cancelled":
        print(f"[RECEIVER][{msg_type}] Handling cancellation")

        errors = validate_invoice_cancelled(root)
        if errors:
            send_to_dlq(channel, body, errors)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # invoice_cancelled body heeft <invoice_number> en optioneel <reason>
        invoice_id = root.findtext("body/invoice_number")
        customer_id = root.findtext("header/message_id")  # geen customer_id in dit schema
        correlation_id = root.findtext("header/correlation_id")

        if not invoice_id:
            send_to_dlq(channel, body, ["ERROR: missing invoice_number in invoice_cancelled message"])
            crm_publisher.publish_cancellation_failed(
                invoice_id="unknown",
                customer_id=customer_id,
                correlation_id=correlation_id,
                reason="missing_invoice_id",
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        print(f"[RECEIVER][{msg_type}] Processing invoice={invoice_id}")

        # Step: check invoice status before cancelling
        try:
            status = fossbilling_client.get_invoice_status(invoice_id)
        except Exception as e:
            error_msg = f"ERROR: FossBilling unreachable during status check: {e}"
            logging.error("[RECEIVER][%s] %s", msg_type, error_msg)
            send_to_dlq(channel, body, [error_msg])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        if status is None:
            logging.warning(
                "[RECEIVER][%s] Invoice '%s' not found in FossBilling", msg_type, invoice_id
            )
            crm_publisher.publish_cancellation_failed(
                invoice_id, customer_id, correlation_id, reason="invoice_not_found"
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        if status in ("paid", "cancelled"):
            reason = "invoice_already_paid" if status == "paid" else "invoice_already_cancelled"
            logging.warning(
                "[RECEIVER][%s] Cancellation blocked — invoice '%s' has status '%s'",
                msg_type, invoice_id, status
            )
            crm_publisher.publish_cancellation_failed(
                invoice_id, customer_id, correlation_id, reason=reason
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        success = fossbilling_client.cancel_invoice(invoice_id)
        if not success:
            error_msg = f"ERROR: FossBilling failed to cancel invoice '{invoice_id}'"
            send_to_dlq(channel, body, [error_msg])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        crm_publisher.publish_invoice_cancelled(invoice_id, customer_id, correlation_id)
        logging.info("[RECEIVER][%s] Flow complete for invoice '%s'", msg_type, invoice_id)
        channel.basic_ack(delivery_tag=method.delivery_tag)

    else:
        print(f"[RECEIVER] No handler for type '{msg_type}' — acknowledging")
        channel.basic_ack(delivery_tag=method.delivery_tag)


def start_receiver(queue: str | None = None) -> None:
    if queue is None:
        queue = os.getenv("QUEUE_INCOMING", "facturatie.incoming")

    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=queue, passive=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue, on_message_callback=process_message)

    print(f"[RECEIVER] Listening on queue '{queue}'... (CTRL+C to stop)")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n[RECEIVER] Stopping consumer...")
    finally:
        connection.close()


if __name__ == "__main__":
    start_receiver()
