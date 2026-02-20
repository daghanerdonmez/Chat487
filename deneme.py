import platform
import subprocess
import threading

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

    while not stop_event.is_set():
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            for raw in proc.stdout:
                raw = raw.strip()
                if raw:
                    print(f"received: {raw}")
                    break
                if stop_event.is_set():
                    break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()


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
