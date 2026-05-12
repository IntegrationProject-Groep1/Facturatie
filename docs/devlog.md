# Development Log

## 2026-03-29 — RabbitMQ Sender & Receiver

### What was built

#### `src/services/rabbitmq_sender.py`
- Implemented `get_connection()` using `.env` variables; port cast to `int` as required by pika.
- Implemented `build_consumption_order_xml()` to generate a valid `consumption_order` XML message with:
  - `<message_id>` (UUID v4) instead of `<id>` per V3 standard
  - `<type>consumption_order</type>` in lowercase snake_case
  - `<unit_price currency="eur">` replacing the old `<price_unit currency="EUR">`
  - `<country>be</country>` in lowercase (ISO-3166)
  - Dynamic company fields only included when `is_company_linked=True`
- Implemented `send_message()` to publish XML to a durable queue with `delivery_mode=2` (persistent).

#### `src/services/rabbitmq_receiver.py`
- Validates messages against the **XML Naming Standard** (all lowercase snake_case).
- `validate_message(root, seen_ids)` checks:
  - `<message_id>` present (was `<id>` in V2)
  - `<version>` equals `2.0`
  - `<type>` is a known lowercase type: `consumption_order`, `payment_registered`, `heartbeat`
  - `<timestamp>` present and matches ISO-8601 UTC format (`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`)
  - `<source>` present
  - For `consumption_order`: VAT rate must be 6, 12, or 21; `company_id` + `company_name` required when `is_company_linked=true`
  - For `payment_registered`: `correlation_id` required
  - Optional `seen_ids` parameter enables duplicate detection
- Error log messages follow V3 snake_case standard:
  - `WARN: missing_required_field: <field>`
  - `ERROR: unknown_message_type`
  - `ERROR: invalid_enum_case` (type known but not lowercase)
  - `ERROR: invalid_iso8601_timestamp`
  - `ERROR: vat_rate must be 6, 12 or 21`
  - `WARN: duplicate_message_id:` — message acknowledged and ignored, NOT sent to DLQ
- `send_to_dlq()` forwards all other invalid messages to `facturatie.dlq`.
- `process_message()` callback: parse XML → check duplicate → validate → DLQ or ACK.

### Tests (`tests/test_validate_message.py`) — 22 tests

| Test | What it checks |
|---|---|
| `test_valid_consumption_order` | Fully valid `consumption_order` returns no errors |
| `test_invalid_vat_rate_returns_error` | VAT rate 99 is rejected |
| `test_valid_vat_rate_6/12/21` | All three allowed VAT rates pass |
| `test_missing_company_id_when_company_linked` | `company_id` required when linked |
| `test_missing_company_name_when_company_linked` | `company_name` required when linked |
| `test_valid_company_linked` | Both fields present — no error |
| `test_missing_message_id` | Empty `<message_id>` returns `missing_required_field` |
| `test_missing_timestamp` | Empty `<timestamp>` returns `missing_required_field` |
| `test_invalid_timestamp_format` | Non-ISO-8601 timestamp returns `invalid_iso8601_timestamp` |
| `test_valid_timestamp_format` | Correct timestamp passes |
| `test_missing_source` | Empty `<source>` returns `missing_required_field` |
| `test_unknown_message_type` | Unknown type returns `unknown_message_type` |
| `test_uppercase_message_type_returns_enum_case_error` | `CONSUMPTION_ORDER` returns `invalid_enum_case` |
| `test_payment_registered_missing_correlation_id` | Missing `correlation_id` returns error |
| `test_payment_registered_with_correlation_id` | Present `correlation_id` passes |
| `test_invalid_version_returns_error` | Version `1.0` rejected |
| `test_valid_version` | Version `2.0` passes |
| `test_duplicate_message_is_flagged` | Known `message_id` returns `duplicate_message_id` |
| `test_unique_message_is_not_flagged` | New `message_id` is not flagged |

### Project structure
```
Facturatie/
├── conftest.py              # Adds project root to sys.path for tests
├── requirements.txt         # pika, python-dotenv
├── .github/workflows/
│   └── ci.yml               # Runs flake8 + pytest on push to main/dev
├── src/
│   ├── __init__.py
│   └── services/
│       ├── __init__.py
│       ├── rabbitmq_sender.py
│       └── rabbitmq_receiver.py
└── tests/
    ├── __init__.py
    └── test_validate_message.py
```

