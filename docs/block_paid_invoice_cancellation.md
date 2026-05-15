# Invoice Cancelled Flow

**Date:** 2026-05-10
**Author:** Team Facturatie

---

## 1. Problem

The `invoice_cancelled` flow did not check whether an invoice was already paid or whether it was a consumption invoice before cancelling it in FossBilling. This meant paid invoices and consumption invoices could be incorrectly cancelled.

---

## 2. Solution

Before cancelling, the service checks both the **payment status** and the **invoice type**. Consumption invoices can never be cancelled. Paid registration invoices get a credit note instead of a direct cancellation. Only unpaid registration invoices are cancelled directly.

---

## 3. Flow

```
invoice_cancelled message arrives on facturatie.incoming
        │
        ▼
[RECEIVER] validates XML → invalid? → facturatie.dlq
        │ valid
        ▼
invoice_id present? → missing? → publish_cancellation_failed(missing_invoice_id) → dlq
        │ present
        ▼
get_invoice_status(invoice_id) via FossBilling API
        │
        ├── None (not found)
        │       → publish_cancellation_failed(reason=invoice_not_found)
        │       → ack
        │
        ├── "cancelled"
        │       → publish_cancellation_failed(reason=invoice_already_cancelled)
        │       → ack
        │
        ├── "paid"
        │       → get_invoice_type(invoice_id)
        │       │
        │       ├── "consumption"
        │       │       → publish_cancellation_failed(reason=consumption_invoice_cannot_be_cancelled)
        │       │       → ack
        │       │
        │       └── "registration"
        │               → create_credit_note(invoice_id) in FossBilling
        │               → publish_invoice_cancelled() to CRM
        │               → ack
        │
        └── "unpaid"
                → get_invoice_type(invoice_id)
                │
                ├── "consumption"
                │       → publish_cancellation_failed(reason=consumption_invoice_cannot_be_cancelled)
                │       → ack
                │
                └── "registration"
                        → cancel_invoice() in FossBilling
                        → publish_invoice_cancelled() to CRM
                        → ack
```

---

## 4. Files changed

| File | Change |
|---|---|
| `src/services/fossbilling_api.py` | Added `get_invoice_status()`, `get_invoice_type()`, `create_credit_note()`, `cancel_invoice()` |
| `src/services/rabbitmq_sender.py` | Added `publish_cancellation_failed()`, `publish_invoice_cancelled()` |
| `src/services/rabbitmq_receiver.py` | Updated `invoice_cancelled` handler met type-check en creditnota logica |
| `tests/test_block_paid_invoice_cancellation.py` | 15 tests covering all scenarios |
| `scripts/send_test_invoice_cancelled.py` | Manual test script voor 3 scenario's |

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

### `get_invoice_type(invoice_id)` — `fossbilling_api.py`

Determines the invoice type by checking the line items.

| Return value | Meaning |
|---|---|
| `"registration"` | Invoice contains a line with "inschrijvingskosten" (case-insensitive) |
| `"consumption"` | All other invoices |

**FossBilling endpoint:** `POST admin/invoice/get`

---

### `create_credit_note(invoice_id)` — `fossbilling_api.py`

Creates a credit note (negative invoice) for a paid registration invoice. Fetches the original invoice lines and creates a new invoice with negated amounts and titles prefixed with `"Creditnota: "`. Returns the new credit note invoice ID.

---

### `publish_cancellation_failed(invoice_id, customer_id, reason)` — `rabbitmq_sender.py`

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
| `invoice_not_found` | FossBilling kent het invoice ID niet |
| `invoice_already_cancelled` | Factuur is al geannuleerd |
| `consumption_invoice_cannot_be_cancelled` | Consumptiefactuur — betaald of onbetaald, kan nooit geannuleerd worden |

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

Start the receiver:
```powershell
python -m src.main
```

Run het testscript (pas de invoice IDs bovenaan het script aan naar bestaande facturen):
```powershell
python -m scripts.send_test_invoice_cancelled
```

Check de huidige status van een factuur:
```powershell
python -c "
from dotenv import load_dotenv
load_dotenv()
from src.services.fossbilling_api import get_invoice_status, get_invoice_type
print('Status:', get_invoice_status('<invoice_id>'))
print('Type:', get_invoice_type('<invoice_id>'))
"
```

---

## 9. Verified results

| Scenario | Invoice | Observed output |
|---|---|---|
| Paid registratiefactuur | #64 | `Credit note created | credit_note_id=70` — creditnota zichtbaar in FossBilling |
| Paid consumptiefactuur | #62 | `cancellation_failed | reason=consumption_invoice_cannot_be_cancelled` |
| Unpaid registratiefactuur | #73 | `Invoice '73' successfully marked as cancelled` |
| Al geannuleerde factuur | #73 (2e keer) | `cancellation_failed | reason=invoice_already_cancelled` |
| Invoice not found | — | `cancellation_failed | reason=invoice_not_found` |

> **Note:** FossBilling's admin dashboard may not visually reflect status changes after an API update. This is a known UI behaviour in FossBilling. The API itself correctly stores and returns the updated status. Always use `get_invoice_status()` to verify, not the dashboard.