# Documentation: Consumption Order & Invoice Flow

**Project:** Facturatie Microservice  
**Branch:** `feature/add-invoices-company`  
**Date:** 2026-05-03  
**Author:** Team Facturatie

---

## 1. Overview

This flow handles bar/kassa purchases made by employees of a company during an event. Each purchase arrives as a `consumption_order` XML message via RabbitMQ. The service stores all items per company in a MySQL table.

Invoices can be triggered in two ways:

- **Direct** — via an `invoice_request` message for one specific `consumption_order`, which creates an immediate invoice for those items.
- **Deferred** — via an `event_ended` message at the end of the event, which collects all remaining uninvoiced items per company and creates one consolidated invoice per company.

---

## 2. Architecture and message flow

```
Kassa / CRM
   |
   | consumption_order (RabbitMQ: facturatie.incoming)
   v
Facturatie service
   |-- Invalid XML           -->  Dead Letter Queue (facturatie.dlq)
   |-- XSD validation fail   -->  Dead Letter Queue (facturatie.dlq)
   |-- Duplicate message_id  -->  Acknowledged, skipped
   |-- No items in message   -->  Dead Letter Queue (facturatie.dlq)
   |-- DB save failed        -->  Dead Letter Queue (facturatie.dlq)
   |
   | Items saved to MySQL (pending_consumptions)
   | Indexed on consumption_order_id = message_id of the consumption_order
   |
   +--[Path A: invoice_request received]-----------------------------+
   |                                                                 |
   | invoice_request (RabbitMQ: facturatie.incoming)                |
   | correlation_id = message_id of the consumption_order           |
   |   --> update_meta_by_correlation_id() (company_name, email)    |
   |   --> get_items_by_correlation_id()                            |
   |   --> process_consumption_order() --> FossBilling invoice      |
   |   --> crm.to.mailing                                           |
   |   --> clear_by_ids() (only the matched rows)                   |
   |   |-- No items found    -->  Acknowledged, skipped             |
   |   |-- FossBilling fail  -->  Dead Letter Queue                 |
   |                                                                 |
   +--[Path B: event_ended received]--------------------------------+
   |
   | event_ended (RabbitMQ: facturatie.incoming)
   |   For each company_id with remaining pending items:
   |   --> get_items_for_company()
   |   --> process_consumption_order() --> FossBilling invoice
   |   --> crm.to.mailing
   |   --> clear_by_ids()
   |   |-- FossBilling fail  -->  Dead Letter Queue
```

---

## 3. XML message formats

### consumption_order (incoming)

Sent by Kassa (via CRM passthrough) when a purchase is made. One message per badge holder per transaction.

```xml
<message>
  <header>
    <message_id>78729c4c-a258-4c35-959c-a1d5805881a1</message_id>
    <type>consumption_order</type>
    <source>crm</source>
    <timestamp>2026-05-01T20:00:00Z</timestamp>
    <version>2.0</version>
  </header>
  <body>
    <is_anonymous>false</is_anonymous>
    <customer>
      <id>bedrijf-nv-001</id>
      <user_id>BADGE-001</user_id>
      <type>company</type>
      <email>jan.peeters@bedrijf.com</email>
    </customer>
    <items>
      <item>
        <id>LINE-0001</id>
        <sku>SKU-001</sku>
        <description>Coca-Cola</description>
        <quantity>2</quantity>
        <unit_price currency="eur">2.50</unit_price>
        <vat_rate>21</vat_rate>
        <total_amount currency="eur">5.00</total_amount>
      </item>
    </items>
  </body>
</message>
```

**Key fields:**
- `header/message_id` — wordt opgeslagen als `consumption_order_id` in MySQL, gebruikt als koppelingssleutel
- `body/customer/id` — `company_id`, gebruikt om items per bedrijf te groeperen
- `body/customer/user_id` — badge ID van de medewerker, verschijnt in de factuurlijn
- `body/items/item` — de geconsumeerde items met prijs en BTW

### invoice_request (incoming)

Verstuurd door CRM wanneer een factuur gevraagd wordt voor één specifieke `consumption_order`. De `correlation_id` in de header verwijst naar de `message_id` van de bijhorende `consumption_order`.

```xml
<message>
  <header>
    <message_id>e5bc986d-3b15-4525-835c-e6d733e69401</message_id>
    <type>invoice_request</type>
    <source>crm</source>
    <timestamp>2026-05-01T20:01:00Z</timestamp>
    <version>2.0</version>
    <correlation_id>78729c4c-a258-4c35-959c-a1d5805881a1</correlation_id>
  </header>
  <body>
    <user_id>BADGE-001</user_id>
    <invoice_data>
      <first_name>Jan</first_name>
      <last_name>Peeters</last_name>
      <email>jan.peeters@bedrijf.com</email>
      <address>
        <street>Teststraat</street>
        <number>1</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>BE</country>
      </address>
      <company_name>Bedrijf NV</company_name>
      <vat_number>BE0123456789</vat_number>
    </invoice_data>
  </body>
</message>
```