### Standards referenced
- **XML Naming Standard** — Desideriushogeschool (snake_case lowercase for all field names and enum values)
- **ISO-8601 UTC** — timestamp format `YYYY-MM-DDTHH:MM:SSZ`
- **ISO-3166 alpha-2** — country codes in lowercase (`be`, `nl`)
- **ISO-4217** — currency codes in lowercase (`eur`)

---

## 2026-03-31 — Flow 4: Annulering inschrijving (Cancel Invoice via Credit Note)

### Goal
When a person or company cancels their registration on the website, the invoicing system must:
1. Receive the cancellation message from RabbitMQ
2. Validate it
3. Cancel the invoice in FossBilling (mark it as "Cancelled")
4. Notify the CRM system so it can process the cancellation on their end
5. If anything fails, route the message to the Dead Letter Queue for manual follow-up

### What was built

#### `src/services/fossbilling_client.py`
- Calls the FossBilling REST API to cancel an invoice
- Uses `POST /api/admin/invoice/update` with `status=cancelled`
- Authentication via Basic Auth using `BILLING_API_USERNAME` and `BILLING_API_TOKEN` from `.env`
- Returns `True` on success, `False` on any connection error or unexpected API response
- All credentials come from `.env` — no secrets in code

#### `src/services/crm_publisher.py`
- Builds and sends an `invoice_cancelled` XML message to the `crm` queue on RabbitMQ
- Includes `invoice_id`, `customer_id` and `correlation_id` from the original message
- Follows the same XML structure (header + body) as all other messages in the project
- Messages are sent as persistent (survive a RabbitMQ restart)

#### `src/services/invoice_cancellation_receiver.py`
- Listens on the `facturatie.incoming` queue
- Filters out all messages that are not of type `invoice_cancelled` — those are simply passed through without processing
- Validates the message using `validate_invoice_cancelled()`:
  - `message_id` must be present
  - `version` must be `2.0`
  - `type` must be exactly `invoice_cancelled`
  - `timestamp` must follow ISO-8601 UTC format
  - `source` must be present
  - `correlation_id` is required for this message type
  - `invoice_id` must be present
  - `customer_id` must be present
  - `reason` is optional — logged for audit trail if present
- If validation fails → message is sent to `facturatie.dlq` with a description of the errors
- If FossBilling API call fails → message is sent to `facturatie.dlq` with error details
- If everything succeeds → CRM is notified and the message is marked as processed

### Tests (`tests/test_invoice_cancellation.py`) — 7 tests

| Test | What it checks |
|---|---|
| `test_valid_message_has_no_errors` | A correct message passes validation with no errors |
| `test_missing_invoice_id_returns_error` | Validation catches a missing `invoice_id` |
| `test_missing_customer_id_returns_error` | Validation catches a missing `customer_id` |
| `test_missing_correlation_id_returns_error` | Validation catches a missing `correlation_id` |
| `test_valid_message_with_reason_has_no_errors` | Optional `reason` field does not cause errors |
| `test_fossbilling_failure_sends_to_dlq` | If FossBilling returns an error, the message goes to DLQ |
| `test_successful_flow_sends_to_crm` | If everything works, CRM receives the `invoice_cancelled` message |

### End-to-end test result
- Sent a test `invoice_cancelled` message via `send_test_cancellation.py` for invoice ID `3` (FOSS00003)
- Receiver picked up the message, called FossBilling, and published to the `crm` queue
- FossBilling confirmed: FOSS00003 status changed from **"Non payé"** to **"Cancelled"**
- RabbitMQ confirmed: `crm` queue received the message, `facturatie.incoming` was empty after processing

### Flow summary
```
Website → RabbitMQ (facturatie.incoming)
              ↓
    invoice_cancellation_receiver
              ↓ validate
         valid? ──No──→ facturatie.dlq
              ↓ Yes
    FossBilling API (status=cancelled)
              ↓
         success? ──No──→ facturatie.dlq
              ↓ Yes
         crm queue (invoice_cancelled)
              ↓
    CRM processes the cancellation
```

