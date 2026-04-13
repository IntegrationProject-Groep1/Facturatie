# Block Cancellation of Paid Invoices

**Branch:** `feature/block-_paid_invoices`
**Date:** 2026-04-13
**Author:** Team Facturatie

---

## 1. Problem

The `invoice_cancelled` flow did not check whether an invoice was already paid before cancelling it in FossBilling. This meant a paid invoice could be cancelled, causing financial inconsistencies.

---

## 2. Solution

Before calling FossBilling to cancel an invoice, the service now fetches the current invoice status first. Based on that status, it either proceeds with the cancellation or blocks it and notifies CRM.

---

## 3. Flow

```
invoice_cancelled message arrives on facturatie.incoming
        │
        ▼
[RECEIVER] validates XML → invalid? → facturatie.dlq
        │ valid
        ▼
invoice_id present? → missing? → facturatie.dlq
        │ present
        ▼
get_invoice_status(invoice_id) via FossBilling API
        │
        ├── None (not found / API unreachable)
        │       → publish_cancellation_failed(reason=invoice_not_found)
        │       → ack
        │
        ├── "paid"
        │       → publish_cancellation_failed(reason=invoice_already_paid)
        │       → ack
        │
        ├── "cancelled"
        │       → publish_cancellation_failed(reason=invoice_already_cancelled)
        │       → ack
        │
        └── "unpaid" (or any other status)
                → cancel_invoice() in FossBilling
                → publish_invoice_cancelled() to CRM
                → ack
```

---

## 4. Files changed

| File | Change |
|---|---|
| `src/services/fossbilling_api.py` | Added `get_invoice_status(invoice_id)` |
| `src/services/crm_publisher.py` | Added `build_cancellation_failed_xml()` and `publish_cancellation_failed()` |
| `src/services/rabbitmq_receiver.py` | Added status check before `cancel_invoice()` |
| `tests/test_block_paid_invoice_cancellation.py` | 15 tests covering all scenarios |
| `scripts/send_cancellation.py` | Manual test script |

---

## 5. New functions

### `get_invoice_status(invoice_id)` — `fossbilling_api.py`

Fetches the current status of an invoice from FossBilling.

| Return value | Meaning |
|---|---|
| `"paid"` | Invoice has been paid |
| `"unpaid"` | Invoice is open/pending |
| `"cancelled"` | Invoice was already cancelled |
| `None` | Invoice not found or API unreachable |

**FossBilling endpoint:** `POST admin/invoice/get`

---

### `publish_cancellation_failed(invoice_id, customer_id, correlation_id, reason)` — `crm_publisher.py`

Sends an `invoice_cancelled` message with `status=failed` to the CRM queue when a cancellation is blocked.

**XML structure sent to CRM:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>...</message_id>
    <version>2.0</version>
    <type>invoice_cancelled</type>
    <timestamp>...</timestamp>
    <source>facturatie_system</source>
    <correlation_id>...</correlation_id>
  </header>
  <body>
    <invoice_id>78</invoice_id>
    <customer_id>12345</customer_id>
    <status>failed</status>
    <reason>invoice_already_paid</reason>
  </body>
</message>
```

---

## 6. Blocking reasons

| Reason | When |
|---|---|
| `invoice_already_paid` | Invoice status is `paid` |
| `invoice_already_cancelled` | Invoice status is `cancelled` |
| `invoice_not_found` | FossBilling returns nothing or API is unreachable |

---

## 7. Tests

**File:** `tests/test_block_paid_invoice_cancellation.py` — 15 tests

| Test | What it checks |
|---|---|
| `test_get_invoice_status_returns_paid` | API returns `paid` status correctly |
| `test_get_invoice_status_returns_pending` | API returns `unpaid` status correctly |
| `test_get_invoice_status_returns_none_when_not_found` | Exception from API returns `None` |
| `test_cancellation_failed_xml_contains_reason` | Failed XML includes `reason` field |
| `test_cancellation_failed_xml_has_correct_type` | Failed XML uses type `invoice_cancelled` |
| `test_cancellation_failed_xml_has_failed_status` | Failed XML has `status=failed` |
| `test_cancellation_failed_xml_preserves_correlation_id` | Failed XML carries original `correlation_id` |
| `test_paid_invoice_blocks_cancellation` | `cancel_invoice` is NOT called for paid invoices |
| `test_paid_invoice_sends_failed_notification_to_crm` | CRM notified with `invoice_already_paid` |
| `test_paid_invoice_is_acked_not_sent_to_dlq` | Valid message is acked, not nacked |
| `test_pending_invoice_proceeds_with_cancellation` | Unpaid invoice goes through normally |
| `test_invoice_not_found_sends_error_to_crm` | CRM notified with `invoice_not_found` |
| `test_fossbilling_unreachable_during_status_check_acks_and_notifies_crm` | API down → acked + CRM notified |
| `test_empty_invoice_id_sends_to_dlq` | Missing `invoice_id` goes to DLQ |
| `test_already_cancelled_invoice_blocks_cancellation` | `cancel_invoice` NOT called for already cancelled |

---

## 8. Manual testing

Start the service:
```powershell
.venv/Scripts/python -m src.main
```

Send a cancellation for a specific invoice:
```powershell
.venv/Scripts/python scripts/send_cancellation.py <invoice_id>
```

Check the current status of an invoice directly:
```powershell
.venv/Scripts/python -c "
from dotenv import load_dotenv
load_dotenv()
from src.services.fossbilling_api import get_invoice_status
print('Status:', get_invoice_status('<invoice_id>'))
"
```

---

## 9. Verified results

| Scenario | Invoice | Observed output |
|---|---|---|
| Unpaid invoice | 77, 78 | `Flow complete` — cancelled in FossBilling, CRM notified |
| Already cancelled invoice | 78 | `Cancellation blocked` — CRM notified with `invoice_already_cancelled` |
| Paid invoice (marked via API) | 75 | `Cancellation blocked` — CRM notified with `invoice_already_paid` |
| Invoice not found / API down | — | CRM notified with `invoice_not_found`, message acked |

> **Note:** FossBilling's admin dashboard may not visually reflect status changes after an API update. This is a known UI behaviour in FossBilling. The API itself correctly stores and returns the updated status. Always use `get_invoice_status()` to verify, not the dashboard.