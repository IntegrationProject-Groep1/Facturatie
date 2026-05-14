# Architecture — Facturatie Service

## System context

The Facturatie service sits at the centre of the event platform's financial processing. It is one of several microservices that communicate exclusively via RabbitMQ XML messages.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RabbitMQ message bus                         │
└──────────┬──────────────────────────────────────────────────────────┘
           │ crm.to.facturatie
           │ (new_registration, consumption_order, invoice_request,
           │  event_ended, payment_registered, invoice_cancelled,
           │  profile_update, heartbeat)
           ▼
┌─────────────────────┐         ┌──────────────────────┐
│                     │─────────▶  FossBilling (REST)  │
│   Facturatie        │         │  + MySQL (fossbilling │
│   Service           │◀─────────  database)            │
│                     │         └──────────────────────┘
└──────────┬──────────┘
           │ Outgoing messages
           ├──▶ facturatie.to.mailing      (Mailing team)
           ├──▶ facturatie.to.frontend     (Frontend team)
           ├──▶ crm.incoming               (CRM team)
           ├──▶ facturatie.dlq             (dead-letter queue)
           ├──▶ errors.facturatie          (Monitoring team)
           └──▶ logs                       (Central logging)
```

External services the Facturatie service calls:
- **FossBilling** — invoicing system (HTTP REST via admin web session)
- **Identity service** — resolves customer email → master UUID (HTTP)
- **MySQL** — stores pending consumption items until an invoice is triggered

---

## Internal components

```
src/
│
├── main.py
│   Bootstraps the service: initialises MySQL, declares owned queues,
│   then spawns two daemon threads and monitors them.
│
├── services/rabbitmq_receiver.py
│   The core of the service. One pika consumer on crm.to.facturatie.
│   process_message() dispatches on header/type, runs XSD validation,
│   calls FossBilling, and publishes outgoing messages.
│
├── services/rabbitmq_sender.py
│   Stateless XML builders and publishers. Each outgoing message type
│   has a build_*_xml() function (ElementTree, never string concat)
│   and a publish_*() wrapper that calls send_message().
│
├── services/fossbilling_api.py
│   HTTP client for the FossBilling admin REST API. Maintains a
│   thread-safe lazy session. All write operations retry up to
│   MAX_RETRIES=3 times with a 2-second delay.
│
├── services/consumption_store.py
│   MySQL connection pool (size 5). Stores consumption_order items in
│   pending_consumptions until an invoice_request or event_ended
│   message triggers invoice generation.
│
├── services/dlq_consumer.py
│   Second daemon thread. Reads from facturatie.dlq, logs each failed
│   message, and forwards it to errors.facturatie for Monitoring.
│
├── services/rabbitmq_utils.py
│   Low-level helpers: get_connection(), get_connection_with_retry(),
│   send_to_dlq().
│
├── services/identity_client.py
│   HTTP call to the identity service to resolve email → master UUID.
│   Used during new_registration processing.
│
└── utils/xml_validator.py
    validate_xml(xml_str, schema_name) — loads xsd/{schema_name}.xsd
    and validates. Called on every inbound message before dispatch,
    and on every outbound message before publishing.
```

---

## Message processing pipeline

Every inbound message follows this pipeline inside `process_message()`:

```
Raw bytes from RabbitMQ
        │
        ▼
1. XML parse (defusedxml — blocks XXE attacks)
        │ ParseError → DLQ + NACK
        ▼
2. Duplicate detection (in-memory seen_message_ids)
        │ duplicate → ACK (silently discard)
        ▼
3. XSD validation (validate_xml against xsd/{type}.xsd)
        │ invalid → send_log(error) + send_system_error + DLQ + NACK
        ▼
4. Mark message_id as seen
        │
        ▼
5. Business logic dispatch (elif msg_type == ...)
        │ exception → DLQ + NACK
        ▼
6. Publish outgoing message(s)
        │
        ▼
