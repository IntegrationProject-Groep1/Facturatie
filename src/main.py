import sys
import threading
from src.services.rabbitmq_receiver import start_receiver
from src.services.dlq_consumer import start_dlq_consumer


def main():
    print("Facturatie Integration Service is gestart.", flush=True)

    receiver_thread = threading.Thread(
        target=start_receiver,
        daemon=True
    )
    receiver_thread.start()
    print("Receiver gestart; luistert naar berichten...", flush=True)

    dlq_thread = threading.Thread(
        target=start_dlq_consumer,
        daemon=True
    )
    dlq_thread.start()
    print("DLQ consumer started; listening on facturatie.dlq...", flush=True)

    try:
        receiver_thread.join()
        dlq_thread.join()
    except KeyboardInterrupt:
        print("\nService wordt afgesloten...")
        sys.exit(0)


if __name__ == "__main__":
    main()
