# Documentation: Invoice Link Notification (Frontend)

**Project:** Facturatie Microservice  
**Date:** 2026-05-06  
**Author:** Team Facturatie

---

## 1. Overview

After every successful invoice creation in FossBilling, the Facturatie service publishes an `invoice_available` message to the Frontend team via RabbitMQ. The Frontend service can use this to display the invoice link to the customer on the website.

---

## 2. Message flow

```
Facturatie service (after FossBilling invoice creation)
   |
   | invoice_available (RabbitMQ: facturatie.to.frontend)
   v
Frontend service
```

Triggered by three incoming message types:

| Trigger | Description |
|---|---|
| `new_registration` | After the registration invoice is created for a new customer |
| `invoice_request` | After the consolidated invoice is created for a specific consumption order |
| `event_ended` | After the consolidated invoice is created per company at end of event |

---

## 3. Outgoing message: `invoice_available`

**Queue:** `facturatie.to.frontend`

### XML structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a3f1c2d4-7e89-4b0a-bc12-3f456d789012</message_id>
    <version>2.0</version>
    <type>invoice_available</type>
    <timestamp>2026-05-06T10:30:00Z</timestamp>
    <source>facturatie</source>
    <master_uuid>550e8400-e29b-41d4-a716-446655440000</master_uuid>
  </header>
  <body>
    <invoice_id>142</invoice_id>
    <pdf_url>https://facturatie.desiderius.me/invoice/142</pdf_url>
  </body>
</message>
```

### Field description

| Field | Required | Description |
|---|---|---|
| `header/message_id` | yes | UUID v4, generated per message |
| `header/version` | yes | Always `2.0` |
| `header/type` | yes | Always `invoice_available` |
| `header/timestamp` | yes | ISO 8601 UTC |
| `header/source` | yes | Always `facturatie` |
| `header/master_uuid` | yes | UUID of the customer from the identity service, used by Frontend to identify the customer |
| `body/invoice_id` | yes | FossBilling invoice ID |
| `body/pdf_url` | yes | URL to the invoice page, constructed from `BILLING_WEB_URL` env variable |

---

## 4. XSD

**File:** `src/services/xsd/invoice_link.xsd`

The `master_uuid` field is validated against a strict UUID pattern:
```
[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}
```

The XML is validated before publishing. If validation fails, an exception is raised and a warning is logged — invoice creation is **not** rolled back.

---

## 5. Implementation

**Builder function:** `build_invoice_link_xml(invoice_id, master_uuid)` in `src/services/rabbitmq_sender.py`

**Publisher function:** `publish_invoice_link(invoice_id, master_uuid, channel)` in `src/services/rabbitmq_sender.py`

The `pdf_url` is constructed from the `BILLING_WEB_URL` environment variable:
```python
billing_web_base = os.getenv("BILLING_WEB_URL", "https://portal.yourdomain.com")
pdf_url = f"{billing_web_base}/invoice/{invoice_id}"
```

The `master_uuid` is retrieved from the `pending_consumptions` table before the rows are cleared:

| Flow | DB function used |
|---|---|
| `new_registration` | Returned directly by the identity service (`request_master_uuid`) |
| `invoice_request` | `get_master_uuid_by_correlation_id(correlation_id)` |
| `event_ended` | `get_master_uuid_by_company_id(company_id)` |

---

## 6. Environment variables

| Variable | Default | Description |
|---|---|---|
| `QUEUE_FRONTEND` | `facturatie.to.frontend` | Queue name for the Frontend service |
| `BILLING_WEB_URL` | `https://portal.yourdomain.com` | Base URL used to construct the `pdf_url` |

---

## 7. Error handling

The `publish_invoice_link` call is wrapped in a `try/except` in all three flows. If it fails (e.g. XSD validation error, RabbitMQ issue, empty `master_uuid`), a warning is logged but the flow continues — the invoice is already created in FossBilling and will not be rolled back.

```
[RECEIVER] Invoice created but invoice_link failed: <reason>
```
