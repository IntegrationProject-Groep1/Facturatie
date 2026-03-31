import requests
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from src.services.rabbitmq_sender import send_message

load_dotenv()

# --- Stap 1: FossBilling auth testen ---
url = f"{os.getenv('BILLING_API_URL')}/admin/client/get_list"
username = os.getenv("BILLING_API_USERNAME")
token = os.getenv("BILLING_API_TOKEN")

methods = [
    ("Basic Auth (email + token)",
     lambda: requests.post(url, auth=(username, token), data={}, timeout=10)),
    ("Bearer token",
     lambda: requests.post(url, headers={"Authorization": f"Bearer {token}"}, data={}, timeout=10)),
    ("access_token in body",
     lambda: requests.post(url, data={"access_token": token}, timeout=10)),
    ("access_token in URL",
     lambda: requests.post(f"{url}?access_token={token}", data={}, timeout=10)),
]

working_method = None
for name, method in methods:
    r = method()
    print(f"[TEST] {name} → {r.status_code}: {r.text[:100]}")
    if r.status_code == 200:
        working_method = name
        break

if working_method:
    print(f"\n[TEST] Werkende methode: {working_method}")

    # --- Stap 1b: client/create debug ---
    print("\n[TEST] client/create testen met minimale velden...")
    r2 = requests.post(
        f"{os.getenv('BILLING_API_URL')}/admin/client/create",
        auth=(username, token),
        data={
            "email": "debug@gmail.com",
            "first_name": "Test",
            "last_name": "User",
            "currency": "EUR",
        },
        timeout=10
    )
    print(f"[TEST] Status: {r2.status_code}")
    print(f"[TEST] Response: {r2.text[:300]}")
else:
    print("\n[TEST] Geen enkele auth-methode werkt.")

if not working_method:
    print("[TEST] Auth mislukt — RabbitMQ bericht wordt NIET verstuurd.")
else:
    # --- Stap 2: new_registration bericht sturen ---
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>{uuid.uuid4()}</message_id>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <customer>
      <email>demo-{uuid.uuid4()}@gmail.com</email>
      <is_company_linked>false</is_company_linked>
      <address>
        <street>Teststraat</street>
        <number>1</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>be</country>
      </address>
    </customer>
    <registration_fee currency="eur">150.00</registration_fee>
  </body>
</message>"""

    print("[TEST] Sending new_registration message...")
    send_message(xml)
    print("[TEST] Message sent.")
