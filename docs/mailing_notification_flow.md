# Documentation: Mailing Notification Flow

**Project:** Facturatie Microservice  
**Date:** 2026-05-12  
**Author:** Team Facturatie

---

## 1. Overview

After a successful invoice creation in FossBilling, the Facturatie service sends a `send_mailing` message to the Mailing team via RabbitMQ. This notifies the Mailing service to deliver the invoice to the customer.

---

## 2. Message flow

```
Facturatie service (after FossBilling invoice creation)
   |
   | send_mailing (RabbitMQ: facturatie.to.mailing)
   v
Mailing service
```

Triggered by three incoming message types:
- `new_registration` (CRM → Facturatie)
- `invoice_request` (CRM → Facturatie)
- `event_ended` (CRM → Facturatie) — one mailing per company with pending consumptions

---

## 3. Outgoing message: `send_mailing`

**Queue:** `facturatie.to.mailing`

### XML structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>uuid</message_id>
    <timestamp>2026-05-12T10:00:00Z</timestamp>
    <source>facturatie</source>
    <type>send_mailing</type>
    <version>2.0</version>
    <correlation_id>uuid</correlation_id>
  </header>
  <body>
    <campaign_id>foss-invoice-42</campaign_id>
    <subject>Uw factuur 42 staat klaar</subject>
    <mail_type>invoice_ready</mail_type>
    <recipients>
      <recipient>
        <email>klant@example.com</email>
        <identity_uuid>uuid</identity_uuid>
        <contact>
          <first_name>AP Hogeschool</first_name>
          <last_name></last_name>
        </contact>
      </recipient>
    </recipients>
    <template_data>{"invoice_id": "42", "pdf_url": "https://..."}</template_data>
  </body>
</message>
```

### Field description

| Field | Required | Description |
|-------|----------|-------------|
| `message_id` | yes | UUID v4, generated per message |
| `version` | yes | Always `2.0` |
| `type` | yes | Always `send_mailing` |
| `timestamp` | yes | ISO 8601 UTC |
| `source` | yes | Always `facturatie` |
| `correlation_id` | yes | UUID van het inkomende bericht |
| `campaign_id` | yes | `foss-invoice-{invoice_id}` |
| `subject` | yes | Email subject line |
| `mail_type` | yes | Always `invoice_ready` |
| `recipient.email` | yes | Email address of the customer |
| `recipient.identity_uuid` | yes | UUID van de klant |
| `contact.first_name` | yes | Voornaam klant, of bedrijfsnaam bij event_ended |
| `contact.last_name` | yes | Achternaam klant (leeg bij bedrijfsfacturen) |
| `template_data` | no | JSON string met `invoice_id` en `pdf_url` voor de mailtemplate |

---

## 4. Implementation

**Builder function:** `build_invoice_created_notification_xml` in `src/services/rabbitmq_sender.py`

De `pdf_url` wordt automatisch samengesteld uit de `BILLING_WEB_URL` omgevingsvariabele:
```python
pdf_url = f"{BILLING_WEB_URL}/invoice/{invoice_id}"
```

Voor `event_ended` wordt `company_name` gebruikt als `first_name` omdat de `consumption_order` geen persoonsgegevens bevat.

---

## 5. Error handling

- Als FossBilling de factuur niet kan aanmaken → geen mailing verstuurd, bericht naar DLQ.
- Als de mailing mislukt na succesvolle factuuraanmaak → error gelogd, bericht naar DLQ (factuur blijft bestaan in FossBilling).