**Key fields:**
- `header/correlation_id` — verwijst naar de `message_id` van de `consumption_order` waarvoor de factuur gevraagd wordt
- `body/invoice_data/company_name` — wordt opgeslagen als facturatienaam en gebruikt als `company_id` sleutel
- `body/invoice_data/email` — ontvangt de factuurmelding via mailing

### event_ended (incoming)

Verstuurd door de frontend aan het einde van het event. Triggert de facturatie van alle resterende items waarvoor geen `invoice_request` is ontvangen.

```xml
<message>
  <header>
    <message_id>d112ac7f-5397-4104-95c7-a4043a1bbec7</message_id>
    <version>2.0</version>
    <type>event_ended</type>
    <timestamp>2026-05-01T22:00:00Z</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <session_id>SESSION-001</session_id>
    <ended_at>2026-05-01T22:00:00Z</ended_at>
  </body>
</message>
```

---

## 4. Waarom MySQL

FossBilling heeft geen API endpoint om items incrementeel toe te voegen aan een bestaande factuur. Een nieuwe factuur per bericht aanmaken zou tientallen losse facturen per bedrijf per event opleveren.

Items worden daarom opgeslagen in MySQL tijdens het event. Bij `invoice_request` of `event_ended` worden de items opgehaald en gebundeld in één factuur per bedrijf.

---

## 5. Database tabellen

Beide tabellen worden automatisch aangemaakt bij het opstarten van de service via `init_db()` in `consumption_store.py`. Kolommen die nog niet bestaan worden automatisch toegevoegd via migraties.

### pending_consumptions

Slaat alle consumption items op die nog niet gefactureerd zijn.

```sql
CREATE TABLE IF NOT EXISTS pending_consumptions (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    consumption_order_id  VARCHAR(100) NOT NULL,
    company_id            VARCHAR(100) NOT NULL DEFAULT '',
    company_name          VARCHAR(255) NOT NULL DEFAULT '',
    email                 VARCHAR(255) NOT NULL DEFAULT '',
    badge_id              VARCHAR(100) NOT NULL DEFAULT '',
    master_uuid           VARCHAR(36)  NOT NULL DEFAULT '',
    description           VARCHAR(255) NOT NULL,
    price                 DECIMAL(10,2) NOT NULL,
    quantity              INT          NOT NULL DEFAULT 1,
    vat_rate              VARCHAR(10),
    received_at           DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_company_id           (company_id),
    INDEX idx_consumption_order_id (consumption_order_id)
)
```

**Kolom toelichting:**
- `consumption_order_id` — de `message_id` van de `consumption_order`, gebruikt als koppelingssleutel met `invoice_request` via `correlation_id`
- `company_id` — `customer/id` uit de `consumption_order`, groepeert items per bedrijf voor `event_ended`
- `company_name` / `email` — worden ingevuld (of bijgewerkt) wanneer de bijhorende `invoice_request` binnenkomt
- `badge_id` — `customer/user_id` uit de `consumption_order`, verschijnt in de factuurlijn zodat het bedrijf kan traceren wie wat besteld heeft

### company_accounts

Slaat de koppeling op tussen een `company_id` en het bijhorende FossBilling billing account.

