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
   | payment_method mapped (on_site → cash, online → card, company_link → bank_transfer)
   |
   | payment_registered (outgoing) --> facturatie.to.crm
```

---

## 3. XML message formats

### payment_registered (inkomend — Kassa → Facturatie)

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
- `body/payment_context` — `registration` of `consumption`, verplicht eerste veld in de body
- `body/user_id` — optioneel badge/klant-ID van de betaler
- `body/invoice/id` — FossBilling factuur-ID dat op `paid` wordt gezet
- `body/transaction/payment_method` — inkomende waarden: `on_site`, `online`, `company_link`

### payment_registered (uitgaand — Facturatie → CRM)

```xml
<message>
  <header>
    <message_id>8fc6d7e8-f9a0-1234-cdef-234567800014</message_id>
    <timestamp>2026-05-04T18:30:05Z</timestamp>
    <source>facturatie</source>
    <type>payment_registered</type>
    <version>2.0</version>
  </header>
  <body>
    <invoice_id>13</invoice_id>
    <customer_id>BADGE-001</customer_id>
    <amount_paid currency="eur">150.00</amount_paid>
    <payment_method>cash</payment_method>
    <paid_at>2026-05-04T18:30:05Z</paid_at>
  </body>
</message>
```

**Key fields:**
- `body/payment_method` — uitgaande waarden: `cash`, `card`, `bank_transfer` (gemapt vanuit inkomende waarden)
- `body/paid_at` — tijdstip waarop FossBilling de betaling heeft verwerkt

---

## 4. Payment method mapping

De inkomende `payment_method` waarden van Kassa worden gemapt naar de uitgaande waarden die CRM verwacht:

| Inkomend (Kassa) | Uitgaand (CRM) |
|-----------------|----------------|
| `on_site`       | `cash`         |
| `online`        | `card`         |
| `company_link`  | `bank_transfer`|

---

## 5. Aangepaste bestanden

### `src/services/rabbitmq_receiver.py`

| Handler | Gedrag |
|---------|--------|
| `payment_registered` | Leest `invoice/id`, `amount_paid`, `transaction/payment_method` en `user_id` uit het bericht; roept `pay_invoice()` aan; mapt `payment_method`; stuurt bevestiging naar `facturatie.to.crm` |

### `src/services/rabbitmq_sender.py`

| Functie | Doel |
|---------|------|
| `build_payment_confirmed_xml(invoice_id, customer_id, amount, currency, payment_method, paid_at)` | Bouwt het uitgaande `payment_registered` bericht voor CRM; valideert tegen `payement_registered_outgoing.xsd` |

### `src/services/fossbilling_api.py`

| Functie | Doel |
|---------|------|
| `pay_invoice(invoice_id, amount)` | Zet de factuur op status `paid` via `admin/invoice/update`; geeft `True` terug bij succes |

---

## 6. Lokaal testen

### Vereisten

| Wat | Commando |
|-----|---------|
| RabbitMQ draait | `docker compose up rabbitmq -d` |
| Receiver draait | `python -m src.services.rabbitmq_receiver` |
| FossBilling bereikbaar | check `.env` voor `BILLING_API_URL` en `BILLING_API_TOKEN` |

### Testscript

Pas `INVOICE_ID` bovenaan aan naar een bestaand factuur-ID uit FossBilling (bijvoorbeeld aangemaakt via de consumption flow):

```bash
python -m scripts.send_test_payment
```

### Verwacht resultaat

- **FossBilling** — factuur heeft status `paid`
- **RabbitMQ `facturatie.to.crm`** — een `payment_registered` bevestigingsbericht staat klaar
- **Receiver logs** — geen errors, je ziet de payment data geëxtraheerd en de bevestiging verstuurd

---

## 7. Security

| Gebied | Status |
|--------|--------|
| Credentials in code | Geen — alles via `.env` en `os.getenv()` |
| SQL injection | Niet van toepassing — geen database interactie in deze flow |
| XML injection (inkomend) | Beschermd — `defusedxml` gebruikt voor alle inkomende XML parsing |
| XML injection (uitgaand) | Beschermd — `ElementTree` escaped alle waarden automatisch |
