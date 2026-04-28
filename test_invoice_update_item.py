"""
Test of de order-gebaseerde aanpak werkt in FossBilling:
  1. Maak een product aan (of gebruik bestaand)
  2. Maak een order aan voor de client
  3. Probeer invoice/generate_all om orders samen te voegen op één factuur
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("BILLING_API_URL")
AUTH = (os.getenv("BILLING_API_USERNAME"), os.getenv("BILLING_API_TOKEN"))


def post(endpoint, data):
    resp = requests.post(f"{URL}/{endpoint}", auth=AUTH, data=data, timeout=10)
    return resp.json()


def test_list_products():
    print("\n── Beschikbare producten ──")
    result = post("admin/product/get_list", {"per_page": 10})
    products = result.get("result", {}).get("list", [])
    for p in products:
        print(f"  ID: {p['id']}  |  {p.get('title')}  |  €{p.get('price')}")
    return products


def test_order_create(client_id: str, product_id: str):
    print(f"\n── Poging: admin/order/create voor client {client_id}, product {product_id} ──")
    result = post("admin/order/create", {
        "client_id": client_id,
        "product_id": product_id,
        "quantity": 2,
    })
    print(f"Response: {str(result)[:300]}")
    return result.get("result")


def test_invoice_generate_all(client_id: str):
    print(f"\n── Poging: admin/invoice/generate_all voor client {client_id} ──")
    result = post("admin/invoice/generate_all", {"client_id": client_id})
    print(f"Response: {str(result)[:300]}")
    return result.get("result")


def test_invoice_batch_pay():
    print("\n── Poging: admin/invoice/batch_pay_with_credits ──")
    result = post("admin/invoice/batch_pay_with_credits", {})
    print(f"Response: {str(result)[:300]}")


if __name__ == "__main__":
    client_id = input("Client ID van het bedrijf (bv. 56): ").strip()

    products = test_list_products()

    if products:
        product_id = input("\nGeef een product ID om te testen: ").strip()
        order_id = test_order_create(client_id, product_id)
        if order_id:
            print(f"\nOrder aangemaakt: {order_id}")
            test_invoice_generate_all(client_id)
        else:
            print("\nOrder aanmaken mislukt — order-aanpak werkt niet.")
    else:
        print("\nGeen producten gevonden. Probeer invoice/generate_all rechtstreeks.")
        test_invoice_generate_all(client_id)