```sql
CREATE TABLE IF NOT EXISTS company_accounts (
    company_id            VARCHAR(100) PRIMARY KEY,
    fossbilling_client_id INT NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

Elke company krijgt een apart billing account met een gegenereerd emailadres (`billing.<company_id>@facturatie.be`). Zo gaat de geconsolideerde factuur altijd naar hetzelfde account, ongeacht hoeveel medewerkers van dat bedrijf al als FossBilling client staan.

---

## 6. Aangepaste bestanden

### `src/services/consumption_store.py`

| Functie | Doel |
|---------|------|
| `init_db()` | Maakt `pending_consumptions` en `company_accounts` tabellen aan bij opstarten, voert migraties uit |
| `save_items(company_id, badge_id, master_uuid, items, email, company_name, consumption_order_id)` | Slaat items op uit één `consumption_order` bericht |
| `get_items_by_correlation_id(correlation_id)` | Haalt items op via `consumption_order_id`, gebruikt door de `invoice_request` handler |
| `update_meta_by_correlation_id(correlation_id, company_name, email)` | Werkt `company_name` en `email` bij op de matching rijen wanneer een `invoice_request` binnenkomt |
| `get_pending_company_ids()` | Geeft alle `company_id`'s terug met niet-gefactureerde items, gebruikt door `event_ended` |
| `get_company_meta(company_id)` | Geeft `email` en `company_name` terug voor een bedrijf, gebruikt bij mailing in `event_ended` |
| `get_items_for_company(company_id)` | Geeft `(items, row_ids)` terug voor alle openstaande items van een bedrijf |
| `clear_by_ids(row_ids)` | Verwijdert specifieke verwerkte rijen op basis van database ID's |
| `get_company_client_id(company_id)` | Geeft het opgeslagen FossBilling billing `client_id` terug, of `None` |
| `save_company_client_id(company_id, client_id)` | Slaat de koppeling tussen `company_id` en billing account op |

### `src/services/fossbilling_api.py`

| Functie | Doel |
|---------|------|
| `_billing_email(company_id)` | Genereert een consistent billing emailadres voor een bedrijf |
| `_get_or_create_billing_client(company_id, company_name)` | Zoekt het billing account op in MySQL; maakt het aan in FossBilling als het nog niet bestaat |
| `process_consumption_order(company_id, items, company_name)` | Maakt één geconsolideerde factuur aan op het billing account van het bedrijf; herprobeert tot 3x |

### `src/services/rabbitmq_receiver.py`

| Handler | Gedrag |
|---------|--------|
| `consumption_order` | Leest `customer/id` als `company_id`, `customer/user_id` als `badge_id`, items uit `body/items`; slaat alles op via `save_items` met `consumption_order_id=msg_id` |
| `invoice_request` | Leest `correlation_id` uit header; werkt meta bij; haalt items op via `get_items_by_correlation_id`; maakt factuur aan; stuurt mailing; verwijdert verwerkte rijen |
| `event_ended` | Haalt alle resterende `company_id`'s op; maakt per bedrijf één factuur met alle openstaande items; stuurt mailing; verwijdert verwerkte rijen |

---

## 7. Factuurlijn formaat

Items worden in FossBilling aangemaakt met de badge ID in de titel zodat het bedrijf kan traceren wie wat besteld heeft:

```
Coca-Cola (badge: BADGE-001)
Water (badge: BADGE-001)
Fanta (badge: BADGE-002)
```

---

## 8. Lokaal testen

### Vereisten

| Wat | Commando |
|-----|---------|
| MySQL draait | `docker compose up mysql -d` |
| RabbitMQ draait | `docker compose up rabbitmq -d` |
| Mock identity service | `python -m scripts.mock_identity_service` (Terminal 1) |
| Receiver draait | `python -m src.services.rabbitmq_receiver` (Terminal 2) |
| FossBilling bereikbaar | check `.env` voor `BILLING_API_URL` en `BILLING_API_TOKEN` |

### Testscript

Het testscript simuleert de volledige flow in drie stappen:

```bash
python -m scripts.send_test_consumption_flow
```

**Stap 1** — Drie `consumption_order` berichten worden verstuurd (BADGE-001, BADGE-002, BADGE-003). Items worden opgeslagen in MySQL.

**Stap 2** — `invoice_request` voor BADGE-001 en BADGE-003. Beide orders worden direct gefactureerd via FossBilling. BADGE-002 krijgt bewust geen `invoice_request`.

**Stap 3** — `event_ended`. De resterende items van BADGE-002 worden opgepikt en gefactureerd.

### Verwacht resultaat

**FossBilling** — 3 facturen:
- Bedrijf NV billing account → Coca-Cola + Water (BADGE-001, via invoice_request)
- Tech Corp billing account → Koffie + Cola (BADGE-003, via invoice_request)
- Bedrijf NV billing account → Fanta (BADGE-002, via event_ended)

**RabbitMQ `crm.to.mailing`** — 3 `send_mailing` berichten.

**MySQL `pending_consumptions`** — leeg na verwerking.

### Database opkuisen voor herhaalde tests

```bash
docker exec -it <mysql-container-naam> mysql -u fossbilling -p<wachtwoord> fossbilling -e "DELETE FROM pending_consumptions; DELETE FROM company_accounts;"
```

---

## 9. Security

| Gebied | Status |
|--------|--------|
| Credentials in code | Geen — alles via `.env` en `os.getenv()` |
| `.env` in git | Uitgesloten via `.gitignore` |
| SQL injection | Beschermd — alle queries gebruiken geparametriseerde `%s` placeholders |
| XML injection (inkomend) | Beschermd — `defusedxml` gebruikt voor alle inkomende XML parsing |
| XML injection (uitgaand) | Beschermd — `ElementTree` escaped alle waarden automatisch |
| Virtual env in git | Beschermd — `.venv/` toegevoegd aan `.gitignore` |
