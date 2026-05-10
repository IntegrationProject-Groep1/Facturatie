import logging
import os
import re
import time
import uuid
import requests
from dotenv import load_dotenv
from .consumption_store import get_company_client_id, save_company_client_id
import datetime as dt

load_dotenv()

MAX_RETRIES = 3


class FossBillingNotFoundError(Exception):
    """Raised when FossBilling confirms the requested resource does not exist."""


RETRY_DELAY_SECONDS = 2


def _api_post(endpoint: str, data: dict) -> dict:
    """Makes an authenticated POST request to the FossBilling admin API."""
    url = f"{os.getenv('BILLING_API_URL', 'http://localhost/api')}/{endpoint}"
    auth = (os.getenv("BILLING_API_USERNAME", "admin"), os.getenv("BILLING_API_TOKEN", ""))
    response = requests.post(url, data=data, auth=auth, timeout=10)
    response.raise_for_status()
    result = response.json()
    if not result.get("result"):
        error_msg = result.get("error", {}).get("message", "unknown error")
        if "not found" in error_msg.lower():
            raise FossBillingNotFoundError(error_msg)
        raise Exception(f"FossBilling API error on '{endpoint}': {error_msg}")
    return result


def _create_client(customer_data: dict) -> int:
    """Creates a new client in FossBilling. Returns the client_id."""
    address = customer_data.get("address", {})
    payload = {
        "email": customer_data["email"],
        "first_name": customer_data.get("first_name") or "Unknown",
        "last_name": customer_data.get("last_name") or "-",
        "password": f"Reg-{uuid.uuid4()}",
        "password_confirm": "",
        "address_1": f"{address.get('street', '')} {address.get('number', '')}".strip(),
        "city": address.get("city", ""),
        "postcode": address.get("postal_code", ""),
        "country": address.get("country", "").upper(),
        "currency": customer_data.get("fee_currency", "eur").upper(),
    }
    if customer_data.get("company_name"):
        payload["company"] = customer_data["company_name"]

    result = _api_post("admin/client/create", payload)
    return int(result["result"])


def _get_client_by_email(email: str) -> int | None:
    """Looks up a client by email in FossBilling. Returns client_id or None if not found."""
    result = _api_post("admin/client/get_list", {"search": email, "per_page": 1})
    clients = result.get("result", {}).get("list", [])
    if clients:
        return int(clients[0]["id"])
    return None


def _get_or_create_client(customer_data: dict) -> int:
    """Returns existing client_id if email is already registered, otherwise creates a new client."""
    existing_id = _get_client_by_email(customer_data["email"])
    if existing_id is not None:
        logging.info("[FOSSBILLING] Client already exists | client_id=%s", existing_id)
        return existing_id
    return _create_client(customer_data)


def update_client(client_id: int, customer_data: dict) -> None:
    """Updates an existing client in FossBilling with the provided customer data.
    Raises Exception if the API call fails.
    """
    address = customer_data.get("address", {})
    payload = {
        "id": client_id,
        "email": customer_data["email"],
        "first_name": customer_data.get("first_name") or "Unknown",
        "last_name": customer_data.get("last_name") or "-",
        "address_1": f"{address.get('street', '')} {address.get('number', '')}".strip(),
        "city": address.get("city", ""),
        "postcode": address.get("postal_code", ""),
        "country": address.get("country", "").upper(),
    }
    if customer_data.get("company_name"):
        payload["company"] = customer_data["company_name"]
    _api_post("admin/client/update", payload)
    logging.info("[FOSSBILLING] Client updated | client_id=%s", client_id)


def _create_invoice(client_id: int, items: list[dict]) -> str:
    """Creates an invoice for a client in FossBilling. Returns the invoice_id.

    Each item dict must contain:
        title (str), price (str), quantity (int)
    Optional fields per item:
        currency (str), vat_rate (int|str), sku (str)
    """
    payload = {"client_id": client_id}
    for i, item in enumerate(items):
        payload[f"items[{i}][title]"] = item["title"]
        payload[f"items[{i}][price]"] = item["price"]
        payload[f"items[{i}][quantity]"] = item.get("quantity", 1)
        if item.get("currency"):
            payload[f"items[{i}][unit]"] = str(item["currency"]).upper()
        if item.get("vat_rate"):
            payload[f"items[{i}][taxrate]"] = item["vat_rate"]
        if item.get("sku"):
            payload[f"items[{i}][sku]"] = item["sku"]
    result = _api_post("admin/invoice/prepare", payload)
    return str(result["result"])