### Updated project structure
```
Facturatie/
├── scripts/
│   └── send_test_cancellation.py    # Manual test script (not for production)
├── src/
│   └── services/
│       ├── rabbitmq_sender.py
│       ├── rabbitmq_receiver.py
│       ├── invoice_cancellation_receiver.py
│       ├── fossbilling_client.py
│       └── crm_publisher.py
└── tests/
    ├── test_validate_message.py
    └── test_invoice_cancellation.py
```

---

## 2026-05-12 — Automated Communication Flows 2 & 3 + Lint Cleanup

### Goal

Fully automate all outgoing communication from the Facturatie service — removing the need for manual triggers. Three flows were reviewed and implemented:

- **Flow 2 (CRM - Status):** Send `invoice_status` to CRM at every invoice status change.
- **Flow 3 (CRM - Payment):** Confirm payment to CRM the moment an invoice reaches `paid` status.
- **Lint cleanup:** Resolve all Flake8 warnings across scripts and test files.

---

### Flow 2 — `invoice_status` to CRM on every status change

**Queue:** `crm.incoming`  
**Message type:** `invoice_status`  
**XSD:** `src/services/xsd/invoice_status.xsd`

A `publish_invoice_status()` call was added at every point in `rabbitmq_receiver.py` where an invoice changes status:

| Trigger | Status sent | Amount source |
|---|---|---|
| `new_registration` invoice created | `sent` | `registration_fee` from incoming message |
| `invoice_request` invoice created | `sent` | computed from consumption items |
| `event_ended` invoice created per company | `sent` | computed from consumption items |
| `payment_registered` payment processed | `paid` | `amount_paid` from incoming message |
| `invoice_cancelled` — unpaid registration | `cancelled` | `invoice.total` from FossBilling |
| `invoice_cancelled` — paid registration (credit note) | `cancelled` | `invoice.total` from FossBilling |

**New functions in `src/services/rabbitmq_sender.py`:**

- `build_invoice_status_xml(invoice_id, identity_uuid, status, amount, correlation_id)` — builds and validates the XML against the invoice_status XSD.
- `publish_invoice_status(...)` — sends the message to `crm.incoming`.

All calls are wrapped in `try/except` — a failure does not block the main flow (logged as warning).

---

### Flow 3 — `payment_registered` confirmation to CRM

**Queue:** `crm.incoming`  
**Message type:** `payment_registered` (source: `facturatie`)

This flow was already implemented via `build_payment_confirmed_xml()` in `rabbitmq_sender.py`. One gap was identified and fixed:

- **Added `correlation_id` parameter** to `build_payment_confirmed_xml()`. The outgoing message now includes a `correlation_id` linking it back to the incoming `payment_registered` from Kassa/CRM.
- The receiver passes `msg_id` as `correlation_id` when calling the function.

---

### Lint cleanup — Flake8 fixes

All errors were resolved without suppression (`# noqa`). Summary by file:

| File | Errors fixed |
|---|---|
| `src/services/rabbitmq_sender.py` | E303 — too many blank lines |
| `scripts/mock_identity_service.py` | E402 — import after `load_dotenv()` |
| `scripts/send_test_consumption_flow.py` | E402 — removed redundant `sys.path.insert` |
| `scripts/send_test_invoice_cancelled.py` | F541 — f-strings without placeholders |
| `scripts/send_test_payment.py` | E402, E221 — import order + aligned spacing |
| `scripts/send_test_registration.py` | E402, E221 × 11, E302 + XSD fixes (see below) |
| `tests/test_consumption_and_registration.py` | F811 duplicate test, E501 long line, F841 unused variable |
| `tests/test_payment.py` | E302, E305, E261 × 2 |
| `tests/test_registration.py` | E302, E305 |
| `tests/test_sender.py` | F401 — `os` imported but unused |
| `tests/test_xsd_and_validation.py` | F401 — `pytest` imported but unused |
| `conftest.py` | W292 — no newline at end of file |

**Additional fixes in `scripts/send_test_registration.py` (XSD correctness):**

- `<user_id>` renamed to `<identity_uuid>` — matches the XSD field name.
- `<session_id>` element removed — not present in the `new_registration` XSD.
- `<correlation_id>` added to the message header — consistent with the company registration script.
- Queue updated from `facturatie.incoming` to `crm.to.facturatie`.

**Pattern for E402 fixes:** In scripts run with `python -m`, `sys.path.insert()` is redundant because `-m` already adds the project root to `sys.path`. Removing it allows all imports to move to the top of the file, resolving the E402 error cleanly.