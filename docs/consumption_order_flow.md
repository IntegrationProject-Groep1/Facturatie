# Documentation: Invoice Request Flow (Consumption Orders)

**Project:** Facturatie Microservice  
**Branch:** `feature/add-invoices-company`  
**Date:** 2026-04-28  
**Author:** Team Facturatie

---

## 1. Overview

The invoice request flow handles bar/kassa purchases made by employees of a company during an event. Each purchase arrives as an `invoice_request` XML message via RabbitMQ. The service stores all items per company in a MySQL table. At the end of the event (triggered by an `event_ended` message), one consolidated FossBilling invoice is created per company containing all items from all employees, with each line showing which badge holder ordered it.

---

## 2. Architecture and message flow

```
CRM / Kassa terminal
   |
   | invoice_request (RabbitMQ: crm.to.facturatie)
   v
Facturatie service
   |-- Invalid XML           -->  Dead Letter Queue (facturatie.dlq)
   |-- XSD validation fail   -->  Dead Letter Queue (facturatie.dlq)
   |-- Duplicate message_id  -->  Acknowledged, skipped
   |-- Not company-linked    -->  Dead Letter Queue (facturatie.dlq)
   |-- DB save failed        -->  Dead Letter Queue (facturatie.dlq)
   |
   | Items saved to MySQL (pending_consumptions)
   |
   v  [event_ended received from frontend]
   |
   | For each company_id with pending items:
   |   process_consumption_order() --> FossBilling invoice created
   |   --> facturatie.to.mailing
   |   --> MySQL rows cleared
   |-- FossBilling failure   -->  Dead Letter Queue (facturatie.dlq)
```

---

## 3. XML message formats

### invoice_request (incoming)

```xml
<message>
  <header>
    <message_id>inv-crm-12345</message_id>
    <master_uuid>01890a5d-ac96-7ab2-80e2-4536629c90de</master_uuid>
    <version>2.0</version>
    <type>invoice_request</type>
    <timestamp>2026-03-29T18:30:00Z</timestamp>
    <source>crm</source>
  </header>
  <body>
    <customer>
      <customer_id>CRM-USR-999</customer_id>
      <email>jan@example.com</email>
      <first_name>Jan</first_name>
      <last_name>Peeters</last_name>
      <is_company_linked>true</is_company_linked>
      <company_id>CRM-COMP-888</company_id>
      <company_name>Peeters NV</company_name>
      <address>...</address>
    </customer>
    <invoice>
      <description>Inschrijving Event</description>
      <amount currency="eur">150.00</amount>
      <due_date>2026-05-01</due_date>
    </invoice>
    <items>
      <item>
        <description>Inschrijving VIP</description>
        <quantity>1</quantity>
        <unit_price currency="eur">150.00</unit_price>
        <vat_rate>21</vat_rate>
      </item>
    </items>
  </body>
</message>
```

**Key fields:**
- `header/master_uuid` — unique identifier for the employee, stored in MySQL
- `body/customer/customer_id` — badge/QR-code ID of the employee
- `body/customer/is_company_linked` — must be `true` for company invoicing
- `body/customer/company_id` — used to group all items per company

### event_ended (incoming)

```xml
<message>
  <header>
    <message_id>...</message_id>
    <version>2.0</version>
    <type>event_ended</type>
    <timestamp>2026-05-01T22:00:00Z</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <session_id>SESSION-001</session_id>
    <ended_at>2026-05-01T22:00:00Z</ended_at>
  </body>
</message>
```

---

## 4. Why MySQL instead of direct invoicing

FossBilling has no API endpoint to add items to an existing invoice incrementally (`admin/invoice/item_add` does not exist in this version). Sending a new invoice per message was rejected because it would create dozens of separate invoices per company per event.

**Solution:** Accumulate all consumption items in a MySQL table during the event. When `event_ended` arrives, read all items per company and create exactly one consolidated invoice with all lines.

---

## 5. Database: pending_consumptions table

Created automatically at service startup via `init_db()` in `src/services/consumption_store.py`.

```sql
CREATE TABLE IF NOT EXISTS pending_consumptions (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    company_id   VARCHAR(100) NOT NULL,
    company_name VARCHAR(255) NOT NULL DEFAULT '',
    email        VARCHAR(255) NOT NULL DEFAULT '',
    badge_id     VARCHAR(100) NOT NULL,
    master_uuid  VARCHAR(36)  NOT NULL,
    description  VARCHAR(255) NOT NULL,
    price        DECIMAL(10,2) NOT NULL,
    quantity     INT          NOT NULL DEFAULT 1,
    vat_rate     VARCHAR(10),
    received_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_company_id  (company_id),
    INDEX idx_master_uuid (master_uuid)
)
```

**Column notes:**
- `company_id` — FossBilling company identifier, used to group items per invoice
- `company_name` / `email` — stored from the `invoice_request` message for use in the mailing notification
- `badge_id` — the employee's badge/QR-code ID (`customer_id` from XML)
- `master_uuid` — from `header/master_uuid`; embedded in invoice line descriptions so the company can trace who ordered what
- `master_uuid` is NOT NULL and indexed, but NOT UNIQUE — one employee can have multiple rows

---

## 6. Files changed

