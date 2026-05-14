# Documentation: Profile Update Flow

**Project:** Facturatie Microservice
**Date:** 2026-05-14
**Author:** Team Facturatie

---

## 1. Overview

When a customer updates their profile (name, company, VAT number), the identity service notifies other teams via RabbitMQ. The Facturatie service receives a `profile_update` message and updates the corresponding client record in FossBilling so future invoices reflect the latest customer data.

---

## 2. Message flow

```
Identity service / CRM
   |
   | profile_update (RabbitMQ: crm.to.facturatie)
   v
Facturatie service
   |-- Invalid XML / XSD fail  -->  Dead Letter Queue (facturatie.dlq)
   |-- Duplicate message_id    -->  Acknowledged, skipped
   |
   | _get_client_by_email() --> FossBilling client lookup
   |-- Client not found        -->  Warning logged, ACK (no rollback)
   |
   | update_client() --> FossBilling client record updated
   |-- FossBilling fail        -->  Dead Letter Queue (facturatie.dlq)
   |
   | send_log(info, "user", ...)
   v
   ACK
```

---

## 3. Incoming message: `profile_update`

**Queue:** `crm.to.facturatie`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>550e8400-e29b-41d4-a716-446655440000</message_id>
    <version>2.0</version>
    <type>profile_update</type>
    <timestamp>2026-05-14T10:00:00Z</timestamp>
    <source>identity</source>
  </header>
  <body>
    <identity_uuid>a1b2c3d4-e5f6-7890-abcd-ef1234567890</identity_uuid>
    <email>jan.peeters@company.be</email>
    <type>company</type>
    <company_name>Company NV</company_name>
    <vat_number>BE0123456789</vat_number>
    <contact>
      <first_name>Jan</first_name>
      <last_name>Peeters</last_name>
    </contact>
  </body>
</message>
```

**Fields read by the handler:**

| Field | Description |
|---|---|
| `body/identity_uuid` | Master UUID of the customer |
| `body/email` | Used to look up the client in FossBilling |
| `body/type` | `company` or `private` |
| `body/company_name` | Updated company name |
| `body/vat_number` | Updated VAT number |
| `body/contact/first_name` | Updated first name |
| `body/contact/last_name` | Updated last name |

---

## 4. Processing logic

1. **XSD validation** — invalid message → DLQ + NACK
2. **Duplicate detection** — same `message_id` → ACK, skip
3. **Client lookup** — `_get_client_by_email(email)` in FossBilling
   - If not found: warning logged, message ACKed (not an error — customer may not have an invoice yet)
4. **Client update** — `update_client(client_id, customer_data)` with new name, company, address
5. **Log** — `send_log(info, "user", ...)` to central logs queue
6. **ACK**

---

## 5. Modified files

### `src/services/rabbitmq_receiver.py`

| Handler | Behaviour |
|---|---|
| `profile_update` | Reads identity fields from body; calls `update_client_by_identity_uuid()`; logs result; ACKs or NACKs on failure |

### `src/services/fossbilling_api.py`

| Function | Purpose |
|---|---|
| `update_client_by_identity_uuid(identity_uuid, email, first_name, last_name, company_name, vat_number)` | Looks up client by email, then calls `update_client()`. Returns `True` on success, `False` if not found |

---

## 6. Error handling

| Situation | Behaviour |
|---|---|
| XSD validation fails | DLQ + NACK |
| Client not found in FossBilling | Error logged, DLQ + NACK |
| FossBilling update fails | Error logged, DLQ + NACK |
