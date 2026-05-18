import logging
import pika
import pika.channel
import pika.spec
import base64
from dotenv import load_dotenv
import os
import xml.etree.ElementTree as ET
from defusedxml.ElementTree import fromstring as defused_fromstring
from datetime import datetime, timezone
import re
from decimal import Decimal

from .fossbilling_api import create_registration_invoice, get_invoice, pay_invoice
from .rabbitmq_sender import (
    build_invoice_created_notification_xml,
    build_payment_confirmed_xml,
    publish_cancellation_failed,
    publish_invoice_cancelled,
    publish_invoice_link,
    publish_invoice_status,
    publish_vat_validation_error,
    send_message,
    send_system_error,
    CRM_QUEUE,
)
from src.utils.xml_validator import validate_xml
from src.services.rabbitmq_utils import (
    get_connection_with_retry, send_to_dlq
)
from src.services.rabbitmq_sender import send_log
from src.services import fossbilling_api as fossbilling_client
from src.services.identity_client import request_master_uuid
from src.services import consumption_store

VALID_TYPES: set[str] = {
    "payment_registered", "heartbeat", "new_registration",
    "invoice_request", "invoice_cancelled", "event_ended",
    "consumption_order"
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
    Conform gedeeld contract: geen registration_fee, geen address, geen is_company_linked.
    """
    amount_el = root.find("body/customer/payment_due/amount")
    return {
        "customer_id": root.findtext("body/customer/user_id") or "",
        "email": root.findtext("body/customer/email"),
        "first_name": root.findtext("body/customer/contact/first_name") or "",
        "last_name": root.findtext("body/customer/contact/last_name") or "",
        "company_name": root.findtext("body/customer/company_name") or "",
        "company_id": root.findtext("body/customer/company_id") or "",
        "address": {
            "street": "", "number": "", "postal_code": "", "city": "", "country": ""
        },
        "registration_fee": amount_el.text if amount_el is not None else "0.00",
        "fee_currency": amount_el.get("currency", "eur") if amount_el is not None else "eur",
        "payment_status": root.findtext("body/customer/payment_due/status") or "unpaid",
        "vat_number": root.findtext("body/customer/vat_number") or "",
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
        "user_id": root.findtext("body/identity_uuid") or "",
        "correlation_id": root.findtext("header/correlation_id") or "",
        "payment_status": root.findtext("body/payment_status") or "pending",
        "payment_method": root.findtext("body/payment_method") or "",
        "customer": {
            "type": root.findtext("body/invoice_data/type")
            or ("company" if root.findtext("body/invoice_data/company_name") else "private"),
            "first_name": root.findtext("body/invoice_data/contact/first_name") or "",
            "last_name": root.findtext("body/invoice_data/contact/last_name") or "",
            "email": root.findtext("body/invoice_data/email") or "",
            "company_name": root.findtext("body/invoice_data/company_name") or "",
            "vat_number": root.findtext("body/invoice_data/vat_number") or "",
            "address": address,
        },
    }


def _is_valid_vat(vat_number: str) -> bool:
    """Validates Belgian VAT number format: BE + 10 digits."""
    if not vat_number:
        return False
    return bool(re.match(r'^BE\d{10}$', vat_number.upper()))


def process_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    _properties: pika.spec.BasicProperties,
    body: bytes
) -> None:
    logging.info("[RECEIVER] Message received")

    # Step 1: parse XML — catch both invalid XML and bad encodings
    try:
        xml_str = body.decode("utf-8")
        root = defused_fromstring(xml_str)
    except (ET.ParseError, UnicodeDecodeError) as e:
        logging.error("[RECEIVER] INVALID XML or encoding: %s", e)
        send_to_dlq(channel, body, [f"ERROR: invalid_xml: {e}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 2: duplicate detection based on header/message_id
    msg_id = root.findtext("header/message_id")
    if msg_id and is_duplicate(msg_id, seen_message_ids):
        logging.warning("[RECEIVER] duplicate_message_id: '%s' — ignored", msg_id)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 3: validate message structure
    msg_type = root.findtext("header/type") or "unknown"
    source = root.findtext("header/source") or ""

    # Use unified payment schema (Contract v2.3-12)
    if msg_type == "payment_registered":
        schema_name = "payment_registered"
    else:
        schema_name = msg_type

    is_valid, error_msg = validate_xml(xml_str, schema_name)

    if not is_valid:
        logging.error("=" * 60)
        logging.error("[RECEIVER] XSD VALIDATION FAILED")
        logging.error("Message Type: %s", msg_type)
        logging.error("Source:       %s", source)
        logging.error("Error:        %s", error_msg)
        logging.error("=" * 60)

        # PROTOCOL: Inbound Message (The "Validator" Log) - Failure
        send_log(
            level="error",
            action="xml_validation",
            message=f"Received {msg_type} from {source}. Validation: Failure. Error: {error_msg}",
            channel=channel
        )

        # Publish official system_error to Monitoring (Contract §2.6)
        send_system_error(
            error_code="XSD_VALIDATION_ERROR",
            message=f"Validation failed for {schema_name}: {error_msg}",
            severity="critical",
            correlation_id=msg_id,
            channel=channel
        )
        send_to_dlq(channel, body, [f"ERROR: xsd_validation: {error_msg}"])
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Step 4: mark message_id as seen
    if msg_id:
        seen_message_ids.add(msg_id)

    # PROTOCOL: Inbound Message (The "Validator" Log) - Success
    send_log(
        level="info",
        action="xml_validation",
        message=f"Received {msg_type} from {source}. Validation: Success.",
        channel=channel
    )

    logging.info(
        "[RECEIVER] Valid message received | type=%s | message_id=%s",
        msg_type, msg_id
    )

    # Process new customer registration
    if msg_type == "new_registration":
        customer_data = extract_customer_data(root)

        vat_number = customer_data.get("vat_number", "")
        if vat_number and not _is_valid_vat(vat_number):
            logging.warning("[RECEIVER] Invalid VAT number: %s", vat_number)
            try:
                publish_vat_validation_error(
                    vat_number=vat_number,
                    identity_uuid=customer_data.get("customer_id", ""),
                    error_message=f"BTW-nummer {vat_number} heeft een ongeldig formaat",
                    correlation_id=msg_id,
                    channel=channel,
                )
            except Exception as vat_err:
                logging.warning("[RECEIVER] Failed to send vat_validation_error: %s", vat_err)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        # Request master UUID from identity-service
        try:
            master_uuid = request_master_uuid(customer_data["email"])
            logging.info(
                "[RECEIVER] master_uuid received | email=%s | master_uuid=%s",
                customer_data['email'], master_uuid
            )
        except Exception as e:
            logging.error("[RECEIVER] master_uuid request failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: identity_service_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            # Create registration invoice in FossBilling
            invoice_id = create_registration_invoice(customer_data)
        except Exception as e:
            send_log("error", "system_error", f"FossBilling failed for new_registration: {e}", channel=channel)
            # Handle failure and move to Dead Letter Queue
            send_to_dlq(channel, body, [f"ERROR: fossbilling_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # Build and send XML for the Mailing Service
        try:
            _invoice_data = get_invoice(invoice_id)
            _pdf_bytes = fossbilling_client.get_invoice_pdf(invoice_id, invoice_hash=(_invoice_data or {}).get("hash"))
        except Exception as e:
            send_to_dlq(channel, body, [f"ERROR: fossbilling_pdf_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        notification_xml = build_invoice_created_notification_xml(
            invoice_id=invoice_id,
            recipient_email=customer_data["email"],
            correlation_id=msg_id,
            first_name=customer_data.get("first_name", ""),
            last_name=customer_data.get("last_name", ""),
            identity_uuid=master_uuid,
            invoice_data=_invoice_data,
            pdf_bytes=_pdf_bytes,
        )

        send_message(
            notification_xml,
            routing_key="facturatie.to.mailing",
            channel=channel
        )

        logging.info(
            "[RECEIVER] send_mailing sent | invoice_id=%s"
            " | correlation_id=%s",
            invoice_id, msg_id
        )

        try:
            publish_invoice_link(invoice_id, master_uuid, channel=channel)
        except Exception as link_err:
            logging.warning("[RECEIVER] Invoice created but invoice_link failed: %s", link_err)
        try:
            publish_invoice_status(
                invoice_id=invoice_id,
                identity_uuid=master_uuid,
                status="sent",
                amount=customer_data.get("registration_fee") or "0.00",
                correlation_id=msg_id,
                channel=channel,
            )
        except Exception as status_err:
            logging.warning("[RECEIVER] invoice_status failed for new_registration: %s", status_err)
        send_log("info", "invoice", f"Registration invoice created: {invoice_id}", channel=channel)

        channel.basic_ack(delivery_tag=method.delivery_tag)

    elif msg_type == "invoice_request":
        data = extract_invoice_request_data(root)
        user_id = data["user_id"]
        correlation_id = data["correlation_id"]
        customer = data["customer"]
        company_name = customer["company_name"]

        customer_type = customer.get("type", "private")
        company_name = customer.get("company_name")
        vat_number = customer.get("vat_number")
        first_name = customer.get("first_name", "Onbekend")
        last_name = customer.get("last_name", "Klant")
        customer_email = customer.get("email")

        if customer_type == "company":
            if not company_name:
                send_to_dlq(channel, body, ["ERROR: invoice_request for company requires company_name"])
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return

            if not vat_number:
                send_to_dlq(channel, body, ["ERROR: invoice_request for company requires vat_number"])
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return

        try:
            consumption_store.update_meta_by_correlation_id(
                correlation_id=correlation_id,
                company_name=company_name,
                email=customer["email"],
            )

            items, row_ids, company_id = consumption_store.get_items_by_correlation_id(correlation_id)
            if not items:
                logging.warning("[RECEIVER] invoice_request: no items found | correlation_id=%s", correlation_id)
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            payment_status = data.get("payment_status", "pending")
            payment_method = data.get("payment_method", "")

            invoice_id = fossbilling_client.process_consumption_order(
                company_id,
                items,
                company_name=company_name,
                first_name=first_name,
                last_name=last_name,
                email=customer_email
            )

            consumption_store.save_invoice_correlation(
                invoice_id=invoice_id,
                correlation_id=msg_id,
                invoice_type="consumption",
            )

            if payment_status == "paid":
                fossbilling_client.mark_invoice_as_paid(invoice_id)
                logging.info("[RECEIVER] Invoice marked as paid | invoice_id=%s", invoice_id)

            consumption_store.clear_by_ids(row_ids)

            try:
                item_total = f"{sum(float(item.get('price') or 0) * item['quantity'] for item in items):.2f}"
                publish_invoice_status(
                    invoice_id=invoice_id,
                    identity_uuid=user_id,
                    status="sent",
                    amount=item_total,
                    correlation_id=msg_id,
                    channel=channel,
                )
            except Exception as status_err:
                logging.warning("[RECEIVER] invoice_status failed for invoice_request: %s", status_err)

            _inv_data = None
            _base64 = ""

            try:
                _inv_data = get_invoice(invoice_id)
                _pdf_bytes = fossbilling_client.get_invoice_pdf(
                    invoice_id, invoice_hash=(_inv_data or {}).get("hash")
                )
                _base64 = base64.b64encode(_pdf_bytes).decode("utf-8")
                notification_xml = build_invoice_created_notification_xml(
                    invoice_id=invoice_id,
                    recipient_email=customer["email"],
                    correlation_id=msg_id,
                    first_name=customer.get("first_name", ""),
                    last_name=customer.get("last_name", ""),
                    identity_uuid=user_id,
                    subject=f"Uw factuur {invoice_id} staat klaar",
                    invoice_data=_inv_data,
                    pdf_bytes=_pdf_bytes,
                )
                send_message(notification_xml, routing_key="facturatie.to.mailing", channel=channel)
            except Exception as mail_err:
                logging.warning("[RECEIVER] Invoice created but mailing failed: %s", mail_err)

            try:
                publish_invoice_link(
                    invoice_id,
                    user_id,
                    channel=channel,
                    status=(_inv_data or {}).get("status", "sent"),
                    invoice_date=(_inv_data or {}).get("created_at", ""),
                    base64_data=_base64,
                )
            except Exception as link_err:
                logging.warning("[RECEIVER] Invoice created but invoice_link failed: %s", link_err)

            logging.info(
                "[RECEIVER] invoice_request processed | invoice_id=%s | correlation_id=%s",
                invoice_id, correlation_id,
            )
            send_log(
                "info", "invoice",
                f"invoice_request saved for company_id={company_id}, items={len(items)}",
                channel=channel,
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            send_log(
                "error", "system_error",
                f"invoice_request save failed for correlation_id={correlation_id}: {e}",
                channel=channel,
            )
            logging.error("[RECEIVER] ERROR: invoice_request_failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: invoice_request_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    elif msg_type == "consumption_order":
        customer_id = root.findtext("body/customer/id") or ""
        identity_uuid = root.findtext("body/customer/identity_uuid") or ""
        email = root.findtext("body/customer/email") or ""

        item_elements = root.findall("body/items/item")
        if not item_elements:
            send_to_dlq(channel, body, ["ERROR: consumption_order has no items"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        items = []
        for item_el in item_elements:
            unit_price_el = item_el.find("unit_price")
            price = unit_price_el.text if unit_price_el is not None else "0.00"
            session_id = item_el.findtext("session_id") or None
            items.append({
                "description": item_el.findtext("description") or "",
                "price": price,
                "quantity": int(item_el.findtext("quantity") or "1"),
                "vat_rate": item_el.findtext("vat_rate") or "",
                "session_id": session_id,
            })

        try:
            consumption_store.save_items(
                company_id=customer_id,
                badge_id=identity_uuid,
                master_uuid=identity_uuid,
                items=items,
                email=email,
                consumption_order_id=msg_id,
            )
            logging.info(
                "[RECEIVER] consumption_order saved | message_id=%s | customer_id=%s | items=%d",
                msg_id, customer_id, len(items),
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logging.error("[RECEIVER] ERROR: consumption_order_save_failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: consumption_order_save_failed: {e}"])
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
                    event_identity_uuid = root.findtext("body/identity_uuid") or ""
                    master_uuid = meta.get("master_uuid", "") or event_identity_uuid
                    if not master_uuid:
                        logging.error(f"[RECEIVER] Geen UUID gevonden voor company {company_id}")
                        master_uuid = "00000000-0000-0000-0000-000000000000"
                    invoice_id = fossbilling_client.process_consumption_order(
                        company_id, items, company_name=meta["company_name"]
                    )

                    consumption_store.clear_by_ids(row_ids)

                    try:
                        item_total = f"{sum(float(item.get('price') or 0) * item['quantity'] for item in items):.2f}"
                        publish_invoice_status(
                            invoice_id=invoice_id,
                            identity_uuid=master_uuid,
                            status="sent",
                            amount=item_total,
                            correlation_id=msg_id,
                            channel=channel,
                        )
                    except Exception as status_err:
                        logging.warning(
                            "[RECEIVER] invoice_status failed for event_ended company_id=%s: %s",
                            company_id, status_err
                        )

                    send_log(
                        "info", "invoice",
                        f"Consolidated invoice {invoice_id} created for company {company_id}",
                        channel=channel,
                    )

                    _inv_data = None
                    _base64 = ""

                    try:
                        _inv_data = get_invoice(invoice_id)
                        _inv_hash = (_inv_data or {}).get("hash")
                        _pdf_bytes = fossbilling_client.get_invoice_pdf(invoice_id, invoice_hash=_inv_hash)
                        _base64 = base64.b64encode(_pdf_bytes).decode("utf-8")
                        notification_xml = build_invoice_created_notification_xml(
                            invoice_id=invoice_id,
                            recipient_email=meta["email"],
                            correlation_id=msg_id,
                            first_name=meta.get("company_name", ""),
                            last_name="",
                            identity_uuid=meta.get("master_uuid", "") or master_uuid,
                            subject=f"Uw factuur {invoice_id} staat klaar",
                            invoice_data=_inv_data,
                            pdf_bytes=_pdf_bytes,
                        )
                        send_message(notification_xml, routing_key="facturatie.to.mailing", channel=channel)
                    except Exception as mail_err:
                        logging.error("[RECEIVER] Mailing failed for company_id=%s: %s", company_id, mail_err)
                        errors.append(f"company_id={company_id}: mailing_failed: {mail_err}")

                    try:
                        publish_invoice_link(
                            invoice_id,
                            master_uuid,
                            channel=channel,
                            status=(_inv_data or {}).get("status", "sent"),
                            invoice_date=(_inv_data or {}).get("created_at", ""),
                            base64_data=_base64,
                        )
                    except Exception as link_err:
                        logging.warning(
                            "[RECEIVER] Invoice created but invoice_link failed for %s: %s",
                            company_id, link_err
                        )

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
            send_log("error", "system_error", f"FossBilling failed for company_id={company_id}: {e}", channel=channel)
            logging.error("[RECEIVER] ERROR: event_ended_failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: event_ended_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    elif msg_type == "payment_registered":
        logging.info("[RECEIVER] Handling payment_registered")

        try:
            # Extract invoice info — inkomend formaat (Kassa→Facturatie via CRM passthrough)
            invoice_el = root.find("body/invoice")
            if invoice_el is None:
                raise ValueError("Missing <invoice> element")

            invoice_id = invoice_el.findtext("id") or ""

            due_date = invoice_el.findtext("due_date") or ""

            if not invoice_id:
                header_correlation_id = root.findtext("header/correlation_id") or ""
                if header_correlation_id:
                    invoice_id = consumption_store.get_invoice_id_by_correlation_id(header_correlation_id)
                if not invoice_id:
                    raise ValueError("Missing invoice id and could not resolve via correlation_id")

            amount_el = invoice_el.find("amount_paid")
            amount = amount_el.text if amount_el is not None else None
            currency = amount_el.get("currency", "eur") if amount_el is not None else "eur"

            # Extract transaction info
            transaction_el = root.find("body/transaction")
            if transaction_el is None:
                raise ValueError("Missing <transaction> element")

            payment_method = transaction_el.findtext("payment_method") or ""
            transaction_id = transaction_el.findtext("id") or ""

            payment_method_out = payment_method

            identity_uuid = root.findtext("body/identity_uuid") or root.findtext("body/user_id") or ""

            logging.info(
                "[RECEIVER] Payment data extracted"
                " | invoice_id=%s | amount=%s %s"
                " | method=%s | transaction_id=%s",
                invoice_id, amount, currency, payment_method, transaction_id
            )

            invoice_data = fossbilling_client.get_invoice(invoice_id)
            if invoice_data is None:
                raise Exception(f"Invoice '{invoice_id}' not found in FossBilling")

            invoice_total = Decimal(
                invoice_data.get("total_with_tax") or invoice_data.get("total") or 0
            )
            payment_amount = Decimal(amount) if amount else 0.0
            is_full_payment = payment_amount >= invoice_total

            if is_full_payment:
                success = pay_invoice(invoice_id, amount)
                if not success:
                    raise Exception(f"Failed to register payment for invoice '{invoice_id}'")
                invoice_status = "paid"
                logging.info("[RECEIVER] Full payment registered in FossBilling | invoice_id=%s", invoice_id)
            else:
                invoice_status = "pending"
                logging.info(
                    "[RECEIVER] Partial payment for invoice '%s' | paid=%s < total=%s | status set to pending",
                    invoice_id, payment_amount, invoice_total,
                )

            try:
                publish_invoice_status(
                    invoice_id=invoice_id,
                    identity_uuid=identity_uuid,
                    status="paid",
                    amount=amount or "0.00",
                    correlation_id=msg_id,
                    channel=channel,
                )
            except Exception as status_err:
                logging.warning("[RECEIVER] invoice_status failed for payment_registered: %s", status_err)

            paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            confirmation_xml = build_payment_confirmed_xml(
                invoice_id=invoice_id,
                identity_uuid=identity_uuid,
                amount=amount,
                currency=currency,
                payment_method=payment_method_out,
                paid_at=paid_at,
                due_date=due_date,
                status=invoice_status,
                correlation_id=msg_id,
            )
            send_message(
                confirmation_xml,
                routing_key=CRM_QUEUE,
                channel=channel,
            )
            logging.info(
                "[RECEIVER] payment_registered confirmation sent"
                " | invoice_id=%s | correlation_id=%s",
                invoice_id, msg_id
            )

            send_log("info", "payment", f"Payment registered for invoice {invoice_id}", channel=channel)

            channel.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            send_log("error", "system_error", f"FossBilling payment failed: {e}", channel=channel)
            logging.error("[RECEIVER] ERROR: payment_registered_failed: %s", e)
            send_to_dlq(channel, body, [f"ERROR: payment_registered_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    elif msg_type == "invoice_cancelled":
        logging.info("[RECEIVER][%s] Handling cancellation", msg_type)

        errors = validate_invoice_cancelled(root)
        if errors:
            send_to_dlq(channel, body, errors)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        invoice_id = root.findtext("body/invoice_id")
        identity_uuid = root.findtext("body/identity_uuid") or ""
        correlation_id = root.findtext("header/correlation_id")

        if not invoice_id and not correlation_id:
            send_to_dlq(channel, body, ["ERROR: missing both invoice_id and correlation_id"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # If not invoice_id, look for correlation_id
        if not invoice_id:
            invoice_id = consumption_store.get_invoice_id_by_correlation_id(correlation_id)
            if not invoice_id:
                logging.warning("[RECEIVER] No invoice found for correlation_id=%s", correlation_id)
                publish_cancellation_failed(
                    invoice_id="unknown",
                    customer_id=identity_uuid,
                    reason="invoice_not_found",
                    channel=channel,
                )
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

        logging.info("[RECEIVER][%s] Processing invoice=%s", msg_type, invoice_id)

        try:
            invoice = fossbilling_client.get_invoice(invoice_id)
        except Exception as e:
            error_msg = f"ERROR: FossBilling unreachable during invoice fetch: {e}"
            logging.error("[RECEIVER][%s] %s", msg_type, error_msg)
            send_to_dlq(channel, body, [error_msg])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        if invoice is None:
            logging.warning(
                "[RECEIVER][%s] Invoice '%s' not found in FossBilling", msg_type, invoice_id
            )
            publish_cancellation_failed(
                invoice_id,
                customer_id=identity_uuid,
                reason="invoice_not_found",
                channel=channel,
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        status = invoice.get("status", "")

        if status == "cancelled":
            reason = "invoice_already_cancelled"
            logging.warning(
                "[RECEIVER][%s] Cancellation blocked — invoice '%s' already cancelled",
                msg_type, invoice_id
            )
            publish_cancellation_failed(
                invoice_id,
                customer_id=identity_uuid,
                reason=reason,
                channel=channel,
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        if status == "paid":
            success = fossbilling_client.create_credit_note(invoice)
            if success:
                success = fossbilling_client.cancel_invoice(invoice_id)
        else:
            success = fossbilling_client.cancel_invoice(invoice_id)

        if not success:
            error_msg = f"ERROR: FossBilling failed to cancel invoice '{invoice_id}'"
            send_to_dlq(channel, body, [error_msg])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        publish_invoice_cancelled(invoice_id, identity_uuid, channel=channel)
        send_log("info", "invoice", f"Invoice {invoice_id} cancelled", channel=channel)
        logging.info("[RECEIVER][%s] Flow complete for invoice '%s'", msg_type, invoice_id)
        channel.basic_ack(delivery_tag=method.delivery_tag)

    elif msg_type == "profile_update":
        try:
            identity_uuid = root.findtext("body/identity_uuid") or ""
            email = root.findtext("body/email") or ""
            first_name = root.findtext("body/contact/first_name") or ""
            last_name = root.findtext("body/contact/last_name") or ""
            company_name = root.findtext("body/company_name") or ""
            vat_number = root.findtext("body/vat_number") or ""
            customer_type = root.findtext("body/type") or "private"

            logging.info(
                "[RECEIVER] profile_update received | identity_uuid=%s | company=%s",
                identity_uuid, company_name
            )

            # Update customer data in FossBilling using identity_uuid
            success = fossbilling_client.update_client_by_identity_uuid(
                identity_uuid=identity_uuid,
                email=email,
                first_name=first_name,
                last_name=last_name,
                company_name=company_name,
                vat_number=vat_number,
            )

            if not success:
                raise Exception(f"FossBilling update failed for identity_uuid={identity_uuid}")

            send_log(
                "info", "user",
                f"Profile updated for identity_uuid={identity_uuid}",
                channel=channel
            )

            logging.info("[RECEIVER] profile_update processed | identity_uuid=%s", identity_uuid)
            channel.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            logging.error("[RECEIVER] ERROR: profile_update_failed: %s", e)
            send_log("error", "system_error", f"profile_update failed: {e}", channel=channel)
            send_to_dlq(channel, body, [f"ERROR: profile_update_failed: {e}"])
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    else:
        logging.info("[RECEIVER] No handler for type '%s' — acknowledging", msg_type)
        channel.basic_ack(delivery_tag=method.delivery_tag)


def start_receiver(queue: str | None = None) -> None:
    if queue is None:
        queue = os.getenv("QUEUE_INCOMING", "facturatie.incoming")

    consumption_store.init_db()

    logging.info("[RECEIVER] Starting — will listen on queue '%s'", queue)

    while True:
        connection = None
        try:
            connection = get_connection_with_retry(max_attempts=5)
            channel = connection.channel()

            channel.queue_declare(queue=queue, passive=False, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=queue, on_message_callback=process_message)

            logging.info("[RECEIVER] Listening on queue '%s'... (CTRL+C to stop)", queue)
            channel.start_consuming()

        except KeyboardInterrupt:
            logging.info("[RECEIVER] Stopping consumer...")
            break

        except Exception as e:
            logging.error("[RECEIVER] Connection lost: %s — reconnecting...", e)

        finally:
            try:
                if connection and connection.is_open:
                    connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    start_receiver()
