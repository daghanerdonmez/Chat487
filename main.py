import subprocess
import threading
import json
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

known_users = {}

username = "daghan"

stop_event = threading.Event()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("8.8.8.8", 80))
my_ip = s.getsockname()[0]
s.close()

cidr = "192.168.0.0/24"
all_hosts = [str(ip) for ip in ipaddress.ip_network(cidr, strict=False).hosts()]

PORT = 12487

def send_packet(ip: str, packet: dict):
    raw = json.dumps(packet)
    subprocess.run(
        ["nc", ip, str(PORT)],
        input = raw + "\n",
        text = True,
        check = False
    )

def send_ask(ip: str):
    packet = {
        "type": "ASK",
        "SENDER_IP": my_ip
    }
    raw = json.dumps(packet)
    try:
        subprocess.run(
            ["nc", ip, str(PORT)],
            input = raw + "\n",
            text = True,
            check = False,
            timeout = 0.4,
            stdout = subprocess.DEVNULL,
            stderr = subprocess.DEVNULL
        )
        print("oldu")
    except subprocess.TimeoutExpired:
        pass

def discover(all_hosts, my_ip):
    targets = [ip for ip in all_hosts if ip != my_ip]

    # tune workers: too high can overload machine/network
    with ThreadPoolExecutor(max_workers=64) as ex:
        futures = {ex.submit(send_ask, ip): ip for ip in targets}

        for f in as_completed(futures):
            ip = futures[f]
            try:
                f.result()   # raises if send_ask crashed
            except Exception as e:
                print(f"discover error {ip}: {e}")

def handle_received_packet(packet: str):
    print("a")
    packet = json.loads(packet)
    if packet["type"] == "ASK":
        reply = {
            "type": "REPLY",
            "RECEIVER_NAME": username,
            "RECEIVER_IP": my_ip
        }
        send_packet(packet["SENDER_IP"], reply)
    elif packet["type"] == "REPLY":
        receiver_name = packet["RECEIVER_NAME"]
        receiver_ip = packet["RECEIVER_IP"]
        if not known_users[receiver_ip]:
            known_users[receiver_ip] = receiver_name
            print(f'Discovered {receiver_name} @ {receiver_ip}')
        elif not known_users[receiver_ip] == receiver_name:
            print(f'Discovered {receiver_name} @ {receiver_ip}, [Address was previously used by {known_users[receiver_ip]}]')
            known_users[receiver_ip] = receiver_name
        else:
            pass
    elif packet["type"] == "MESSAGE":
        print(f'{packet["SENDER_NAME"]}: {packet["PAYLOAD"]}')
    else:
        pass


def listen_loop():
    while not stop_event.is_set():
        try:
            p = subprocess.run(
                ["nc", "-l", "-p", str(PORT)],
                capture_output = True,
                text = True,
                timeout = 1.0,
                check = False
            )
            raw = p.stdout.strip()
            if raw:
                handle_received_packet(raw)
        except subprocess.TimeoutExpired:
            pass

def main():
    mock_packet = {
        "type": "MESSAGE",
        "PAYLOAD": "Merhaba!",
        "SENDER_NAME": username,
        "SENDER_IP": my_ip
    }
    #send_packet("192.168.0.24", mock_packet)
    #discover()

    t = threading.Thread(target=listen_loop, daemon=True)
    t.start()

    while True:
        cmd = input("> ").strip()
        if cmd == "quit":
            stop_event.set()
            t.join(timeout=2)
            break
        elif cmd == "discover":
            discover(all_hosts, my_ip)
        else:
            print("unknown command")

main()