import os
import time
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def _api_post(endpoint: str, data: dict) -> dict:
    """Makes an authenticated POST request to the FossBilling admin API."""
    url = f"{os.getenv('BILLING_API_URL', 'http://localhost/api')}/{endpoint}"
    auth = (os.getenv("BILLING_API_USERNAME", "admin"), os.getenv("BILLING_API_TOKEN", ""))
    response = requests.post(url, data=data, auth=auth, timeout=10, verify=False)
    response.raise_for_status()
    result = response.json()
    if not result.get("result"):
        error_msg = result.get("error", {}).get("message", "unknown error")
        raise Exception(f"FossBilling API error on '{endpoint}': {error_msg}")
    return result


def _create_client(customer_data: dict) -> int:
    """Creates a new client in FossBilling. Returns the client_id."""
    address = customer_data.get("address", {})
    payload = {
        "email": customer_data["email"],
        "first_name": customer_data.get("company_name") or "Onbekend",
        "last_name": "-",
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


def _create_invoice(client_id: int, fee: str, currency: str) -> str:
    """Creates a registration invoice for a client in FossBilling. Returns the invoice_id."""
    payload = {
        "client_id": client_id,
        "items[0][title]": "Inschrijvingskosten",
        "items[0][price]": fee,
        "items[0][quantity]": 1,
        "items[0][unit]": currency.upper(),
    }
    result = _api_post("admin/invoice/prepare", payload)
    return str(result["result"])


def create_registration_invoice(customer_data: dict) -> str:
    """
    Creates a FossBilling client and registration invoice.
    Retries up to MAX_RETRIES times on failure.
    Returns the invoice_id on success.
    Raises Exception if all retries are exhausted.
    """
    client_id = _create_client(customer_data)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            invoice_id = _create_invoice(
                client_id,
                customer_data["registration_fee"],
                customer_data.get("fee_currency", "eur"),
            )
            print(f"[FOSSBILLING] Invoice created | invoice_id={invoice_id} | attempt={attempt}/{MAX_RETRIES}")
            return invoice_id
        except Exception as e:
            last_error = e
            print(f"[FOSSBILLING] Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise Exception(f"FossBilling invoice creation failed after {MAX_RETRIES} attempts: {last_error}")
