# Documentation: Consumption Order Flow

**Project:** Facturatie Microservice  
**Branch:** `feature/add-invoices-company`  
**Date:** 2026-04-28  
**Author:** Team Facturatie

---

## 1. Overview

The consumption order flow handles bar/kassa purchases made by employees of a company during an event. Each purchase arrives as a `consumption_order` XML message via RabbitMQ. The service stores all items per company in a MySQL table. At the end of the event, one consolidated FossBilling invoice is created per company containing all items from all employees, with each line showing which badge holder ordered it.

---

## 2. Architecture and message flow

```
Kassa / Bar terminal
   |
   | consumption_order (RabbitMQ: crm.to.facturatie)
   v
Facturatie service
   |-- Invalid XML          -->  Dead Letter Queue (facturatie.dlq)
   |-- XSD validation fail  -->  Dead Letter Queue (facturatie.dlq)
   |-- Duplicate message_id -->  Acknowledged, skipped
   |-- Not company-linked   -->  Dead Letter Queue (facturatie.dlq)
   |-- DB save failed       -->  Dead Letter Queue (facturatie.dlq)
   |
   | Items saved to MySQL (pending_consumptions)
   |
   v  [event-end trigger — pending from frontend/CRM team]
   |
   | For each company_id with pending items:
   |   process_consumption_order() --> FossBilling invoice created
   |   --> facturatie.to.mailing
   |   --> MySQL rows cleared
```

---

## 3. Why MySQL instead of direct invoicing

FossBilling has no API endpoint to add items to an existing invoice incrementally (`admin/invoice/item_add` does not exist in this version). Sending a new invoice per consumption message was rejected because it would create dozens of separate invoices per company per event.

**Solution:** Accumulate all consumption items in a MySQL table during the event. At event-end, read all items per company and create exactly one consolidated invoice with all lines.

---

## 4. Database: pending_consumptions table

Created automatically at service startup via `init_db()` in `src/services/consumption_store.py`.

```sql
CREATE TABLE IF NOT EXISTS pending_consumptions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    company_id  VARCHAR(100) NOT NULL,
    badge_id    VARCHAR(100) NOT NULL,
    master_uuid VARCHAR(36)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    price       DECIMAL(10,2) NOT NULL,
    quantity    INT          NOT NULL DEFAULT 1,
    vat_rate    VARCHAR(10),
    received_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_company_id  (company_id),
    INDEX idx_master_uuid (master_uuid)
)
```

**Column notes:**
- `company_id` — FossBilling company identifier (e.g. `FOSS-CUST-102`), used to group items per invoice
- `badge_id` / `master_uuid` — the employee's badge or QR-code ID from the XML; embedded in invoice line descriptions so the company can trace who ordered what
- `master_uuid` is NOT NULL and indexed, but NOT UNIQUE — one employee can have multiple rows (multiple purchases)

---

## 5. Files changed

### `src/services/consumption_store.py` *(new)*

Handles all MySQL interaction for pending consumptions.

| Function | Purpose |
|----------|---------|
| `init_db()` | Creates the `pending_consumptions` table if it does not exist |
| `save_items(company_id, badge_id, master_uuid, items)` | Inserts items from one consumption_order message |
| `get_pending_company_ids()` | Returns all company_ids that have uninvoiced items |
| `get_items_for_company(company_id)` | Returns all pending items formatted for FossBilling (badge_id embedded in title) |
| `clear_company(company_id)` | Deletes all rows for a company after the invoice is created |

### `src/services/fossbilling_api.py` *(modified)*

Added three new functions:

| Function | Purpose |
|----------|---------|
| `get_client_by_company_id(company_id)` | Looks up a FossBilling client by company_id; returns client_id or None |
| `get_unpaid_invoice_for_client(client_id)` | Returns the first unpaid invoice_id for a client, or None |
| `add_item_to_invoice(invoice_id, item)` | Stub kept for reference — endpoint does not exist in this FossBilling version |

