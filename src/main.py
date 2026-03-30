import sys
import threading
from services.rabbitmq_receiver import start_receiver


def main():
    print("Facturatie Integration Service is gestart.", flush=True)

    receiver_thread = threading.Thread(
        target=start_receiver,
        daemon=True
    )
    receiver_thread.start()
    print("Receiver gestart; luistert naar berichten...", flush=True)

    try:
        receiver_thread.join()
    except KeyboardInterrupt:
        print("\nService wordt afgesloten...")
        sys.exit(0)


if __name__ == "__main__":
    main()
