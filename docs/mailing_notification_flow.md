# Documentation: Mailing Notification Flow

**Project:** Facturatie Microservice  
**Date:** 2026-04-28  
**Author:** Team Facturatie

---

## 1. Overview

After a successful invoice creation in FossBilling, the Facturatie service sends an `invoice_created_notification` message to the Mailing team via RabbitMQ. This notifies the Mailing service to deliver the invoice to the customer.

---

## 2. Message flow

```
Facturatie service (after FossBilling invoice creation)
   |
   | invoice_created_notification (RabbitMQ: facturatie.to.mailing)
   v
Mailing service
```

Triggered by two incoming message types:
- `new_registration` (CRM → Facturatie)
- `invoice_request` (CRM → Facturatie)

---

## 3. Outgoing message: `invoice_created_notification`

**Queue:** `facturatie.to.mailing`

### XML structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>uuid</message_id>
    <version>1.0</version>
    <type>invoice_created_notification</type>
    <timestamp>2026-04-01T18:00:00Z</timestamp>
    <source>facturatie_system</source>
  </header>
  <body>
    <recipient_email>klant@example.com</recipient_email>
    <invoice_id>INV-2026-001</invoice_id>
    <subject>Uw nieuwe factuur</subject>
    <message_text>Beste klant, uw factuur is beschikbaar.</message_text>
    <pdf_url>https://<fossbilling_host>/invoices/INV-2026-001.pdf</pdf_url>
  </body>
</message>
```

### Field description

| Field | Required | Description |
|---|---|---|
| `message_id` | yes | UUID v4, generated per message |
| `version` | yes | Always `1.0` |
| `type` | yes | Always `invoice_created_notification` |
| `timestamp` | yes | ISO 8601 UTC |
| `source` | yes | Always `facturatie_system` |
| `recipient_email` | yes | Email address of the customer |
| `invoice_id` | yes | FossBilling invoice ID |
| `subject` | yes | Email subject line |
| `message_text` | yes | Email body text |
| `pdf_url` | yes | URL to the invoice PDF, constructed from `BILLING_API_URL` env variable |

---

## 4. Implementation

**Builder function:** `build_invoice_created_notification_xml` in `src/services/rabbitmq_sender.py`

The `pdf_url` is automatically constructed from the `BILLING_API_URL` environment variable:
```python
billing_base = os.getenv("BILLING_API_URL", "").rsplit("/api", 1)[0]
pdf_url = f"{billing_base}/invoices/{invoice_id}.pdf"
```

---

## 5. Error handling

If FossBilling fails to create the invoice, no notification is sent to Mailing. The message is forwarded to the Dead Letter Queue (`facturatie.dlq`) instead.
