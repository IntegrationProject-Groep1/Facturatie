"""
Manual end-to-end test: multiple badge holders → one consolidated invoice.

Run from the project root:
    MYSQL_HOST=localhost python scripts/test_consumption_flow.py

Requires:
  - docker compose up mysql -d   (MySQL running on localhost:3306)
  - BILLING_API_URL reachable    (uses .env values for FossBilling)
"""

import os
import sys

os.environ.setdefault("MYSQL_HOST", "localhost")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.consumption_store import (  # noqa: E402
    init_db, save_items, get_pending_company_ids,
    get_items_for_company, clear_company,
)
from src.services.fossbilling_api import process_consumption_order  # noqa: E402

COMPANY_ID = "FOSS-CUST-102"  # change to a real company_id in your FossBilling


def step(label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)


step("1. Creating pending_consumptions table if needed")
init_db()
print("  -> Table ready")

step("2. Simulating badge holder A ordering 2 items")
save_items(
    company_id=COMPANY_ID,
    badge_id="BADGE-001",
    master_uuid="BADGE-001",
    items=[
        {"description": "Coca-Cola", "price": "2.50", "quantity": 1, "vat_rate": "21"},
        {"description": "Water",     "price": "1.50", "quantity": 2, "vat_rate": "6"},
    ],
)
print("  -> BADGE-001 saved")

step("3. Simulating badge holder B ordering 1 item")
save_items(
    company_id=COMPANY_ID,
    badge_id="BADGE-002",
    master_uuid="BADGE-002",
    items=[
        {"description": "Fanta", "price": "2.50", "quantity": 3, "vat_rate": "21"},
    ],
)
print("  -> BADGE-002 saved")

step("4. Reading accumulated items from MySQL")
items = get_items_for_company(COMPANY_ID)
print(f"  -> {len(items)} items pending for {COMPANY_ID}:")
for item in items:
    print(f"     {item}")

step("5. Companies with pending consumptions")
companies = get_pending_company_ids()
print(f"  -> {companies}")

step(f"6. Creating consolidated FossBilling invoice for {COMPANY_ID}")
try:
    invoice_id = process_consumption_order(COMPANY_ID, items)
    print(f"  -> Invoice created! invoice_id = {invoice_id}")

    step("7. Clearing MySQL rows after invoicing")
    clear_company(COMPANY_ID)
    print(f"  -> Cleared pending rows for {COMPANY_ID}")

    remaining = get_items_for_company(COMPANY_ID)
    print(f"  -> Remaining rows: {len(remaining)}")
except Exception as e:
    print(f"  -> FossBilling call failed: {e}")
    print("  -> MySQL rows NOT cleared (invoice was not created)")

print("\nDone.")