7. ACK
```

---

## Flow summary

| Trigger message | What Facturatie does | Outgoing messages |
|---|---|---|
| `new_registration` | Creates FossBilling client + invoice, fetches PDF | `send_mailing` → Mailing, `invoice_available` → Frontend, `invoice_status` → CRM |
| `consumption_order` | Saves items to MySQL pending store | — |
| `invoice_request` | Flushes pending items for one company → invoice | `send_mailing` → Mailing, `invoice_available` → Frontend, `invoice_status` → CRM |
| `event_ended` | Flushes ALL pending companies → one invoice each | Same as invoice_request, per company |
| `payment_registered` | Marks invoice paid in FossBilling (or sets pending for partial) | `payment_registered` confirmation → CRM, `invoice_status` → CRM |
| `invoice_cancelled` | Cancels invoice or creates credit note | `invoice_cancelled` → CRM, `invoice_status` → CRM |
| `profile_update` | Updates client record in FossBilling | — |
| `heartbeat` | ACK, no action | — |

---

## Queue ownership

Facturatie declares only the queues it owns. It must not re-declare queues owned by other teams (e.g. `crm.to.facturatie` is owned by CRM).

| Queue | Owner | Notes |
|---|---|---|
| `crm.to.facturatie` | CRM | Facturatie reads from it; CRM declares it with DLX |
| `facturatie.to.mailing` | Facturatie | Declared on startup |
| `facturatie.to.frontend` | Facturatie | Declared on startup |
| `facturatie.dlq` | Facturatie | Written to on error |
| `crm.incoming` | CRM | Facturatie writes to it |
| `errors.facturatie` | Monitoring | Facturatie writes system_error messages |
| `logs` | Monitoring | Facturatie writes log messages |

---

## Database schema

Managed by `consumption_store.init_db()` — runs `CREATE TABLE IF NOT EXISTS` on startup.

### `pending_consumptions`

Holds consumption order items awaiting invoice generation.

| Column | Type | Description |
|---|---|---|
| `id` | INT AUTO_INCREMENT PK | |
| `consumption_order_id` | VARCHAR(100) | `header/message_id` of the source `consumption_order` |
| `company_id` | VARCHAR(100) | Customer / company identifier from CRM |
| `badge_id` | VARCHAR(100) | Attendee badge / identity UUID |
| `master_uuid` | VARCHAR(100) | Master UUID from identity service |
| `email` | VARCHAR(255) | Customer email (populated on `invoice_request`) |
| `company_name` | VARCHAR(255) | Company display name (populated on `invoice_request`) |
| `description` | TEXT | Item description |
| `price` | DECIMAL(10,2) | Unit price |
| `quantity` | INT | Quantity |
| `vat_rate` | VARCHAR(10) | VAT rate (6, 12, or 21) |
| `correlation_id` | VARCHAR(100) | Links `consumption_order` to its `invoice_request` |

### `company_accounts`

Caches FossBilling client IDs per company to avoid redundant API calls.

| Column | Type | Description |
|---|---|---|
| `company_id` | VARCHAR(100) PK | |
| `client_id` | VARCHAR(100) | FossBilling internal client ID |

---

## Deployment

### Local (Docker)

```bash
cp .env.example .env      # configure credentials
docker compose up -d      # starts FossBilling, MySQL, DLQ consumer
python -m src.main        # run the main service locally
```

### CI/CD

- **CI** (`ci.yml`): runs on push to `main`, `dev`, `prod` — flake8 lint + pytest unit tests.
- **Deploy** (`deploy.yml`): triggered after CI passes on `main`.

### Port

The service exposes port `30010` in the Docker image (defined in `Dockerfile`). The container does not serve HTTP — this port is reserved for future health-check or metrics endpoints.

---

## Error handling strategy

| Situation | Behaviour |
|---|---|
| Unparseable XML | DLQ + NACK |
| XSD validation failure | `send_log(error)` + `send_system_error` to Monitoring + DLQ + NACK |
| Duplicate `message_id` | ACK, silently discard |
| FossBilling unreachable | Retry 3× with 2 s delay → DLQ + NACK |
| Identity service failure | DLQ + NACK |
| Invalid VAT number format | `vat_validation_error` → Frontend + ACK (not a fatal error) |
| Partial payment | Invoice status set to `pending`; no FossBilling `pay_invoice` call |
| Cancellation of paid invoice | Credit note created instead of direct cancellation |
| Cancellation of consumption invoice | Blocked — `cancellation_failed` → CRM + ACK |

---

## Security notes

- XML parsed with `defusedxml` to prevent XXE/entity expansion attacks.
- All outgoing XML built with `ElementTree` — user-controlled strings are never concatenated into XML.
- SQL queries use parameterised statements — no string interpolation in queries.
- FossBilling credentials stored in `.env` (gitignored), injected via environment variables.
- Docker image runs as non-root user `appuser` (UID 1000).
