"""
Lokaal testscript voor de consumption_order flow.
Stap 1: toont beschikbare klanten in FossBilling
Stap 2: stuurt een testbericht naar de queue
"""

import os
import requests
from dotenv import load_dotenv
from src.services.rabbitmq_sender import build_consumption_order_xml, send_message

load_dotenv()

BILLING_URL = os.getenv("BILLING_API_URL")
AUTH = (os.getenv("BILLING_API_USERNAME"), os.getenv("BILLING_API_TOKEN"))
QUEUE = os.getenv("QUEUE_INCOMING", "facturatie.incoming")


def list_clients():
    """Toont de eerste 10 klanten in FossBilling."""
    resp = requests.post(f"{BILLING_URL}/admin/client/get_list", auth=AUTH, data={"per_page": 10}, timeout=10)
    clients = resp.json().get("result", {}).get("list", [])
    print("\n── Beschikbare klanten in FossBilling ──")
    for c in clients:
        print(f"  ID: {c['id']}  |  {c.get('first_name', '')} {c.get('last_name', '')}  |  {c.get('company', '')}  |  {c['email']}")
    print()
    return clients


def send_test_order(company_id: str, badge_id: str = "BADGE-007"):
    xml = build_consumption_order_xml(
        customer_id=badge_id,
        items=[
            {"id": "BEV-001", "description": "Coca-Cola", "quantity": 2, "unit_price": "2.50", "vat_rate": 21},
            {"id": "BEV-002", "description": "Water", "quantity": 1, "unit_price": "1.50", "vat_rate": 6},
        ],
        is_company_linked=True,
        company_id=company_id,
        company_name="Testbedrijf NV",
    )
    print(f"── Versturen naar queue '{QUEUE}' ──")
    print(f"   company_id : {company_id}")
    print(f"   badge_id   : {badge_id}")
    send_message(xml, routing_key=QUEUE)
    print("   Bericht verstuurd!\n")


if __name__ == "__main__":
    clients = list_clients()

    if not clients:
        print("Geen klanten gevonden in FossBilling.")
    else:
        company_id = input("Geef het client-ID in dat je wil testen: ").strip()
        badge_id = input("Geef een badge-ID (of Enter voor 'BADGE-007'): ").strip() or "BADGE-007"
        send_test_order(company_id, badge_id)
