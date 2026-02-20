import platform
import subprocess
import threading
import json

NC = "/usr/bin/nc"

PORT = 12487
stop_event = threading.Event()


def get_listen_cmd(port: int):
    system = platform.system().lower()
    if system == "darwin":
        return [NC, "-lk", str(port)]
    return [NC, "-l", "-k", "-p", str(port)]


def listen_loop():
    cmd = get_listen_cmd(PORT)

    while not stop_event.is_set():
        # On macOS this handles one connection, then process exits.
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True) as proc:
            for line in proc.stdout:
                if stop_event.is_set():
                    break
                raw = line.strip()
                if raw:
                    print(raw)


def main():
    t = threading.Thread(target=listen_loop, daemon=True)
    t.start()

    while True:
        cmd = input("> ").strip().lower()
        if cmd == "quit":
            stop_event.set()
            t.join(timeout=2)
            break


if __name__ == "__main__":
    main()
