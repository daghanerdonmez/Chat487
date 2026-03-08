import threading
import json
import socket
import select
import time


class Chat():
    def __init__(self, username):
        self.PORT = 12487 # Change this variable to change the port to be used for communication.
        self.UDP_PORT = 5566
        self.DISCOVER_INTERVAL_SECONDS = 10

        self.username = username
        self.ip_chatting = None
        self.chatting_name = None
        self.menuextra = ""

        self.known_users = {}
        self.known_users_chats = {}

        self.stop_event = threading.Event()

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        self.my_ip = s.getsockname()[0]
        s.close()

        self.listener_sock = None
        self.listener_lock = threading.Lock()

        self.udp_listener_sock = None
        self.udp_listener_lock = threading.Lock()
        self.discover_thread = None
        self.discover_lock = threading.Lock()

        self.state = 0

    def _message_packet(self, message: str):
        return {
                "type": "MESSAGE",
                "PAYLOAD": message,
                "SENDER_NAME": self.username,
                "SENDER_IP": self.my_ip
            }
    
    def _send_packet(self, ip: str, packet: dict):
        if packet.get("type") == "MESSAGE":
            payload = str(packet.get("PAYLOAD", ""))
            payload_size = len(payload.encode("utf-8"))
            if payload_size > 2048:
                print(f"Message too large ({payload_size} bytes). Max allowed is 2048 bytes.")
                return False

        raw = json.dumps(packet)
        try:
            with socket.create_connection((ip, self.PORT), timeout=1.0) as s:
                s.sendall((raw + "\n").encode("utf-8"))
            return True
        except OSError:
            return False

    def _send_ask_broadcast(self):
        packet = {
            "type": "ASK",
            "SENDER_IP": self.my_ip
        }
        raw = json.dumps(packet).encode("utf-8")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind(("", 0))  
                sock.sendto(raw, ("<broadcast>", self.UDP_PORT)) 
            return True
        except OSError as e:
            print(f"broadcast failed: {e}")
            return False
        
    def _discover_worker(self):
        for _ in range(3):
            if self.stop_event.is_set():
                return
            self._send_ask_broadcast()
            time.sleep(0.2)

    def _auto_discover_loop(self):
        while not self.stop_event.is_set():
            self._discover_worker()
            # Wait in small steps so stop_event can terminate promptly.
            for _ in range(self.DISCOVER_INTERVAL_SECONDS * 10):
                if self.stop_event.is_set():
                    return
                time.sleep(0.1)

    def _start_discover_thread(self):
        with self.discover_lock:
            if self.discover_thread is not None and self.discover_thread.is_alive():
                return False
            self.discover_thread = threading.Thread(target=self._discover_worker, daemon=True)
            self.discover_thread.start()
            return True
        
    def _udp_listen_loop(self):
        port = self.UDP_PORT 
        buffer_size = 1024

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        s.setblocking(0)

        with self.udp_listener_lock:
            self.udp_listener_sock = s

        try:
            while not self.stop_event.is_set():
                result = select.select([s], [], [], 1.0)  # timeout keeps loop stoppable
                if not result[0]:
                    continue

                msg = result[0][0].recv(buffer_size)
                if not msg:
                    continue

                raw = msg.decode("utf-8", errors="replace").strip()
                if raw:
                    self._handle_received_packet(raw)
        finally:
            with self.udp_listener_lock:
                if self.udp_listener_sock is s:
                    self.udp_listener_sock = None
            s.close()

    def _handle_received_packet(self, packet: str):
        #print("a")
        try:
            packet = json.loads(packet)
        except json.decoder.JSONDecodeError:
            #print("evet json error")
            return
        if packet["type"] == "ASK":
            sender_ip = packet.get("SENDER_IP")
            if sender_ip == self.my_ip:
                return  # ignore our own broadcast ASK
            #print("b")
            reply = {
                "type": "REPLY",
                "RECEIVER_NAME": self.username,
                "RECEIVER_IP": self.my_ip
            }
            self._send_packet(packet["SENDER_IP"], reply)
        elif packet["type"] == "REPLY":
            #print("f")
            receiver_name = packet["RECEIVER_NAME"]
            receiver_ip = packet["RECEIVER_IP"]
            if receiver_ip == self.my_ip:
                return
            if receiver_ip not in self.known_users:
                self.known_users[receiver_ip] = receiver_name
                self.known_users_chats.setdefault(receiver_ip, [])
                print(f'Discovered {receiver_name} @ {receiver_ip}')
            elif self.known_users[receiver_ip] != receiver_name:
                print(f'Discovered {receiver_name} @ {receiver_ip}, [Address was previously used by {self.known_users[receiver_ip]}]')
                self.known_users[receiver_ip] = receiver_name
                self.known_users_chats.setdefault(receiver_ip, [])
            else:
                pass

            if self.state == 0:
                self._clear_window()
                self._render_menu()
                print("> ", end="", flush=True)

        elif packet["type"] == "MESSAGE":
            #print("d")
            sender_ip = packet["SENDER_IP"]
            sender_name = packet["SENDER_NAME"]
            payload = packet["PAYLOAD"]
            self.known_users_chats.setdefault(sender_ip, []).append((sender_name, payload))
            #print(f'{sender_name}: {payload}')
            if self.state == 1:
                self._clear_window()
                self._render_chat()

        else:
            pass
            #print("handleelse")


    def _tcp_listen_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(("", self.PORT))
            server_sock.listen()
            server_sock.settimeout(1.0)

            with self.listener_lock:
                self.listener_sock = server_sock

            while not self.stop_event.is_set():
                try:
                    conn, addr = server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                with conn:
                    chunks = []
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                        if b"\n" in data:
                            break

                    raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
                    if raw:
                        self._handle_received_packet(raw)

            with self.listener_lock:
                if self.listener_sock is server_sock:
                    self.listener_sock = None


    def _stop_listener_socket(self):
        with self.listener_lock:
            sock = self.listener_sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _stop_udp_listener_socket(self):
        with self.udp_listener_lock:
            sock = self.udp_listener_sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _clear_window(self):
        print("\x1b[2J\x1b[H", end="")

    def _render_menu(self):
        print("487 Chat App")
        print("Write '\quit' to quit")
        print("New users are discovered automatically every 10 seconds.")
        print("Write '\discover' to manually discover new users around you.")
        print("Write the name of a user to chat with them.")
        print(self.menuextra)
        print()
        print("Users you already know:")
        print(self.known_users)

    def _render_chat(self):
        print("Chatting with", self.chatting_name)
        print("Type '\menu' to return to menu or '\quit' to quit.")
        print()
        if self.ip_chatting is None:
            return

        for sender_name, message in self.known_users_chats.get(self.ip_chatting, []):
            print(f"{sender_name}: {message}")

    def _find_ip(self, users, username):
        return next((ip for ip, user in users.items() if user == username), None)

    def run(self):
        tcp_listen_thread = None
        udp_listen_thread = None
        auto_discover_thread = None
        try:
            self._clear_window()
            self.state = 0

            tcp_listen_thread = threading.Thread(target=self._tcp_listen_loop, daemon=True)
            tcp_listen_thread.start()

            udp_listen_thread = threading.Thread(target=self._udp_listen_loop, daemon=True)
            udp_listen_thread.start()

            auto_discover_thread = threading.Thread(target=self._auto_discover_loop, daemon=True)
            auto_discover_thread.start()

            while True:
                if self.state == 0:
                    self._clear_window()
                    self._render_menu()
                elif self.state == 1:
                    self._clear_window()
                    self._render_chat()

                cmd = input("> ").strip()
                self.menuextra = ""

                if self.state == 0:
                    if cmd == "\quit":
                        break
                    elif cmd == "\discover":
                        started = self._start_discover_thread()
                        if not started:
                            self.menuextra = "Discovery is already running."
                    else:
                        self.ip_chatting = self._find_ip(self.known_users, cmd)
                        if self.ip_chatting is not None:
                            self.state = 1
                            self.chatting_name = cmd
                            self.known_users_chats.setdefault(self.ip_chatting, [])
                        else:
                            self.menuextra = "No user with name " + cmd
                else:
                    if cmd == "\quit":
                        break
                    elif cmd == "\menu":
                        self.state = 0
                    else:
                        sent = self._send_packet(self.ip_chatting, self._message_packet(cmd))
                        if sent:
                            self.known_users_chats.setdefault(self.ip_chatting, []).append((self.username, cmd))
        finally:
            self.stop_event.set()
            self._stop_listener_socket()
            self._stop_udp_listener_socket()
            if tcp_listen_thread is not None:
                tcp_listen_thread.join(timeout=2)
            if udp_listen_thread is not None:
                udp_listen_thread.join(timeout=2)
            if auto_discover_thread is not None:
                auto_discover_thread.join(timeout=2)

if __name__ == "__main__":
    print("\x1b[2J\x1b[H", end="")
    username = ""
    while not username:
        username = input("Enter username: ").strip()
        if not username:
            print("Username cannot be empty.")
    chatapp = Chat(username)
    chatapp.run()