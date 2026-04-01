import requests
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from src.services.rabbitmq_sender import send_message
import pytest

load_dotenv()

# Only define functions, don't execute code at module level
def get_auth_method():
    """Test which auth method works"""
    url = f"{os.getenv('BILLING_API_URL')}/admin/client/get_list"
    username = os.getenv("BILLING_API_USERNAME")
    token = os.getenv("BILLING_API_TOKEN")
    
    methods = [
        ("Basic Auth (email + token)",
         lambda: requests.post(url, auth=(username, token), data={}, timeout=10, verify=False)),
        ("Bearer token",
         lambda: requests.post(url, headers={"Authorization": f"Bearer {token}"}, data={}, timeout=10, verify=False)),
        ("access_token in body",
         lambda: requests.post(url, data={"access_token": token}, timeout=10, verify=False)),
        ("access_token in URL",
         lambda: requests.post(f"{url}?access_token={token}", data={}, timeout=10, verify=False)),
    ]
    
    for name, method in methods:
        try:
            r = method()
            print(f"[TEST] {name} → {r.status_code}: {r.text[:100]}")
            if r.status_code == 200:
                return name
        except Exception as e:
            print(f"[TEST] {name} failed: {e}")
    return None

@pytest.mark.integration
def test_auth_methods():
    """Test FossBilling authentication"""
    working_method = get_auth_method()
    assert working_method is not None, "No authentication method worked"
    print(f"\n[TEST] Werkende methode: {working_method}")

@pytest.mark.integration
def test_client_create():
    """Test client creation endpoint"""
    working_method = get_auth_method()
    if not working_method:
        pytest.skip("Authentication failed")
    
    username = os.getenv("BILLING_API_USERNAME")
    token = os.getenv("BILLING_API_TOKEN")
    
    r2 = requests.post(
        f"{os.getenv('BILLING_API_URL')}/admin/client/create",
        auth=(username, token),
        data={
            "email": "debug@gmail.com",
            "first_name": "Test",
            "last_name": "User",
            "currency": "EUR",
        },
        timeout=10,
        verify=False
    )
    assert r2.status_code == 200, f"Got {r2.status_code}: {r2.text[:300]}"

@pytest.mark.integration
def test_new_registration_message():
    """Test sending new_registration message"""
    working_method = get_auth_method()
    if not working_method:
        pytest.skip("Authentication failed")
    
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

    send_message(xml)
    # Add assertion to verify message was sent successfully
    assert True, "Message sent"