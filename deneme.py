import platform
import subprocess
import threading
import json

PORT = 12487
stop_event = threading.Event()


def get_listen_cmd(port: int):
    system = platform.system().lower()
    if system == "darwin":
        return ["nc", "-lk", str(port)]
    return ["nc", "-l", "-k", "-p", str(port)]


def listen_loop():
    cmd = get_listen_cmd(PORT)
    print(f"listening with: {' '.join(cmd)}")
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True) as proc:
        for line in proc.stdout:
            if stop_event.is_set():
                break
            raw = line.strip()
            print(raw)

        if proc.poll() is None:
            proc.terminate()


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
