# Documentation: Consumption Order & Invoice Flow

**Project:** Facturatie Microservice  
**Date:** 2026-05-03  
**Author:** Team Facturatie

---

## 1. Overview

This flow handles bar/kassa purchases made by employees of a company during an event. Each purchase arrives as a `consumption_order` XML message via RabbitMQ. The service stores all items per company in a MySQL table.

Invoices can be triggered in two ways:

- **Direct** — via an `invoice_request` message for one specific `consumption_order`, which creates an immediate invoice for those items.
- **Deferred** — via an `event_ended` message at the end of the event, which collects all remaining uninvoiced items per company and creates one consolidated invoice per company.

---

## 2. Architecture and message flow

```
Kassa / CRM
   |
   | consumption_order (RabbitMQ: crm.to.facturatie)
   v
Facturatie service
   |-- Invalid XML           -->  Dead Letter Queue (facturatie.dlq)
   |-- XSD validation fail   -->  Dead Letter Queue (facturatie.dlq)
   |-- Duplicate message_id  -->  Acknowledged, skipped
   |-- No items in message   -->  Dead Letter Queue (facturatie.dlq)
   |-- DB save failed        -->  Dead Letter Queue (facturatie.dlq)
   |
   | Items saved to MySQL (pending_consumptions)
   | Indexed on consumption_order_id = message_id of the consumption_order
   |
   +--[Path A: invoice_request received]-----------------------------+
   |                                                                 |
   | invoice_request (RabbitMQ: crm.to.facturatie)                  |
   | correlation_id = message_id of the consumption_order           |
   |   --> update_meta_by_correlation_id() (company_name, email)    |
   |   --> get_items_by_correlation_id()                            |
   |   --> process_consumption_order() --> FossBilling invoice      |
   |   --> facturatie.to.mailing                                    |
   |   --> clear_by_ids() (only the matched rows)                   |
   |   |-- No items found    -->  Acknowledged, skipped             |
   |   |-- FossBilling fail  -->  Dead Letter Queue                 |
   |                                                                 |
   +--[Path B: event_ended received]--------------------------------+
   |
   | event_ended (RabbitMQ: crm.to.facturatie)
   |   For each company_id with remaining pending items:
   |   --> get_items_for_company()
   |   --> process_consumption_order() --> FossBilling invoice
   |   --> facturatie.to.mailing
   |   --> clear_by_ids()
   |   |-- FossBilling fail  -->  Dead Letter Queue
```

---

## 3. XML message formats

### consumption_order (incoming)

Sent by Kassa (via CRM passthrough) when a purchase is made. One message per badge holder per transaction.

```xml
<message>
  <header>
    <message_id>78729c4c-a258-4c35-959c-a1d5805881a1</message_id>
    <type>consumption_order</type>
    <source>crm</source>
    <timestamp>2026-05-01T20:00:00Z</timestamp>
    <version>2.0</version>
  </header>
  <body>
    <is_anonymous>false</is_anonymous>
    <customer>
      <id>bedrijf-nv-001</id>
      <user_id>BADGE-001</user_id>
      <type>company</type>
      <email>jan.peeters@bedrijf.com</email>
    </customer>
    <items>
      <item>
        <id>LINE-0001</id>
        <sku>SKU-001</sku>
        <description>Coca-Cola</description>
        <quantity>2</quantity>
        <unit_price currency="eur">2.50</unit_price>
        <vat_rate>21</vat_rate>
        <total_amount currency="eur">5.00</total_amount>
      </item>
    </items>
  </body>
</message>
```

**Key fields:**
- `header/message_id` — stored as `consumption_order_id` in MySQL; used as the linking key
- `body/customer/id` — `company_id`, used to group items per company
- `body/customer/user_id` — employee badge ID; appears on the invoice line
- `body/items/item` — consumed items with price and VAT

### invoice_request (incoming)

Sent by CRM when an invoice is requested for one specific `consumption_order`. The `correlation_id` in the header references the `message_id` of the corresponding `consumption_order`.

