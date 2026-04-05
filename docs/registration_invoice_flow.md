# Documentation: Registration Invoice Flow

**Project:** Facturatie Microservice
**Branch:** `Inschrijvingskosten-flow`
**Date:** 2026-04-05
**Author:** Team Facturatie

---

## 1. Overview

The registration invoice flow processes new customer registrations received from the CRM team. When a new customer registers, the Facturatie service receives a message via RabbitMQ, automatically creates a client and invoice in FossBilling, and sends a confirmation message to the Mailing team to deliver the invoice to the customer.

---

## 2. Architecture and message flow

```
CRM
   |
   | new_registration (RabbitMQ: crm.to.facturatie)
   v
Facturatie service
   |-- Validation failed  -->  Dead Letter Queue (facturatie.dlq)
   |-- FossBilling error  -->  Dead Letter Queue (facturatie.dlq)
   |
   | Client + invoice created (FossBilling API)
   |
   | invoice (RabbitMQ: facturatie.to.mailing)
   v
Mailing team
   |
   | Invoice sent to customer
   v
Customer
```

---

## 3. Message format

### 3.1 Incoming message: `new_registration`

Received via queue: **`crm.to.facturatie`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>550e8400-e29b-41d4-a716-446655440000</message_id>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>2026-03-31T10:00:00Z</timestamp>
    <source>crm</source>
  </header>
  <body>
    <customer>
      <email>customer@company.be</email>
      <first_name>Jan</first_name>
      <last_name>Peeters</last_name>
      <is_company_linked>true</is_company_linked>
      <company_id>123</company_id>
      <company_name>Company NV</company_name>
      <address>
        <street>Kiekenmarkt</street>
        <number>42</number>
        <postal_code>1000</postal_code>
        <city>Brussels</city>
        <country>be</country>
      </address>
    </customer>
    <registration_fee currency="eur">150.00</registration_fee>
  </body>
</message>
```

**Required fields:**

| Field | Description |
|---|---|
| `header/message_id` | Unique message identifier (UUID) |
| `header/version` | Must be `2.0` |
| `header/type` | Must be `new_registration` (lowercase) |
| `header/timestamp` | ISO-8601 UTC format (e.g. `2026-03-31T10:00:00Z`) |
| `header/source` | Name of the sending system |
| `body/customer/email` | Customer email address |
| `body/customer/is_company_linked` | `true` or `false` |
| `body/customer/company_id` | Required if `is_company_linked=true` |
| `body/customer/company_name` | Required if `is_company_linked=true` |
| `body/registration_fee` | Registration fee amount |

**Optional fields:**

| Field | Description |
|---|---|
| `body/customer/first_name` | Customer first name |
| `body/customer/last_name` | Customer last name |
| `body/customer/address/*` | Address fields (street, number, postal_code, city, country) |

### 3.2 Outgoing message: `invoice`

Sent to queue: **`facturatie.to.mailing`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a1b2c3d4-e5f6-7890-abcd-ef1234567890</message_id>
    <version>2.0</version>
    <type>invoice</type>
    <timestamp>2026-03-31T10:00:05Z</timestamp>
    <source>facturatie</source>
    <correlation_id>550e8400-e29b-41d4-a716-446655440000</correlation_id>
  </header>
  <body>
    <invoice_id>INV-2026-001</invoice_id>
    <client_email>customer@company.be</client_email>
    <company_name>Company NV</company_name>
  </body>
</message>
```

The `correlation_id` in the header references the `message_id` of the original `new_registration` message, allowing the Mailing team to link messages.

---

## 4. Processing logic

For each received message, the service follows these steps:

1. **XML parsing** — invalid XML or bad encoding → DLQ
2. **Duplicate detection** — same `message_id` already processed → ignored (ACK)
3. **Validation** — missing or invalid fields → DLQ
4. **Create client in FossBilling** — idempotent: if the email already exists, the existing client is reused
5. **Create invoice in FossBilling** — endpoint: `admin/invoice/prepare`
6. **Retry logic** — steps 4 and 5 are retried up to **3 times** with a 2-second delay on failure; after 3 failed attempts → DLQ
7. **Send `invoice` message** to `facturatie.to.mailing`
8. **ACK** — message is acknowledged as processed

---

## 5. FossBilling integration

The service communicates with the FossBilling REST API via HTTP Basic Auth.

| Setting | Environment variable | Example |
|---|---|---|
| API URL | `BILLING_API_URL` | `https://server/api` |
| Username | `BILLING_API_USERNAME` | `admin` |
| API token | `BILLING_API_TOKEN` | *(generated in FossBilling)* |

Generate the API token via: **FossBilling admin → Account → API tokens → Generate new key**

---

## 6. Queues

| Queue | Direction | Purpose |
|---|---|---|
| `crm.to.facturatie` | Incoming | Receive messages from CRM |
| `facturatie.dlq` | Outgoing | Invalid or failed messages |
| `facturatie.to.mailing` | Outgoing | Invoice messages to Mailing team |

---

## 7. Error handling

| Situation | Behaviour |
|---|---|
| Invalid XML | Forwarded to DLQ, message rejected (NACK) |
| Validation errors | Forwarded to DLQ with error details in header, message rejected |
| Duplicate message | Ignored, message acknowledged (ACK) |
| FossBilling error | Max. 3 attempts, then DLQ + NACK |
| Existing client | Existing client_id reused (idempotent) |

---

## 8. Implemented files

| File | Status | Description |
|---|---|---|
| `src/services/rabbitmq_receiver.py` | Modified | Validation, processing and DLQ logic |
| `src/services/rabbitmq_sender.py` | Modified | `build_invoice_request_xml()` added |
| `src/services/fossbilling_api.py` | New | FossBilling API integration with retry |
| `tests/test_validate_message.py` | Modified | +12 tests for new_registration validation |
| `tests/test_invoice_request.py` | New | 9 tests for invoice message builder |
| `tests/test_fossbilling_api.py` | New | 18 tests for FossBilling API service |
| `tests/test_process_new_registration.py` | New | 7 tests for process_message integration |

---

## 9. Test results

All tests pass.

| Test file | Tests |
|---|---|
| `test_validate_message.py` | 12 new |
| `test_invoice_request.py` | 9 new |
| `test_fossbilling_api.py` | 18 new |
| `test_process_new_registration.py` | 7 new |
| **Total new** | **46 tests** |
| **Total project** | **81 tests passing** |

---

## 10. Open points

- The Mailing team must implement and monitor the **`facturatie.to.mailing`** queue.
- `seen_message_ids` is currently stored in-memory and resets on service restart. Migration to persistent storage (MySQL) is planned for a later sprint.
