import logging
import sys
import time
import threading
from src.services.rabbitmq_receiver import start_receiver
from src.services.dlq_consumer import start_dlq_consumer
from src.services.consumption_store import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main():
    print("Facturatie Integration Service started.", flush=True)
    init_db()

    receiver_thread = threading.Thread(target=start_receiver, daemon=True)
    receiver_thread.start()
    print("Receiver started; listening for messages...", flush=True)

    dlq_thread = threading.Thread(target=start_dlq_consumer, daemon=True)
    dlq_thread.start()
    print("DLQ consumer started; listening on facturatie.dlq...", flush=True)

    try:
        while True:
            if not receiver_thread.is_alive():
                print("[MAIN] ERROR: receiver thread died — shutting down.", flush=True)
                sys.exit(1)
            if not dlq_thread.is_alive():
                print("[MAIN] ERROR: DLQ consumer thread died — shutting down.", flush=True)
                sys.exit(1)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nService shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
