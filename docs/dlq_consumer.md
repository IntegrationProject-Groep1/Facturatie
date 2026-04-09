# DLQ Consumer — facturatie.dlq

## What is the DLQ?

The Dead Letter Queue (DLQ) is a special queue (`facturatie.dlq`) that receives messages that could not be processed by the main receiver. This happens when:

- The XML is invalid or corrupt (parse error)
- XSD validation fails (wrong structure)
- A FossBilling API call fails
- A required field is missing

Messages are forwarded to the DLQ by `send_to_dlq()` in `rabbitmq_utils.py`. This is a manual forward via `basic_publish` — not a native RabbitMQ Dead Letter Exchange.

---

## Message flow

```
facturatie.incoming
        │
        ▼
[RECEIVER] rabbitmq_receiver.py
        │
        ├── valid message → process normally
        │
        └── invalid message → send_to_dlq()
                                    │
                                    ▼
                            facturatie.dlq
                                    │
                                    ▼
                    [DLQ CONSUMER] dlq_consumer.py
                                    │
                                    ├── log message_type
                                    ├── log message_id
                                    ├── log correlation_id
                                    ├── log rejection reason (errors header)
                                    ├── print [ALERT][DLQ] line
                                    └── ack → queue stays clean
```

---

## Files

| File | Role |
|---|---|
| `src/services/dlq_consumer.py` | DLQ consumer — reads, logs, alerts, acks |
| `src/services/rabbitmq_utils.py` | `send_to_dlq()` — forwards bad messages to the DLQ |
| `src/main.py` | Starts both the receiver and DLQ consumer as threads |
| `scripts/send_bad_message.py` | Test script — sends invalid XML to trigger the DLQ |
| `tests/test_dlq_consumer.py` | Unit tests for the DLQ consumer |

---

## What the DLQ consumer logs

For every message received on `facturatie.dlq`:

```
[DLQ] Dead-letter message received
[DLQ] message_type     : new_registration
[DLQ] message_id       : abc123...
[DLQ] correlation_id   : def456...
[DLQ] original_queue   : unknown
[DLQ] rejection_errors : ERROR: xsd_validation: ...
[ALERT][DLQ] Unprocessed message | queue=unknown | type=new_registration | message_id=abc123 | reason=ERROR: ...
[DLQ] Message acknowledged (ack)
```

The `[ALERT][DLQ]` line is designed to be easy to grep or picked up by a monitoring tool.

---

## Headers

Messages forwarded by `send_to_dlq()` carry:

| Header | Content |
|---|---|
| `errors` | Semicolon-separated list of rejection reasons |
| `x-death` | Present only if a native RabbitMQ DLX is configured (not used here) |

---

## How to run

```powershell
.venv/Scripts/python -m src.main
```

This starts two threads:
- **Receiver** — listens on `facturatie.incoming`
- **DLQ consumer** — listens on `facturatie.dlq`

Both run automatically as long as the service is up.

---

## How to test

Send a bad message to trigger the full flow:

```powershell
.venv/Scripts/python scripts/send_bad_message.py
```

This sends invalid XML to `facturatie.incoming`. The receiver rejects it, forwards it to `facturatie.dlq`, and the DLQ consumer logs it.

To verify manually via the RabbitMQ UI:
1. Stop `src.main`
2. Run `scripts/send_bad_message.py`
3. Refresh the browser — `facturatie.dlq` shows **1**
4. Restart `src.main` — count drops back to **0**

---

## Queue responsibilities

| Queue | Owner | Consumed by |
|---|---|---|
| `facturatie.incoming` | Facturatie team | `rabbitmq_receiver.py` |
| `facturatie.dlq` | Facturatie team | `dlq_consumer.py` |
| `crm.to.facturatie` | CRM team / Infra | Not this service |

---

## Docker

The service runs via:

```dockerfile
CMD ["python", "-m", "src.main"]
```

Both the receiver and DLQ consumer start automatically inside the container.
