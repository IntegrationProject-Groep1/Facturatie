# Documentation: Payment Registered Flow

**Project:** Facturatie Microservice  
**Date:** 2026-05-04  
**Author:** Team Facturatie

---

## 1. Overview

This flow handles incoming payment confirmations from the Kassa (via CRM passthrough). When a payment is registered, Facturatie marks the corresponding invoice as paid in FossBilling and sends a confirmation to CRM.

---

## 2. Architecture and message flow

```
Kassa → CRM (passthrough)
   |
   | payment_registered (RabbitMQ: facturatie.incoming)
   v
Facturatie service
   |-- Invalid XML             -->  Dead Letter Queue (facturatie.dlq)
   |-- XSD validation fail     -->  Dead Letter Queue (facturatie.dlq)
   |-- Duplicate message_id    -->  Acknowledged, skipped
   |-- Missing invoice element -->  Dead Letter Queue (facturatie.dlq)
   |-- Missing invoice id      -->  Dead Letter Queue (facturatie.dlq)
   |
   | pay_invoice() --> FossBilling invoice marked as paid
   |-- FossBilling fail        -->  Dead Letter Queue (facturatie.dlq)
   |
   | build_payment_confirmed_xml()
   | payment_method normalised (see mapping in section 4)
   |
   | payment_registered (outgoing) --> crm.incoming
```

---

## 3. XML message formats

### payment_registered (incoming — Kassa → Facturatie)

```xml
<message>
  <header>
    <message_id>e0ce8a33-ef26-4610-8bb7-2480dbacadc2</message_id>
    <version>2.0</version>
    <type>payment_registered</type>
    <timestamp>2026-05-04T18:30:00Z</timestamp>
    <source>kassa</source>
  </header>
  <body>
    <payment_context>consumption</payment_context>
    <user_id>BADGE-001</user_id>
    <invoice>
      <id>13</id>
      <status>paid</status>
      <amount_paid currency="eur">150.00</amount_paid>
      <due_date>2026-05-31</due_date>
    </invoice>
    <transaction>
      <id>TRANS-4CEB4373</id>
      <payment_method>on_site</payment_method>
    </transaction>
  </body>
</message>
```

**Key fields:**
- `body/payment_context` — `registration` or `consumption`, required first field in the body
- `body/user_id` — optional badge/customer ID of the payer
- `body/invoice/id` — FossBilling invoice ID to mark as paid
- `body/transaction/payment_method` — incoming values: `on_site`, `online`, `company_link`

### payment_registered (outgoing — Facturatie → CRM)

```xml
<message>
  <header>
    <message_id>8fc6d7e8-f9a0-1234-cdef-234567800014</message_id>
    <timestamp>2026-05-04T18:30:05Z</timestamp>
    <source>facturatie</source>
    <type>payment_registered</type>
    <version>2.0</version>
    <correlation_id>e0ce8a33-ef26-4610-8bb7-2480dbacadc2</correlation_id>
  </header>
  <body>
    <identity_uuid>BADGE-001</identity_uuid>
    <invoice>
      <id>13</id>
      <amount_paid currency="eur">150.00</amount_paid>
      <status>paid</status>
      <due_date>2026-05-31</due_date>
    </invoice>
    <payment_context>online_invoice</payment_context>
    <transaction>
      <id>550e8400-e29b-41d4-a716-446655440000</id>
      <payment_method>on_site</payment_method>
    </transaction>
  </body>
</message>
```

**Key fields:**
- `header/correlation_id` — `message_id` of the incoming `payment_registered` message
- `body/identity_uuid` — badge/customer ID of the payer
- `body/invoice/status` — `paid` for full payment, `pending` for partial payment
- `body/transaction/payment_method` — see mapping in section 4
- `body/payment_context` — always `online_invoice`

---

## 4. Payment method mapping

Incoming values are normalised to the values accepted by the shared XSD (Section 8.2). Unrecognised values fall back to `online`.

| Incoming | Outgoing |
|---|---|
| `on_site` | `on_site` |
| `card` | `on_site` |
| `cash` | `on_site` |
| `pos` | `on_site` |
| `online` | `online` |
| `company_link` | `company_link` |
| `link` | `company_link` |
| *(unknown)* | `online` |

---

## 5. Modified files

### `src/services/rabbitmq_receiver.py`

| Handler | Behaviour |
|---|---|
| `payment_registered` | Reads `invoice/id`, `amount_paid`, `transaction/payment_method` and `user_id` from the message; calls `pay_invoice()`; normalises `payment_method`; sends confirmation to `crm.incoming` |

### `src/services/rabbitmq_sender.py`

| Function | Purpose |
|---|---|
| `build_payment_confirmed_xml(invoice_id, identity_uuid, amount, currency, payment_method, paid_at, status, due_date, correlation_id)` | Builds the outgoing `payment_registered` message for CRM; validates against `payment_registered.xsd` |

### `src/services/fossbilling_api.py`

| Function | Purpose |
|---|---|
| `pay_invoice(invoice_id, amount)` | Sets invoice status to `paid` via `admin/invoice/update`; returns `True` on success |

---

## 6. Local testing

### Requirements

| What | Command |
|---|---|
| RabbitMQ running | `docker compose up rabbitmq -d` |
| Receiver running | `python -m src.main` |
| FossBilling reachable | verify `.env` for `BILLING_API_URL` and `BILLING_API_TOKEN` |

### Test script

Update `INVOICE_ID` at the top of the script to an existing invoice ID from FossBilling (e.g. one created via the consumption flow):

```bash
python -m scripts.send_test_payment
```

### Expected result

- **FossBilling** — invoice has status `paid`
- **RabbitMQ `crm.incoming`** — a `payment_registered` confirmation message is available
- **Receiver logs** — no errors; payment data extracted and confirmation sent

---

## 7. Security

| Area | Status |
|---|---|
| Credentials in code | None — all via `.env` and `os.getenv()` |
| SQL injection | Not applicable — no database interaction in this flow |
| XML injection (incoming) | Protected — `defusedxml` used for all incoming XML parsing |
| XML injection (outgoing) | Protected — `ElementTree` automatically escapes all values |