### `src/services/consumption_store.py` *(new)*

Handles all MySQL interaction for pending consumptions.

| Function | Purpose |
|----------|---------|
| `init_db()` | Creates the `pending_consumptions` table if it does not exist |
| `save_items(company_id, badge_id, master_uuid, items, email, company_name)` | Inserts items from one `invoice_request` message |
| `get_pending_company_ids()` | Returns all company_ids that have uninvoiced items |
| `get_company_meta(company_id)` | Returns `email` and `company_name` for a company |
| `get_items_for_company(company_id)` | Returns all pending items formatted for FossBilling (badge_id embedded in title) |
| `clear_company(company_id)` | Deletes all rows for a company after the invoice is created |

### `src/services/fossbilling_api.py` *(modified)*

| Function | Purpose |
|----------|---------|
| `get_client_by_company_id(company_id)` | Exact-match lookup by company_id; returns client_id or None |
| `get_unpaid_invoice_for_client(client_id)` | Returns the first unpaid invoice_id for a client, or None |
| `process_consumption_order(company_id, items)` | Creates one consolidated invoice at event-end; retries up to 3x |

### `src/services/rabbitmq_receiver.py` *(modified)*

| Handler | Behaviour |
|---------|-----------|
| `invoice_request` | Reads `master_uuid` from header, `customer_id` as badge_id; saves items + email + company_name to MySQL; acks |
| `event_ended` | For each company with pending items: creates FossBilling invoice, sends to mailing, clears MySQL |
| `consumption_order` | **Removed** — replaced by `invoice_request` |

### `src/services/xsd/invoice_request.xsd` *(modified)*

Updated `CustomerType` to use `is_company_linked` (field name confirmed by CRM team).

### `src/main.py` *(modified)*

Added `init_db()` call at service startup.

### `requirements.txt` *(modified)*

Added `mysql-connector-python==9.3.0` and pinned `lxml==6.0.2`.

### `docker-compose.yml` *(modified)*

- Removed obsolete `version: "3.9"` attribute
- Added `ports: "3306:3306"` for local development access

### `.gitignore` *(modified)*

Added `.venv/` to prevent accidental commits of the virtual environment.

---

## 7. Invoice line format

When `get_items_for_company()` prepares items for FossBilling, each line title includes the badge ID:

```
Coca-Cola (badge: BADGE-001)
Water (badge: BADGE-001)
Fanta (badge: BADGE-002)
```

This allows the company to trace each line back to the employee who ordered it.

---

## 8. Tests

Test file: `tests/test_consumption_order.py` — 28 tests total

| Class | Tests |
|-------|-------|
| `TestGetClientByCompanyId` | found (exact match), not found, passes company_id, raises on error |
| `TestGetUnpaidInvoiceForClient` | found, not found, ignores paid, passes client_id |
| `TestAddItemToInvoice` | sends invoice_id, sends title/price/quantity, raises on error |
| `TestProcessConsumptionOrder` | creates invoice, raises when company not found, retries on failure, raises after max retries |
| `TestProcessMessageConsumptionOrder` | happy path ack, company_id/badge_id saved, master_uuid from header, description format, multiple items, DB failure → DLQ, duplicate skipped, invalid XML → DLQ, XSD failure → DLQ |
| `TestProcessMessageEventEnded` | happy path ack, no pending → ack immediately, FossBilling failure → DLQ, clear_company called after invoice |

Run with:
```bash
pytest -v -m "not integration"
```

---

## 9. Setup for teammates

If you pulled this branch and want to run or test the service locally:

**1. Copy the environment file**
```bash
cp .env.example .env
```
Fill in the correct values for `MYSQL_USER`, `MYSQL_PASSWORD`, `RABBITMQ_HOST`, etc.

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Start MySQL**
```bash
docker compose up mysql -d
```

**4. The database table is created automatically**
The `pending_consumptions` table is created on service startup — no manual SQL needed. Just start the service:
```bash
python -m src.main
```

---

## 10. Local end-to-end test

An integration test is available at `tests/test_consumption_e2e.py`. It simulates two badge holders ordering items for the same company and verifies the full MySQL → FossBilling invoice flow. FossBilling is mocked — only MySQL needs to be running.

**Requirements:**
- Docker Desktop running
- `docker compose up mysql -d`
- Wait ~15 seconds

**Run (PowerShell):**
```powershell
$env:MYSQL_HOST = "localhost"; pytest -m integration -v
```

The test:
1. Drops and recreates the `pending_consumptions` table
2. Saves items for BADGE-001 (Coca-Cola, Water) and BADGE-002 (Fanta) under the same company
3. Triggers the `event_ended` handler with FossBilling mocked
4. Asserts that one ACK and one mailing notification are sent

---

## 11. Security notes

| Area | Status |
|------|--------|
| Credentials in code | None — all via `.env` and `os.getenv()` |
| `.env` in git | Excluded via `.gitignore` |
| SQL injection | Protected — all queries use parameterized `%s` placeholders |
| XML injection (incoming) | Protected — `defusedxml` used for all incoming XML parsing |
| XML injection (outgoing) | Protected — `ElementTree` auto-escapes all values |
| Virtual env in git | Protected — `.venv/` added to `.gitignore` |