def create_registration_invoice(customer_data: dict) -> str:
    """
    Creates a FossBilling client and registration invoice.
    Both client and invoice creation are retried up to MAX_RETRIES times on failure.
    Returns the invoice_id on success.
    Raises Exception if all retries are exhausted.
    """
    items = [{
        "title": "Inschrijvingskosten",
        "price": customer_data["registration_fee"],
        "quantity": 1,
        "currency": customer_data.get("fee_currency", "eur"),
    }]
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client_id = _get_or_create_client(customer_data)
            invoice_id = _create_invoice(client_id, items)

            if customer_data.get("payment_status") == "paid":
                mark_invoice_as_paid(invoice_id)
                logging.info("[FOSSBILLING] Invoice marked as paid | invoice_id=%s", invoice_id)

            logging.info(
                "[FOSSBILLING] Invoice created | invoice_id=%s | attempt=%d/%d",
                invoice_id, attempt, MAX_RETRIES
            )
            return invoice_id
        except Exception as e:
            last_error = e
            logging.error("[FOSSBILLING] Attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise Exception(f"FossBilling invoice creation failed after {MAX_RETRIES} attempts: {last_error}")


def pay_invoice(invoice_id: str, amount: str) -> bool:
    """
    Marks an invoice as paid. paid_at is sent as a datetime string
    because FossBilling ignores Unix timestamps for this field.
    """
    paid_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        _api_post("admin/invoice/update", {
            "id": invoice_id,
            "status": "paid",
            "paid_at": paid_at,
        })

        logging.info("[FOSSBILLING] Invoice '%s' marked as PAID | paid_at=%s", invoice_id, paid_at)
        return True

    except Exception as e:
        from .rabbitmq_sender import send_log
        send_log(
            level="error",
            action="system_error",
            message=f"Internal Error in [FossBilling_API]: Failed to update invoice '{invoice_id}': {e}"
        )
        logging.error("[FOSSBILLING] ERROR: Failed to update invoice '%s': %s", invoice_id, e)
        return False


def get_invoice(invoice_id: str) -> dict | None:
    """Fetches the full invoice object from FossBilling.
    Returns None if the invoice is not found.
    Raises Exception for transient errors (network issues, API unreachable).
    """
    try:
        result = _api_post("admin/invoice/get", {"id": invoice_id})
        return result.get("result", {})
    except FossBillingNotFoundError:
        logging.info("[FOSSBILLING] Invoice '%s' not found in FossBilling", invoice_id)
        return None
    except Exception as e:
        logging.error(
            "[FOSSBILLING] ERROR: Could not fetch invoice '%s': %s: %s",
            invoice_id, type(e).__name__, e
        )
        raise


def get_invoice_status(invoice_id: str) -> str | None:
    """Returns the status of an invoice from FossBilling (e.g. 'paid', 'unpaid', 'cancelled').
    Returns None if the invoice is definitively not found.
    Raises Exception for transient errors (network issues, API unreachable).
    """
    invoice = get_invoice(invoice_id)
    return invoice.get("status") if invoice is not None else None


def get_client_by_company_id(company_id: str) -> int | None:
    """Looks up a client by company_id in FossBilling. Returns client_id or None if not found."""
    result = _api_post("admin/client/get_list", {"search": company_id, "per_page": 100})
    clients = result.get("result", {}).get("list", [])
    for client in clients:
        if client.get("company") == company_id or str(client.get("id")) == company_id:
            return int(client["id"])
    return None


def get_unpaid_invoice_for_client(client_id: int) -> str | None:
    """Returns the invoice_id of the first unpaid invoice for the given client, or None."""
    result = _api_post("admin/invoice/get_list", {"client_id": client_id, "status": "unpaid", "per_page": 1})
    invoices = result.get("result", {}).get("list", [])
    for invoice in invoices:
        if invoice.get("status") == "unpaid":
            return str(invoice["id"])
    return None


def add_item_to_invoice(invoice_id: str, item: dict) -> None:
    """Adds a single item to an existing invoice in FossBilling."""
    payload = {
        "id": invoice_id,
        "title": item["title"],
        "price": item["price"],
        "quantity": item.get("quantity", 1),
    }
    if item.get("vat_rate"):
        payload["taxrate"] = item["vat_rate"]
    _api_post("admin/invoice/item_add", payload)


def _billing_email(company_id: str) -> str:
    safe = re.sub(r"[^a-z0-9]", "_", company_id.lower())
    return f"billing.{safe}@facturatie.be"


def _get_or_create_billing_client(
        company_id: str,
        company_name: str,
        first_name: str = "",
        last_name: str = "",
        email: str = None) -> int:
    """Returns the FossBilling client_id for the company billing account, creating it if needed."""
    client_id = get_company_client_id(company_id)
    if client_id is not None:
        return client_id

    if email is None:
        email = _billing_email(company_id)

    existing_id = _get_client_by_email(email)
    if existing_id is not None:
        save_company_client_id(company_id, existing_id)
        return existing_id

    payload = {
        "email": email,
        "first_name": first_name or company_name or company_id,
        "last_name": last_name or "-",
        "password": f"Billing-{uuid.uuid4()}",
        "password_confirm": "",
        "company": company_name or "",
        "currency": "EUR",
        "country": "BE",
    }
    result = _api_post("admin/client/create", payload)
    new_id = int(result["result"])
    save_company_client_id(company_id, new_id)
    logging.info(
        "[FOSSBILLING] Company billing account created | company_id=%s | client_id=%s",
        company_id, new_id,
    )
    return new_id


def process_consumption_order(
        company_id: str,
        items: list[dict],
        company_name: str = "",
        first_name: str = "",
        last_name: str = "",
        email: str = None) -> str:
    """
    Creates one consolidated invoice for the given company with all provided items.
    Called at event-end after items have been accumulated in MySQL.
    Returns the invoice_id.
    Raises ValueError immediately if company_id is not found.
    Raises Exception after MAX_RETRIES on transient API failures.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client_id = _get_or_create_billing_client(
                company_id,
                company_name,
                first_name=first_name,
                last_name=last_name,
                email=email
                )
            invoice_id = _create_invoice(client_id, items)
            logging.info(
                "[FOSSBILLING] Consolidated invoice created | invoice_id=%s | company_id=%s",
                invoice_id, company_id,
            )
            return invoice_id

        except Exception as e:
            last_error = e
            logging.warning("[FOSSBILLING] Attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise Exception(f"FossBilling consumption order failed after {MAX_RETRIES} attempts: {last_error}")


def get_invoice_type(invoice: dict) -> str:
    """Returns 'registration' if the invoice contains 'Inschrijvingskosten', otherwise 'consumption'."""
    lines = invoice.get("lines", [])
    for line in lines:
        if "inschrijvingskosten" in line.get("title", "").lower():
            return "registration"
    return "consumption"


def create_credit_note(invoice: dict) -> str:
    """Creates a credit note (negative invoice) for a paid registration invoice.
    Accepts an already-fetched invoice dict to avoid a redundant API call.
    Returns the credit note invoice_id.
    Raises Exception if the credit note cannot be created.
    """
    client_id = int(invoice["client_id"])
    lines = invoice.get("lines", [])

    if not lines:
        raise Exception("Invoice has no line items — cannot create credit note")

    credit_items = [
        {
            "title": f"Creditnota: {line['title']}",
            "price": -abs(float(line["price"])),
            "quantity": line.get("quantity", 1),
            "vat_rate": line.get("taxrate", ""),
        }
        for line in lines
    ]

    credit_note_id = _create_invoice(client_id, credit_items)
    logging.info(
        "[FOSSBILLING] Credit note created | credit_note_id=%s",
        credit_note_id,
    )
    return credit_note_id


def cancel_invoice(invoice_id: str) -> bool:
    """Cancels an invoice in FossBilling by setting its status to 'cancelled'.
    Returns True on success, False on any failure.
    """
    try:
        _api_post("admin/invoice/update", {"id": invoice_id, "status": "cancelled"})
        logging.info("[FOSSBILLING] Invoice '%s' successfully marked as cancelled", invoice_id)
        return True
    except Exception as e:
        logging.error("[FOSSBILLING] ERROR: Failed to cancel invoice '%s': %s", invoice_id, e)
        return False


def update_client_by_identity_uuid(
    identity_uuid: str,
    email: str,
    first_name: str,
    last_name: str,
    company_name: str = "",
    vat_number: str = "",
) -> bool:
    """Updates a client in FossBilling based on their email (via identity_uuid lookup).
    Returns True on success, False if client not found or update fails.
    """
    try:
        client_id = _get_client_by_email(email)
        if client_id is None:
            logging.warning(
                "[FOSSBILLING] profile_update: client not found for email=%s | identity_uuid=%s",
                email, identity_uuid
            )
            return False

        customer_data = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company_name": company_name,
            "address": {},
        }
        update_client(client_id, customer_data)
        logging.info(
            "[FOSSBILLING] Client profile updated | client_id=%s | identity_uuid=%s",
            client_id, identity_uuid
        )
        return True

    except Exception as e:
        logging.error("[FOSSBILLING] ERROR: profile_update failed for identity_uuid=%s: %s", identity_uuid, e)
        return False


def mark_invoice_as_paid(invoice_id: str) -> None:
    """Marks an invoice as paid in FossBilling."""
    gateway_id = _get_custom_gateway_id()
    _api_post("admin/invoice/update", {
        "id": int(invoice_id),
        "gateway_id": gateway_id,
    })
    _api_post("admin/invoice/mark_as_paid", {
        "id": int(invoice_id),
    })
    logging.info("[FOSSBILLING] Invoice %s marked as paid", invoice_id)


def _get_custom_gateway_id() -> int:
    """Gets the database ID of the Custom payment gateway."""
    result = _api_post("admin/invoice/gateway_get_list", {})
    gateways = result.get("result", {}).get("list", [])
    for gw in gateways:
        if gw.get("code") == "Custom":
            return int(gw["id"])
    raise Exception("Custom payment gateway not found in FossBilling")
