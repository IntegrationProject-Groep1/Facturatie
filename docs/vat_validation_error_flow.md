# Documentation: VAT Validation Error Flow

**Project:** Facturatie Microservice
**Date:** 2026-05-14
**Author:** Team Facturatie

---

## 1. Overview

When a `new_registration` message contains a VAT number that does not match the expected Belgian format (`BE` + 10 digits), the Facturatie service does not reject the message to the DLQ. Instead, it publishes a `vat_validation_error` notification to the Frontend team so the customer can be prompted to correct their VAT number, and then ACKs the original message.

---

## 2. Message flow

```
CRM
   |
   | new_registration (crm.to.facturatie) — contains invalid VAT number
   v
Facturatie service
   |
   | XSD validation passes (VAT format is not enforced by XSD)
   |
   | _is_valid_vat(vat_number) → False
   |
   | publish_vat_validation_error() --> facturatie.to.frontend
   v
   ACK (no invoice created, no DLQ)
```

If the VAT number is absent, the registration proceeds normally — absence is not an error.

---

## 3. Outgoing message: `vat_validation_error`

**Queue:** `facturatie.to.frontend`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>a1b2c3d4-e5f6-7890-abcd-ef1234567890</message_id>
    <version>2.0</version>
    <type>vat_validation_error</type>
    <timestamp>2026-05-14T10:00:00Z</timestamp>
    <source>facturatie</source>
    <correlation_id>550e8400-e29b-41d4-a716-446655440000</correlation_id>
  </header>
  <body>
    <identity_uuid>a1b2c3d4-e5f6-7890-abcd-ef1234567890</identity_uuid>
    <vat_number>BE012345</vat_number>
    <error_message>BTW-nummer BE012345 heeft een ongeldig formaat</error_message>
    <timestamp>2026-05-14T10:00:00Z</timestamp>
  </body>
</message>
```

**Field description:**

| Field | Required | Description |
|---|---|---|
| `header/correlation_id` | yes | `message_id` of the original `new_registration` message |
| `body/identity_uuid` | no | Master UUID of the customer |
| `body/vat_number` | yes | The invalid VAT number as received |
| `body/error_message` | no | Human-readable description of the validation failure |
| `body/timestamp` | yes | UTC timestamp of when the error was detected |

---

## 4. Validation rule

```python
def _is_valid_vat(vat_number: str) -> bool:
    return bool(re.match(r'^BE\d{10}$', vat_number.upper()))
```

Valid format: `BE` followed by exactly 10 digits (case-insensitive). Examples:
- `BE0123456789` → valid
- `BE012345678` → invalid (9 digits)
- `0123456789` → invalid (missing `BE` prefix)
- `BE012345678X` → invalid (non-digit character)

---

## 5. Implementation

**Builder:** `build_vat_validation_error_xml(vat_number, identity_uuid, error_message, correlation_id)` in `src/services/rabbitmq_sender.py`

**Publisher:** `publish_vat_validation_error(vat_number, identity_uuid, error_message, correlation_id, channel)` in `src/services/rabbitmq_sender.py`

The outgoing XML is validated against `xsd/vat_validation_error.xsd` before publishing.

---

## 6. Error handling

| Situation | Behaviour |
|---|---|
| VAT number absent | Registration proceeds normally — no error published |
| VAT number present but invalid format | `vat_validation_error` published to Frontend, message ACKed, no invoice created |
| `publish_vat_validation_error` fails | Warning logged, message ACKed anyway — the Frontend notification is best-effort |