Updated `process_consumption_order(company_id, items)`:
- Now always creates one new consolidated invoice via `_create_invoice`
- Called at event-end only, not per message
- Retries up to `MAX_RETRIES` (3) on transient API failures
- Raises `ValueError` immediately if `company_id` is not found in FossBilling

### `src/services/rabbitmq_receiver.py` *(modified)*

Updated the `consumption_order` handler in `process_message`:

**Before:** Called FossBilling API directly per message (not possible without item_add endpoint)  
**After:** Saves items to MySQL and acknowledges the message. FossBilling is only called at event-end.

```
consumption_order received
  → validate XML (XSD)
  → check for duplicate message_id
  → extract company_id, badge_id, items from XML
  → consumption_store.save_items(...)
  → basic_ack
```

### `src/main.py` *(modified)*

Added `init_db()` call at service startup so the `pending_consumptions` table is created before any messages are processed.

### `requirements.txt` *(modified)*

Added `mysql-connector-python==9.3.0` and pinned `lxml==6.0.2`.

### `docker-compose.yml` *(modified)*

- Removed obsolete `version: "3.9"` attribute
- Added `ports: "3306:3306"` to the MySQL service for local development access

### `.gitignore` *(modified)*

Added `.venv/` so the Python virtual environment directory cannot be accidentally committed.

---

## 6. Invoice line format

When `get_items_for_company()` prepares items for FossBilling, each line title includes the badge ID:

```
Coca-Cola (badge: BADGE-001)
Water (badge: BADGE-001)
Fanta (badge: BADGE-002)
```

This allows the company to trace each line back to the employee who ordered it without exposing personal data beyond what was already in the original message.

---

## 7. Tests

Test file: `tests/test_consumption_order.py`

| Class | Tests |
|-------|-------|
| `TestGetClientByCompanyId` | found, not found, passes company_id to API, raises on error |
| `TestGetUnpaidInvoiceForClient` | found, not found, ignores paid invoices, passes client_id |
| `TestAddItemToInvoice` | sends invoice_id, sends title/price/quantity, raises on error |
| `TestProcessConsumptionOrder` | creates invoice with all items, raises when company not found, retries on transient failure, raises after max retries |
| `TestProcessMessageConsumptionOrder` | happy path ack, company_id and badge_id saved, description format, multiple items, DB failure → DLQ, duplicate skipped, invalid XML → DLQ, XSD failure → DLQ |

Run with:
```bash
pytest -v -m "not integration"
```

---

## 8. Local end-to-end test

A manual test script is available at `scripts/test_consumption_flow.py`. It simulates two badge holders ordering items for the same company and verifies the full MySQL → FossBilling invoice flow.

**Requirements:**
- Docker Desktop running
- `docker compose up mysql -d`
- Wait ~15 seconds

**Run (PowerShell):**
```powershell
$env:MYSQL_HOST = "localhost"; python scripts/test_consumption_flow.py
```

Change `COMPANY_ID` at the top of the script to a company_id that exists in your FossBilling instance.

---

## 9. Pending: event-end handler

The trigger that fires at the end of an event to create invoices is **not yet implemented**. It is waiting on the frontend/CRM team to define the message type and format.

When the format is known, the handler will:

1. Call `get_pending_company_ids()` to find all companies with open items
2. For each company:
   - `get_items_for_company(company_id)` — fetch all accumulated items
   - `process_consumption_order(company_id, items)` — create FossBilling invoice
   - Send invoice to mailing queue (`facturatie.to.mailing`)
   - `clear_company(company_id)` — remove MySQL rows

---

## 10. Security notes

| Area | Status |
|------|--------|
| Credentials in code | None — all via `.env` and `os.getenv()` |
| `.env` in git | Excluded via `.gitignore` |
| SQL injection | Protected — all queries use parameterized `%s` placeholders |
| XML injection (incoming) | Protected — `defusedxml` used for all incoming XML parsing |
| XML injection (outgoing) | Protected — `ElementTree` auto-escapes all values |
| Virtual env in git | Protected — `.venv/` added to `.gitignore` |
