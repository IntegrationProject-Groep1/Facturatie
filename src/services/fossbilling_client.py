import os
import requests
from dotenv import load_dotenv

load_dotenv()


def cancel_invoice(invoice_id: str) -> bool:
    """
    Cancels an invoice in FossBilling by setting its status to 'cancelled'.
    Uses POST /api/admin/invoice/update — keeps the invoice visible as a credit note.
    Returns True on success, False on any failure.
    """
    base_url = os.getenv("BILLING_API_URL", "").rstrip("/")
    api_token = os.getenv("BILLING_API_TOKEN", "")
    api_username = os.getenv("BILLING_API_USERNAME", "admin")

    if not base_url or not api_token:
        print("[FOSSBILLING] ERROR: BILLING_API_URL or BILLING_API_TOKEN not configured")
        return False

    url = f"{base_url}/api/admin/invoice/update"
    try:
        response = requests.post(
            url,
            data={"id": invoice_id, "status": "cancelled"},
            auth=(api_username, api_token),
            timeout=10,
            verify=True,
        )
        if response.status_code == 200:
            print(f"[FOSSBILLING] Invoice '{invoice_id}' successfully marked as cancelled")
            return True
        print(f"[FOSSBILLING] ERROR: API returned status {response.status_code} for invoice '{invoice_id}'")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[FOSSBILLING] ERROR: Connection failed for invoice '{invoice_id}': {e}")
        return False
