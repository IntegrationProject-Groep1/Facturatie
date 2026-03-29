# Facturatie

RabbitMQ integration service for FOSSBilling. Handles sending and receiving billing messages over a RabbitMQ queue.

## Project structure

```
Facturatie/
├── conftest.py                  # Pytest path configuration
├── requirements.txt
├── .env                         # RabbitMQ credentials (not committed)
├── docs/
│   └── devlog.md                # Development log
├── src/
│   └── services/
│       ├── rabbitmq_sender.py   # Builds and publishes XML messages
│       └── rabbitmq_receiver.py # Consumes, validates, and routes messages
└── tests/
    └── test_validate_message.py # Unit tests for validation logic
```

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest
```

## Running

```bash
# Send a test message
python src/services/rabbitmq_sender.py

# Start the receiver
python src/services/rabbitmq_receiver.py
```

## Tests

```bash
pytest tests/ -v
```

## Message types

| Type | Description |
|---|---|
| `CONSUMPTION_ORDER` | Order placed at a bar/kassa |
| `PAYMENT_REGISTERED` | Payment confirmation (requires `correlation_id`) |
| `HEARTBEAT` | Health check message |

Invalid messages (bad VAT rate, missing fields, wrong version) are forwarded to `facturatie.dlq`.

## Dev log

See [docs/devlog.md](docs/devlog.md) for a full history of changes.