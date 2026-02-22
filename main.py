#CONFIGURATION

NC = "/usr/bin/nc"
PORT = 12487

#CONFIGURATION

import subprocess
import threading
import json
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform

username = ""
ip_chatting = None
chatting_name = None
menuextra = ""

known_users = {}
known_users_chats = {}

stop_event = threading.Event()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("8.8.8.8", 80))
my_ip = s.getsockname()[0]
s.close()

local_network = ipaddress.ip_network(f"{my_ip}/24", strict=False)
all_hosts = [str(ip) for ip in local_network.hosts()]

listener_proc = None
listener_lock = threading.Lock()

def get_listen_cmd(port: int):
    system = platform.system().lower()
    if system == "darwin":
        return [NC, "-lk", str(port)]            # macOS/BSD nc
    else:
        return [NC, "-l", "-k", "-p", str(port)] # your Debian variant
    
def get_send_cmd(ip:int, port: int):
    system = platform.system().lower()
    if system == "darwin":
        return [NC, "-N", "0", str(ip), str(port)]
    else:
        return [NC, "-N", str(ip), str(port)]
    
def message_packet(message: str):
    return {
            "type": "MESSAGE",
            "PAYLOAD": message,
            "SENDER_NAME": username,
            "SENDER_IP": my_ip
        }

def send_packet(ip: str, packet: dict):
    if packet.get("type") == "MESSAGE":
        payload = str(packet.get("PAYLOAD", ""))
        payload_size = len(payload.encode("utf-8"))
        if payload_size > 2048:
            print(f"Message too large ({payload_size} bytes). Max allowed is 2048 bytes.")
            return False

    raw = json.dumps(packet)
    subprocess.run(
        get_send_cmd(ip, PORT),
        input = raw + "\n",
        text = True,
        check = False
    )
    return True

def send_ask(ip: str):
    packet = {
        "type": "ASK",
        "SENDER_IP": my_ip
    }
    raw = json.dumps(packet)
    try:
        subprocess.run(
            get_send_cmd(ip, PORT),
            input = raw + "\n",
            text = True,
            check = False,
            timeout = 1,
            stdout = subprocess.DEVNULL,
            stderr = subprocess.DEVNULL
        )
        #print(f"oldu @ {ip}")
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

    print(known_users)

def handle_received_packet(packet: str):
    #print("a")
    try:
        packet = json.loads(packet)
    except json.decoder.JSONDecodeError:
        #print("evet json error")
        return
    if packet["type"] == "ASK":
        #print("b")
        reply = {
            "type": "REPLY",
            "RECEIVER_NAME": username,
            "RECEIVER_IP": my_ip
        }
        send_packet(packet["SENDER_IP"], reply)
    elif packet["type"] == "REPLY":
        #print("f")
        receiver_name = packet["RECEIVER_NAME"]
        receiver_ip = packet["RECEIVER_IP"]
        if receiver_ip not in known_users:
            known_users[receiver_ip] = receiver_name
            known_users_chats.setdefault(receiver_ip, [])
            print(f'Discovered {receiver_name} @ {receiver_ip}')
        elif known_users[receiver_ip] != receiver_name:
            print(f'Discovered {receiver_name} @ {receiver_ip}, [Address was previously used by {known_users[receiver_ip]}]')
            known_users[receiver_ip] = receiver_name
            known_users_chats.setdefault(receiver_ip, [])
        else:
            pass
    elif packet["type"] == "MESSAGE":
        #print("d")
        sender_ip = packet["SENDER_IP"]
        sender_name = packet["SENDER_NAME"]
        payload = packet["PAYLOAD"]
        known_users_chats.setdefault(sender_ip, []).append((sender_name, payload))
        #print(f'{sender_name}: {payload}')
        if state == 1:
            clear_window()
            render_chat()

    else:
        pass
        #print("handleelse")


def listen_loop():
    global listener_proc
    cmd = get_listen_cmd(PORT)

    while not stop_event.is_set():
        # On macOS this handles one connection, then process exits.
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True) as proc:
            with listener_lock:
                listener_proc = proc

            for line in proc.stdout:
                if stop_event.is_set():
                    break
                raw = line.strip()
                if raw:
                    #print(raw)
                    handle_received_packet(raw)

            with listener_lock:
                if listener_proc is proc:
                    listener_proc = None

def stop_listener_proc():
    with listener_lock:
        proc = listener_proc
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()

def clear_window():
    print("\x1b[2J\x1b[H", end="")  # clear + home

def render_menu():
    print("487 Chat App")
    print("Write '\quit' to quit")
    print("Write '\discover' to discover new users around you.")
    print("Write the name of a user to chat with them.")
    print(menuextra)
    print()
    print("Users you already know:")
    print(known_users)

def render_chat():
    print("Chatting with", chatting_name)
    print("Type '\menu' to return to menu or '\quit' to quit.")
    print()
    if ip_chatting is None:
        return

    for sender_name, message in known_users_chats.get(ip_chatting, []):
        print(f"{sender_name}: {message}")


def find_ip(users, username):
    return next((ip for ip, user in users.items() if user == username), None)

def main():
    global username
    global chatting_name
    global ip_chatting
    global menuextra
    global state

    try:
        clear_window()
        
        while not username:
            username = input("Enter username: ").strip()
            if not username:
                print("Username cannot be empty.")

        state = 0
        #send_packet("192.168.0.24", mock_packet)
        #discover()

        t = threading.Thread(target=listen_loop, daemon=True)
        t.start()

        while True:
            if state == 0:
                clear_window()
                render_menu()
            elif state == 1:
                clear_window()
                render_chat()

            cmd = input("> ").strip()
            menuextra = ""
            if state == 0:
                if cmd == "\quit":
                    break
                elif cmd == "\discover":
                    discover(all_hosts, my_ip)
                else:
                    ip_chatting = find_ip(known_users, cmd)
                    if ip_chatting is not None:
                        state = 1
                        chatting_name = cmd
                        known_users_chats.setdefault(ip_chatting, [])
                    else:
                        menuextra = "No user with name " + cmd
                    #send_packet(other_ip, mock_packet)
            elif state == 1:
                if cmd == "\quit":
                    break
                elif cmd == "\menu":
                    state = 0
                else:
                    sent = send_packet(ip_chatting, message_packet(cmd))
                    if sent:
                        known_users_chats.setdefault(ip_chatting, []).append((username, cmd))

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        stop_listener_proc()
        t.join(timeout=2)

main()
