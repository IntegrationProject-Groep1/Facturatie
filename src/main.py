import time
import sys


def main():
    print("Facturatie Integration Service is gestart.", flush=True)
    print("Container draait en wacht op berichten...", flush=True)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("Service wordt afgesloten...")
        sys.exit(0)


if __name__ == "__main__":
    main()