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


def _create_client(customer_data: dict, extra_fields: dict = None) -> int:
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

    if extra_fields:
        payload.update(extra_fields)

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
        print(f"[FOSSBILLING] Client already exists | client_id={existing_id}")
        return existing_id
    return _create_client(customer_data)

def _get_client_by_custom_field(field_name: str, value: str) -> int | None:
    """
    Zoekt een client op basis van een custom field. 
    We halen de lijst op en filteren handmatig in Python om false positives te voorkomen.
    """
    # We gebruiken de 'search' parameter van FossBilling om de lijst te verkleinen
    response = _api_post("admin/client/get_list", {"search": value})
    clients = response.get("result", {}).get("list", [])
    
    for client in clients:
        # Check of het gevraagde veld exact overeenkomt in de resultaten
        if client.get(field_name) == value:
            return int(client["id"])
    return None

def _create_client(customer_data: dict, extra_params: dict = None) -> int:
    """Maakt een client aan en zorgt dat extra velden (zoals custom_1) worden opgeslagen."""
    address = customer_data.get("address", {})
    payload = {
        "email": customer_data["email"],
        "first_name": customer_data.get("first_name") or "Onbekend",
        "last_name": customer_data.get("last_name") or "-",
        "password": f"Reg-{uuid.uuid4()}",
        "password_confirm": "",
        "address_1": f"{address.get('street', '')} {address.get('number', '')}".strip(),
        "city": address.get("city", ""),
        "postcode": address.get("postal_code", ""),
        "country": address.get("country", "BE").upper(),
        "currency": customer_data.get("fee_currency", "EUR").upper(),
    }
    
    if customer_data.get("company_name"):
        payload["company"] = customer_data["company_name"]
    
    # Cruciaal: voeg de custom_1 (company_id) toe aan de aanmaak-payload
    if extra_params:
        payload.update(extra_params)

    result = _api_post("admin/client/create", payload)
    return int(result["result"])

def get_or_create_client_id(customer_data: dict) -> int:
    """
    B2B flow met harde controle op company_id.
    """
    if customer_data.get("company_id"):
        company_id = customer_data["company_id"]
        
        # 1. Zoek op company_id (custom_1)
        client_id = _get_client_by_custom_field("custom_1", company_id)
        
        if client_id:
            print(f"[FOSSBILLING] B2B Match gevonden! ID: {client_id} voor {company_id}")
            return client_id
        
        # 2. Niet gevonden? Check email om duplicaten te voorkomen
        existing_id = _get_client_by_email(customer_data["email"])
        if existing_id:
            print(f"[FOSSBILLING] Email bestaat al (ID: {existing_id}). Koppelen aan company_id.")
            # Optioneel: hier zou je een 'admin/client/update' kunnen doen om custom_1 alsnog te zetten
            return existing_id
        
        # 3. Nieuw B2B account aanmaken
        print(f"[FOSSBILLING] Geen match. Nieuw B2B account voor: {company_id}")
        return _create_client(customer_data, extra_params={"custom_1": company_id})

    # B2C flow
    return _get_or_create_client(customer_data)

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
    print(f"[FOSSBILLING] Client updated | client_id={client_id}")


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
            client_id = get_or_create_client_id(customer_data)
            invoice_id = _create_invoice(client_id, items)
            print(f"[FOSSBILLING] Invoice created | invoice_id={invoice_id} | attempt={attempt}/{MAX_RETRIES}")
            return invoice_id
        except Exception as e:
            last_error = e
            print(f"[FOSSBILLING] Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise Exception(f"FossBilling invoice creation failed after {MAX_RETRIES} attempts: {last_error}")


def pay_invoice(invoice_id: str, amount: str) -> bool:
    """
    Marks an invoice as paid by updating its status directly.
    Works on all FossBilling versions.
    """
    try:
        payload = {
            "id": invoice_id,
            "status": "paid",
            "paid_at": int(time.time())
        }

        _api_post("admin/invoice/update", payload)

        print(f"[FOSSBILLING] Invoice '{invoice_id}' marked as PAID via update()")
        return True

    except Exception as e:
        print(f"[FOSSBILLING] ERROR: Failed to update invoice '{invoice_id}': {e}")
        return False


def cancel_invoice(invoice_id: str) -> bool:
    """Cancels an invoice in FossBilling by setting its status to 'cancelled'.
    Returns True on success, False on any failure.
    """
    try:
        _api_post("admin/invoice/update", {"id": invoice_id, "status": "cancelled"})
        print(f"[FOSSBILLING] Invoice '{invoice_id}' successfully marked as cancelled")
        return True
    except Exception as e:
        print(f"[FOSSBILLING] ERROR: Failed to cancel invoice '{invoice_id}': {e}")
        return False
