import sys
import threading
from services.rabbitmq_receiver import start_receiver


def main():
    print("Facturatie Integration Service started.", flush=True)

    receiver_thread = threading.Thread(
        target=start_receiver,
        daemon=True
    )
    receiver_thread.start()
    print("Receiver started; listening for messages...", flush=True)

    try:
        receiver_thread.join()
    except KeyboardInterrupt:
        print("\nService shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
