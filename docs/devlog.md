# Development Log

## 2026-03-29 — RabbitMQ Sender & Receiver

### What was built

#### `src/services/rabbitmq_sender.py`
- Implemented `get_connection()` to connect to RabbitMQ using credentials and host/port from `.env`.
- Fixed port to be cast to `int` (`int(os.getenv('RABBITMQ_PORT', 5672))`) since pika requires an integer.
- Implemented `build_consumption_order_xml()` to generate a valid `CONSUMPTION_ORDER` XML message with:
  - A unique UUID v4 as `<id>`
  - Version `2.0`
  - ISO-8601 UTC timestamp
  - Dynamic company fields (`<company_id>`, `<company_name>`) that are only included when `is_company_linked=True`
- Implemented `send_message()` to publish the XML to a durable RabbitMQ queue with `delivery_mode=2` (persistent).

#### `src/services/rabbitmq_receiver.py`
- Renamed from `rabbitqm_receiver.py` (typo fix).
- Implemented `validate_message(root, seen_ids)` that returns a list of error strings:
  - Validates required header fields: `<id>`, `<version>` (must be `2.0`), `<type>`, `<timestamp>`, `<source>`
  - Validates known message types: `CONSUMPTION_ORDER`, `PAYMENT_REGISTERED`, `HEARTBEAT`
  - For `CONSUMPTION_ORDER`: validates VAT rate (must be 6, 12, or 21) and company fields when `is_company_linked=true`
  - For `PAYMENT_REGISTERED`: validates that `<correlation_id>` is present
  - Optional `seen_ids` parameter enables duplicate detection by header ID
- Implemented `send_to_dlq()` to forward invalid messages to `facturatie.dlq` with error details in headers.
- Implemented `process_message()` as the RabbitMQ callback:
  1. Parse XML
  2. Check for duplicate message ID against in-memory set
  3. Validate message structure
  4. Route invalid messages to DLQ, acknowledge valid ones
- Implemented `start_receiver()` to start consuming from the `facturatie` queue.

### Tests written (`tests/test_validate_message.py`)

Written following TDD principles. All tests target `validate_message()` directly.

| Test | What it checks |
|---|---|
| `test_valid_consumption_order` | A fully valid message returns no errors |
| `test_invalid_vat_rate_returns_error` | VAT rate 99 is rejected |
| `test_valid_vat_rate_6/12/21` | All allowed VAT rates pass |
| `test_missing_company_id_when_company_linked` | `company_id` required when linked |
| `test_missing_company_name_when_company_linked` | `company_name` required when linked |
| `test_valid_company_linked` | Both fields present — no error |
| `test_missing_message_id` | Empty `<id>` returns error |
| `test_missing_timestamp` | Empty `<timestamp>` returns error |
| `test_missing_source` | Empty `<source>` returns error |
| `test_unknown_message_type` | Unknown type returns error |
| `test_payment_registered_missing_correlation_id` | Missing `correlation_id` returns error |
| `test_payment_registered_with_correlation_id` | Present `correlation_id` passes |
| `test_invalid_version_returns_error` | Version `1.0` is rejected |
| `test_valid_version` | Version `2.0` passes |
| `test_duplicate_message_is_flagged` | Same ID in `seen_ids` returns duplicate error |
| `test_unique_message_is_not_flagged` | New ID is not flagged |

### Project structure fixes
- Created `src/__init__.py` and `src/services/__init__.py` so Python treats them as packages.
- Created `tests/__init__.py` for the test package.
- Created `conftest.py` at project root to add the root to `sys.path`, enabling `from src.services.x import ...` in tests.