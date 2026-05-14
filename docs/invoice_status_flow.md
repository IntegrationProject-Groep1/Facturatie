# Documentation: Invoice Status Notification

**Project:** Facturatie Microservice
**Date:** 2026-05-14
**Author:** Team Facturatie

---

## 1. Overview

After every significant invoice event, the Facturatie service publishes an `invoice_status` message to CRM. This keeps CRM in sync with the current state of every invoice without CRM having to poll FossBilling.

---

## 2. Message flow

```
Facturatie service (after any invoice state change)
   |
   | invoice_status (RabbitMQ: crm.incoming)
   v
CRM
```

Triggered after these events:

| Trigger | Status sent |
|---|---|
| `new_registration` — invoice created | `sent` |
| `invoice_request` — consumption invoice created | `sent` |
| `event_ended` — consolidated invoice created per company | `sent` |
| `payment_registered` — full payment processed | `paid` |
| `invoice_cancelled` — invoice cancelled directly | `cancelled` |
| `invoice_cancelled` — credit note created for paid invoice | `cancelled` |

---

## 3. Outgoing message: `invoice_status`

**Queue:** `crm.incoming`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a1b2c3d4-e5f6-7890-abcd-ef1234567890</message_id>
    <version>2.0</version>
    <type>invoice_status</type>
    <timestamp>2026-05-14T10:00:00Z</timestamp>
    <source>facturatie</source>
    <correlation_id>550e8400-e29b-41d4-a716-446655440000</correlation_id>
  </header>
  <body>
    <invoice_id>42</invoice_id>
    <identity_uuid>a1b2c3d4-e5f6-7890-abcd-ef1234567890</identity_uuid>
    <status>sent</status>
    <amount currency="eur">150.00</amount>
  </body>
</message>
```

**Field description:**

| Field | Required | Description |
|---|---|---|
| `header/correlation_id` | no | `message_id` of the incoming message that triggered this status change |
| `body/invoice_id` | yes | FossBilling invoice ID |
| `body/identity_uuid` | yes | Master UUID of the customer |
| `body/status` | yes | `sent`, `paid`, or `cancelled` |
| `body/amount` | yes | Invoice amount with `currency` attribute (always `eur`) |

---

## 4. Implementation

**Builder:** `build_invoice_status_xml(invoice_id, identity_uuid, status, amount, correlation_id)` in `src/services/rabbitmq_sender.py`

**Publisher:** `publish_invoice_status(invoice_id, identity_uuid, status, amount, correlation_id, channel)` in `src/services/rabbitmq_sender.py`

The outgoing XML is validated against `xsd/invoice_status.xsd` before publishing.

---

## 5. Error handling

`publish_invoice_status` is wrapped in a `try/except` at every call site. If it fails (e.g. XSD validation error, RabbitMQ issue), a warning is logged but the main flow continues — the invoice operation itself is not rolled back.

```
[RECEIVER] invoice_status failed for <flow>: <reason>
```
