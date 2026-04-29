# Documentation: Invoice Request Flow (Consumption Orders)

**Project:** Facturatie Microservice  
**Branch:** `feature/add-invoices-company`  
**Date:** 2026-04-29  
**Author:** Team Facturatie

---

## 1. Overview

The invoice request flow handles bar/kassa purchases made by employees of a company during an event. Each purchase arrives as an `invoice_request` XML message via RabbitMQ. The service stores all items per company in a MySQL table. At the end of the event (triggered by an `event_ended` message), one consolidated FossBilling invoice is created per company containing all items from all employees, with each line showing which badge holder ordered it.

---

## 2. Architecture and message flow

```
CRM / POS terminal
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
      <description>Event Registration</description>
      <amount currency="eur">150.00</amount>
      <due_date>2026-05-01</due_date>
    </invoice>
    <items>
      <item>
        <description>VIP Registration</description>
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

## 5. Database tables

Both tables are created automatically at service startup via `init_db()` in `src/services/consumption_store.py`.

### pending_consumptions

Stores all consumption items received during an event, grouped per company.

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

### company_accounts

Stores the mapping between a `company_id` and the dedicated FossBilling billing account for that company.

```sql
CREATE TABLE IF NOT EXISTS company_accounts (
    company_id            VARCHAR(100) PRIMARY KEY,
    fossbilling_client_id INT NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

**Why this table exists:** When multiple employees of the same company are registered as FossBilling clients, a simple search by company name can return any of them. This table ensures the consolidated invoice always goes to one dedicated billing account per company, regardless of how many employee accounts exist.

The billing account is created automatically the first time `event_ended` fires for a company. It has a generated email (`billing.<company_id>@facturatie.be`) and is never overwritten by employee registrations.

---

## 6. Files changed

### `src/services/consumption_store.py` *(new)*

Handles all MySQL interaction for pending consumptions and company billing accounts.

| Function | Purpose |
|----------|---------|
| `init_db()` | Creates `pending_consumptions` and `company_accounts` tables if they do not exist |
| `save_items(company_id, badge_id, master_uuid, items, email, company_name)` | Inserts items from one `invoice_request` message |
| `get_pending_company_ids()` | Returns all company_ids that have uninvoiced items |
| `get_company_meta(company_id)` | Returns `email` and `company_name` for a company |
| `get_items_for_company(company_id)` | Returns `(items, row_ids)` — items formatted for FossBilling, row_ids for safe atomic deletion |
| `clear_by_ids(row_ids)` | Deletes only the specific processed rows by their IDs (prevents race conditions) |
| `get_company_client_id(company_id)` | Returns the stored FossBilling billing `client_id` for a company, or `None` |
| `save_company_client_id(company_id, client_id)` | Saves or updates the billing account mapping for a company |

### `src/services/fossbilling_api.py` *(modified)*

| Function | Purpose |
|----------|---------|
| `_billing_email(company_id)` | Generates a consistent billing email for a company account (e.g. `billing.bedrijf_nv@facturatie.be`) |
| `_get_or_create_billing_client(company_id, company_name)` | Looks up the company billing account from MySQL; creates it in FossBilling if it does not exist yet |
| `process_consumption_order(company_id, items, company_name)` | Creates one consolidated invoice on the company billing account; retries up to 3x |

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

**4. Tables are created automatically**
Both `pending_consumptions` and `company_accounts` are created on service startup — no manual SQL needed. Just start the service:
```bash
python -m src.main
```

**5. Test the flow**
```bash
python scripts/send_invoice_request.py
python scripts/send_event_ended.py
```

Check FossBilling — you should see one invoice per company on the dedicated billing account (`billing.<company>@facturatie.be`).

---

## 10. End-to-end test via RabbitMQ

This test uses the real service, real MySQL, and real FossBilling. Two scripts simulate the full flow.

### Requirements

| What | Command |
|------|---------|
| MySQL running | `docker compose up mysql -d` |
| Service running | `python -m src.main` (Terminal 1) |
| FossBilling reachable | check `.env` for `BILLING_API_URL` and `BILLING_API_TOKEN` |
| RabbitMQ running | queue `crm.to.facturatie` must exist |

### Steps

**1. Clean up old test data (optional but recommended)**

Delete any leftover rows in MySQL:
```bash
docker exec -it facturatie-mysql-1 mysql -u fossbilling -pfossbilling fossbilling
```
```sql
DELETE FROM pending_consumptions;
DELETE FROM company_accounts;
EXIT;
```

Delete old test invoices in FossBilling via the admin interface.

**2. Send consumption items for two companies**
```bash
python scripts/send_invoice_request.py
```
This sends three `invoice_request` messages:
- **BADGE-001** (Jan Peeters / Bedrijf NV): Coca-Cola + Water
- **BADGE-002** (Marie Janssen / Bedrijf NV): Fanta
- **BADGE-003** (Piet Janssen / Tech Corp): Coffee
- **BADGE-004** (Sara Jan / Tech Corp): Cola + Fanta

Items are saved in MySQL `pending_consumptions`. No FossBilling invoice is created yet.

**3. Trigger end-of-event invoicing**
```bash
python scripts/send_event_ended.py
```

The service will:
1. Find all companies with pending items
2. For each company: look up or create a dedicated billing account in FossBilling
3. Create one consolidated invoice per company
4. Clear the processed rows from MySQL
5. Send an invoice notification to `facturatie.to.mailing`

**4. Verify in FossBilling**

Go to **Invoices** in the FossBilling admin. You should see:
- **Bedrijf NV billing account** → 1 invoice with Coca-Cola + Water + Fanta (all BADGE-001 and BADGE-002 lines)
- **Tech Corp billing account** → 1 invoice with Koffie + Cola + Fanta (all BADGE-003 and BADGE-004 lines)

The billing accounts have emails like `billing.bedrijf_nv@facturatie.be` and are separate from the individual employee accounts.

### Automated integration test (MySQL only, FossBilling mocked)

```powershell
$env:MYSQL_HOST = "localhost"; pytest -m integration -v
```

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
