# Documentatie: Inschrijvingskosten-flow

**Project:** Facturatie Microservice
**Branch:** `Inschrijvingskosten-flow`
**Datum:** 2026-03-31
**Auteur:** Team Facturatie

---

## 1. Overzicht

De inschrijvingskosten-flow verwerkt nieuwe klantregistraties afkomstig van het Frontend team. Wanneer een nieuwe klant zich registreert, ontvangt de Facturatie service een bericht via RabbitMQ, maakt automatisch een factuur aan in het FossBilling facturatiesysteem, en stuurt vervolgens een bevestigingsbericht naar het Mailing team om de factuur naar de klant te verzenden.

---

## 2. Architectuur en berichtenstroom

```
Frontend
   |
   | new_registration (RabbitMQ: facturatie.incoming)
   v
Facturatie service
   |-- Validatie mislukt  -->  Dead Letter Queue (facturatie.dlq)
   |-- FossBilling fout   -->  Dead Letter Queue (facturatie.dlq)
   |
   | Klant + factuur aangemaakt (FossBilling API)
   |
   | invoice_request (RabbitMQ: facturatie.to.mailing)
   v
Mailing team
   |
   | Factuur verstuurd naar klant
   v
Klant
```

---

## 3. Berichtformaat

### 3.1 Inkomend bericht: `new_registration`

Ontvangen via queue: **`facturatie.incoming`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>550e8400-e29b-41d4-a716-446655440000</message_id>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>2026-03-31T10:00:00Z</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <customer>
      <email>klant@bedrijf.be</email>
      <is_company_linked>true</is_company_linked>
      <company_id>123</company_id>
      <company_name>Bedrijf NV</company_name>
      <address>
        <street>Kiekenmarkt</street>
        <number>42</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>be</country>
      </address>
    </customer>
    <registration_fee currency="eur">150.00</registration_fee>
  </body>
</message>
```

**Verplichte velden:**

| Veld | Beschrijving |
|---|---|
| `header/message_id` | Unieke berichtidentificatie (UUID) |
| `header/version` | Moet `2.0` zijn |
| `header/type` | Moet `new_registration` zijn (lowercase) |
| `header/timestamp` | ISO-8601 UTC formaat (bijv. `2026-03-31T10:00:00Z`) |
| `header/source` | Naam van het verzendende systeem |
| `body/customer/email` | E-mailadres van de klant |
| `body/customer/is_company_linked` | `true` of `false` |
| `body/customer/company_id` | Verplicht als `is_company_linked=true` |
| `body/customer/company_name` | Verplicht als `is_company_linked=true` |
| `body/customer/address/*` | Alle adresvelden verplicht (street, number, postal_code, city, country) |
| `body/registration_fee` | Bedrag van de inschrijvingskost |

### 3.2 Uitgaand bericht: `invoice_request`

Verstuurd naar queue: **`facturatie.to.mailing`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a1b2c3d4-e5f6-7890-abcd-ef1234567890</message_id>
    <version>2.0</version>
    <type>invoice_request</type>
    <timestamp>2026-03-31T10:00:05Z</timestamp>
    <source>facturatie</source>
    <correlation_id>550e8400-e29b-41d4-a716-446655440000</correlation_id>
  </header>
  <body>
    <invoice_id>INV-2026-001</invoice_id>
    <client_email>klant@bedrijf.be</client_email>
    <company_name>Bedrijf NV</company_name>
  </body>
</message>
```

De `correlation_id` in de header verwijst naar de `message_id` van het oorspronkelijke `new_registration` bericht, zodat het Mailing team berichten kan koppelen.

---

## 4. Verwerkingslogica

De service doorloopt bij elk ontvangen bericht de volgende stappen:

1. **XML-parsing** — ongeldig XML of verkeerde encoding → DLQ
2. **Duplicaatdetectie** — zelfde `message_id` reeds verwerkt → genegeerd (ACK)
3. **Validatie** — ontbrekende of ongeldige velden → DLQ
4. **Klant aanmaken in FossBilling** — idempotent: als het e-mailadres al bestaat, wordt de bestaande klant hergebruikt
5. **Factuur aanmaken in FossBilling** — endpoint: `admin/invoice/prepare`
6. **Retry-logica** — stappen 4 en 5 worden bij een fout tot **3 keer** herhaald met een vertraging van 2 seconden; na 3 mislukte pogingen → DLQ
7. **invoice_request versturen** naar `facturatie.to.mailing`
8. **ACK** — bericht wordt bevestigd als verwerkt

---

## 5. FossBilling integratie

De service communiceert met de FossBilling REST API via HTTP Basic Auth.

| Instelling | Omgevingsvariabele | Voorbeeld |
|---|---|---|
| API-URL | `BILLING_API_URL` | `https://server/api` |
| Gebruikersnaam | `BILLING_API_USERNAME` | `admin` |
| API-token | `BILLING_API_TOKEN` | *(gegenereerd in FossBilling)* |

Het API-token wordt gegenereerd via: **FossBilling admin → Account → API tokens → Generate new key**

---

## 6. Queues

| Queue | Richting | Doel |
|---|---|---|
| `facturatie.incoming` | Inkomend | Berichten van Frontend ontvangen |
| `facturatie.dlq` | Uitgaand | Ongeldige of gefaalde berichten |
| `facturatie.to.mailing` | Uitgaand | Factuurverzoeken naar Mailing team |

---

## 7. Foutafhandeling

| Situatie | Gedrag |
|---|---|
| Ongeldig XML | Doorgestuurd naar DLQ, bericht geweigerd (NACK) |
| Validatiefouten | Doorgestuurd naar DLQ met foutmelding in header, bericht geweigerd |
| Dubbel bericht | Genegeerd, bericht bevestigd (ACK) |
| FossBilling fout | Max. 3 pogingen, daarna DLQ + NACK |
| Bestaande klant | Bestaande client_id hergebruikt (idempotent) |

---

## 8. Geïmplementeerde bestanden

| Bestand | Status | Beschrijving |
|---|---|---|
| `src/services/rabbitmq_receiver.py` | Aangepast | Validatie, verwerking en DLQ-logica |
| `src/services/rabbitmq_sender.py` | Aangepast | `build_invoice_request_xml()` toegevoegd |
| `src/services/fossbilling_api.py` | Nieuw | FossBilling API-integratie met retry |
| `tests/test_validate_message.py` | Aangepast | +12 tests voor new_registration validatie |
| `tests/test_invoice_request.py` | Nieuw | 9 tests voor invoice_request builder |
| `tests/test_fossbilling_api.py` | Nieuw | 13 tests voor FossBilling API service |
| `tests/test_process_new_registration.py` | Nieuw | 7 tests voor process_message integratie |

---

## 9. Testresultaten

Alle bestaande en nieuwe tests slagen.

| Testbestand | Aantal tests |
|---|---|
| `test_validate_message.py` | 12 nieuw |
| `test_invoice_request.py` | 9 nieuw |
| `test_fossbilling_api.py` | 13 nieuw |
| `test_process_new_registration.py` | 7 nieuw |
| **Totaal nieuw** | **41 tests** |
| **Totaal project** | **63 tests geslaagd** |

---

## 10. Openstaande punten

- Het Mailing team dient de queue **`facturatie.to.mailing`** te implementeren en te monitoren.