```xml
<message>
  <header>
    <message_id>e5bc986d-3b15-4525-835c-e6d733e69401</message_id>
    <type>invoice_request</type>
    <source>crm</source>
    <timestamp>2026-05-01T20:01:00Z</timestamp>
    <version>2.0</version>
    <correlation_id>78729c4c-a258-4c35-959c-a1d5805881a1</correlation_id>
  </header>
  <body>
    <user_id>BADGE-001</user_id>
    <invoice_data>
      <first_name>Jan</first_name>
      <last_name>Peeters</last_name>
      <email>jan.peeters@bedrijf.com</email>
      <address>
        <street>Teststraat</street>
        <number>1</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>BE</country>
      </address>
      <company_name>Bedrijf NV</company_name>
      <vat_number>BE0123456789</vat_number>
    </invoice_data>
  </body>
</message>
```

**Key fields:**
- `header/correlation_id` — references the `message_id` of the `consumption_order` for which the invoice is requested
- `body/invoice_data/company_name` — stored as the billing name and used as the `company_id` key
- `body/invoice_data/email` — receives the invoice notification via mailing

### event_ended (incoming)

Sent by the Frontend at the end of the event. Triggers invoicing for all remaining items that have not received an `invoice_request`.

```xml
<message>
  <header>
    <message_id>d112ac7f-5397-4104-95c7-a4043a1bbec7</message_id>
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

## 4. Why MySQL

FossBilling has no API endpoint for incrementally adding items to an existing invoice. Creating a new invoice per message would result in dozens of separate invoices per company per event.

Items are therefore stored in MySQL during the event. On `invoice_request` or `event_ended`, the items are retrieved and bundled into one invoice per company.

---

## 5. Database tabellen

Both tables are created automatically on service startup via `init_db()` in `consumption_store.py`. Missing columns are added automatically via migrations.

### pending_consumptions

Stores all consumption items that have not yet been invoiced.

```sql
CREATE TABLE IF NOT EXISTS pending_consumptions (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    consumption_order_id  VARCHAR(100) NOT NULL,
    company_id            VARCHAR(100) NOT NULL DEFAULT '',
    company_name          VARCHAR(255) NOT NULL DEFAULT '',
    email                 VARCHAR(255) NOT NULL DEFAULT '',
    badge_id              VARCHAR(100) NOT NULL DEFAULT '',
    master_uuid           VARCHAR(36)  NOT NULL DEFAULT '',
    description           VARCHAR(255) NOT NULL,
    price                 DECIMAL(10,2) NOT NULL,
    quantity              INT          NOT NULL DEFAULT 1,
    vat_rate              VARCHAR(10),
    received_at           DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_company_id           (company_id),
    INDEX idx_consumption_order_id (consumption_order_id)
)
```

**Column notes:**
- `consumption_order_id` — the `message_id` of the `consumption_order`; used as the linking key with `invoice_request` via `correlation_id`
- `company_id` — `customer/id` from the `consumption_order`; groups items per company for `event_ended`
- `company_name` / `email` — filled in (or updated) when the matching `invoice_request` arrives
- `badge_id` — `customer/user_id` from the `consumption_order`; appears on the invoice line so the company can trace who ordered what

### company_accounts

Stores the mapping between a `company_id` and its FossBilling billing account.

```sql
CREATE TABLE IF NOT EXISTS company_accounts (
    company_id            VARCHAR(100) PRIMARY KEY,
    fossbilling_client_id INT NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

Each company gets a dedicated billing account with a generated email address (`billing.<company_id>@facturatie.be`). This ensures the consolidated invoice always goes to the same account, regardless of how many employees of that company are already registered as FossBilling clients.

---

## 6. Modified files

### `src/services/consumption_store.py`

| Function | Purpose |
|---|---|
| `init_db()` | Creates `pending_consumptions` and `company_accounts` tables on startup; runs migrations |
| `save_items(company_id, badge_id, master_uuid, items, email, company_name, consumption_order_id)` | Stores items from one `consumption_order` message |
| `get_items_by_correlation_id(correlation_id)` | Retrieves items by `consumption_order_id`; used by the `invoice_request` handler |
| `update_meta_by_correlation_id(correlation_id, company_name, email)` | Updates `company_name` and `email` on the matching rows when an `invoice_request` arrives |
| `get_pending_company_ids()` | Returns all `company_id`s with uninvoiced items; used by `event_ended` |
| `get_company_meta(company_id)` | Returns `email` and `company_name` for a company; used for mailing in `event_ended` |
| `get_items_for_company(company_id)` | Returns `(items, row_ids)` for all open items of a company |
| `clear_by_ids(row_ids)` | Deletes specific processed rows by database ID |
| `get_company_client_id(company_id)` | Returns the cached FossBilling billing `client_id`, or `None` |
| `save_company_client_id(company_id, client_id)` | Stores the mapping between `company_id` and billing account |

### `src/services/fossbilling_api.py`

| Function | Purpose |
|---|---|
| `_billing_email(company_id)` | Generates a deterministic billing email address for a company |
| `_get_or_create_billing_client(company_id, company_name)` | Looks up the billing account in MySQL; creates it in FossBilling if it does not exist |
| `process_consumption_order(company_id, items, company_name)` | Creates one consolidated invoice on the company billing account; retries up to 3 times |

### `src/services/rabbitmq_receiver.py`

| Handler | Behaviour |
|---|---|
| `consumption_order` | Reads `customer/id` as `company_id`, `customer/user_id` as `badge_id`, items from `body/items`; stores everything via `save_items` with `consumption_order_id=msg_id` |
| `invoice_request` | Reads `correlation_id` from header; updates meta; retrieves items via `get_items_by_correlation_id`; creates invoice; sends mailing; deletes processed rows |
| `event_ended` | Retrieves all remaining `company_id`s; creates one invoice per company for all open items; sends mailing; deletes processed rows |

---

## 7. Invoice line format

Items are created in FossBilling with the badge ID in the title so the company can trace who ordered what:

```
Coca-Cola (badge: BADGE-001)
Water (badge: BADGE-001)
Fanta (badge: BADGE-002)
```

---

## 8. Local testing

### Requirements

| What | Command |
|---|---|
| MySQL running | `docker compose up mysql -d` |
| RabbitMQ running | `docker compose up rabbitmq -d` |
| Mock identity service | `python -m scripts.mock_identity_service` (Terminal 1) |
| Receiver running | `python -m src.main` (Terminal 2) |
| FossBilling reachable | verify `.env` for `BILLING_API_URL` and `BILLING_API_TOKEN` |

### Test script

The test script simulates the full flow in three steps:

```bash
python -m scripts.send_test_consumption_flow
```

**Step 1** — Three `consumption_order` messages are sent (BADGE-001, BADGE-002, BADGE-003). Items are saved to MySQL.

**Step 2** — `invoice_request` for BADGE-001 and BADGE-003. Both orders are immediately invoiced via FossBilling. BADGE-002 intentionally receives no `invoice_request`.

**Step 3** — `event_ended`. The remaining items of BADGE-002 are picked up and invoiced.

### Expected result

**FossBilling** — 3 invoices:
- Bedrijf NV billing account → Coca-Cola + Water (BADGE-001, via invoice_request)
- Tech Corp billing account → Koffie + Cola (BADGE-003, via invoice_request)
- Bedrijf NV billing account → Fanta (BADGE-002, via event_ended)

**RabbitMQ `facturatie.to.mailing`** — 3 `send_mailing` messages.

**MySQL `pending_consumptions`** — empty after processing.

### Clean up database between test runs

```bash
docker exec -it <mysql-container-name> mysql -u fossbilling -p<password> fossbilling -e "DELETE FROM pending_consumptions; DELETE FROM company_accounts;"
```

---

## 9. Security

| Area | Status |
|---|---|
| Credentials in code | None — all via `.env` and `os.getenv()` |
| `.env` in git | Excluded via `.gitignore` |
| SQL injection | Protected — all queries use parameterised `%s` placeholders |
| XML injection (incoming) | Protected — `defusedxml` used for all incoming XML parsing |
| XML injection (outgoing) | Protected — `ElementTree` automatically escapes all values |
| Virtual env in git | Protected — `.venv/` added to `.gitignore` |
