import socket
import json
from concurrent.futures import ThreadPoolExecutor
import time
import threading
import readline
import os
import sys
import platform
import select
import base64


class ChatApp:
    def __init__(self, username):
        # Constants
        self.PORT = 12487
        self.MSG_TYPES = ["ASK", "REPLY", "MESSAGE"]
        self.SCAN_INTERVAL = 10
        self.KEYWORDS = {
                "/exit": self._exit_app,
                "/users": self._print_users,
                "/help": self._print_help,
                "/sendfile": self._sendfile_command
                }
        self.MAX_STR_SIZE = 2048  # In bytes
        self.CHUNK_SIZE = 1500  # Bytes of raw file data per packet
        self.BUFFER_SIZE = 10   # Receiver buffer in packets
        self.ACK_TIMEOUT = 1.0  # Seconds before retransmit
        self.UDP_RECV_SIZE = 65535  # UDP recv buffer size

        # Attributes
        self.username = username

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 48778))
        self.HOST = s.getsockname()[0]
        s.close()

        # Are there more than one people with the same name?
        self.table = {self.username: self.HOST}  # <NAME>: <IP>

        # Some useful attributes for listening
        self._stop_event = threading.Event()
        # --------------------------------

        self.current_prompt = ""
        self.system = platform.system().lower()

        # File transfer state
        self.file_transfers_out = {}  # target_ip -> sender state
        self.file_transfers_in = {}   # (sender_ip, filename) -> receiver state
        self.ft_lock = threading.Lock()
        self.ft_event = threading.Event()

    def _tcp_listen(self):
        while not self._stop_event.is_set():
            rcv_data = ""
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # "To manipulate options at the sockets API level, level is specified as SOL_SOCKET."
                # (man getsockopt)
                # Or https://pubs.opengroup.org/onlinepubs/7908799/xns/getsockopt.html
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                s.bind((self.HOST, self.PORT))
                s.listen()
                conn, _ = s.accept()
                with conn:
                    data = conn.recv(self.MAX_STR_SIZE)
                    if not data:
                        break
                s.close()
            rcv_data = data.decode("utf-8")
            # self.print_pp(rcv_data)
            try:
                rcv_payload = json.loads(rcv_data.strip())
                # print(rcv_payload)
                payload_typ = rcv_payload.get("type")

                if payload_typ == "ASK":
                    self._REPLY(rcv_payload["SENDER_IP"])
                elif payload_typ == "REPLY":
                    # Update the table
                    self.table[rcv_payload["RECEIVER_NAME"]] = rcv_payload["RECEIVER_IP"]
                    # print("Table is updated:")
                    # print(self.table)
                else:
                    sender_ip = rcv_payload["SENDER_IP"]
                    sender_name = rcv_payload["SENDER_NAME"]
                    if sender_ip == self.table[sender_name]:  # A valid user
                        self.print_pp(f'[Incoming Message] {sender_name} > {rcv_payload["PAYLOAD"]}')
                    else:
                        continue
            except (json.JSONDecodeError, KeyError):  # An invalid message has arrived
                pass

    def _udp_listen(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", self.PORT))
            s.setblocking(0)
            while not self._stop_event.is_set():
                result = select.select([s], [], [], 1.0)
                if not result[0]:
                    continue
                data, addr = result[0][0].recvfrom(self.UDP_RECV_SIZE)
                rcv_data = data.decode("utf-8")
                try:
                    rcv_payload = json.loads(rcv_data.strip())
                    payload_typ = rcv_payload.get("type")

                    if payload_typ == "ASK":
                        self._REPLY(rcv_payload["SENDER_IP"])
                    elif payload_typ == "File":
                        self._handle_file_packet(rcv_payload, addr[0])
                    elif payload_typ == "ACK":
                        self._handle_ack_packet(rcv_payload, addr[0])
                except (json.JSONDecodeError, KeyError):
                    pass

    def _tcp_send(self, target_ip, payload):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect((target_ip, self.PORT))
                s.sendall(payload.encode("utf-8"))  # Converting into byte-string
            except ConnectionRefusedError:
                pass

    def _udp_broadcast(self, payload):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.bind((self.HOST, 0))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(payload.encode("utf-8"), ("<broadcast>", self.PORT))
            except socket.error:
                pass

    def _udp_send(self, target_ip, payload):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.sendto(payload.encode("utf-8"), (target_ip, self.PORT))
            except socket.error:
                pass

    def _handle_file_packet(self, packet, sender_ip):
        filename = packet["NAME"]
        seq = packet["SEQ"]
        body = base64.b64decode(packet["BODY"])
        eof = packet["EOF"]
        key = (sender_ip, filename)

        with self.ft_lock:
            if key not in self.file_transfers_in:
                sender_name = packet.get("SENDER_NAME", sender_ip)
                self.print_pp(f'[File Transfer] Receiving "{filename}" from {sender_name}...')
                self.file_transfers_in[key] = {
                    "buffer": {},
                    "next_to_write": 1,
                    "data": bytearray(),
                    "eof_seq": None,
                }
            ft = self.file_transfers_in[key]

            if eof:
                ft["eof_seq"] = seq

            # Buffer chunk if not already written/buffered and buffer not full
            if seq >= ft["next_to_write"] and seq not in ft["buffer"]:
                if len(ft["buffer"]) < self.BUFFER_SIZE:
                    ft["buffer"][seq] = body

            # Flush sequential chunks to assembled data
            while ft["next_to_write"] in ft["buffer"]:
                ft["data"].extend(ft["buffer"].pop(ft["next_to_write"]))
                ft["next_to_write"] += 1

            rwnd = self.BUFFER_SIZE - len(ft["buffer"])

            # Check if transfer is complete
            if ft["eof_seq"] is not None and ft["next_to_write"] > ft["eof_seq"]:
                save_path = os.path.join(os.getcwd(), os.path.basename(filename))
                with open(save_path, "wb") as f:
                    f.write(ft["data"])
                sender_name = packet.get("SENDER_NAME", sender_ip)
                self.print_pp(f'[File Transfer] Completed "{os.path.basename(filename)}" from {sender_name} -> {save_path}')
                del self.file_transfers_in[key]

        # Send ACK
        ack = json.dumps({"type": "ACK", "SEQ": seq, "RWND": rwnd})
        self._udp_send(sender_ip, ack)

    def _handle_ack_packet(self, packet, sender_ip):
        seq = packet["SEQ"]
        rwnd = packet["RWND"]

        with self.ft_lock:
            if sender_ip in self.file_transfers_out:
                ft = self.file_transfers_out[sender_ip]
                ft["acked"].add(seq)
                ft["rwnd"] = rwnd
                if seq in ft["in_flight"]:
                    del ft["in_flight"][seq]
        self.ft_event.set()

    def _ASK(self):
        """
        Users shall ask frequently to the subnet if there
        are active users.
        """
        payload = json.dumps({"type": "ASK", "SENDER_IP": self.HOST})
        self._udp_broadcast(payload)

    def _scan(self):
        """
        Scans the network every self.SCAN_INTERVAL seconds.
        """
        while True:
            self._ASK()
            time.sleep(self.SCAN_INTERVAL)

    def _REPLY(self, target_ip):
        payload = json.dumps({"type": "REPLY", "RECEIVER_NAME": self.username, "RECEIVER_IP": self.HOST})
        self._tcp_send(target_ip, payload)

    def _send_chunk(self, target_ip, ft, seq):
        chunk = ft["chunks"][seq - 1]
        is_eof = (seq == ft["total"])
        payload = json.dumps({
            "type": "File",
            "NAME": ft["filename"],
            "SEQ": seq,
            "BODY": base64.b64encode(chunk).decode("ascii"),
            "EOF": is_eof,
        })
        ft["in_flight"][seq] = time.time()
        self._udp_send(target_ip, payload)

    def _send_file(self, target_ip, filepath):
        filename = os.path.basename(filepath)

        with open(filepath, "rb") as f:
            file_data = f.read()

        chunks = []
        for i in range(0, len(file_data), self.CHUNK_SIZE):
            chunks.append(file_data[i:i + self.CHUNK_SIZE])

        total_chunks = len(chunks)

        ft = {
            "filename": filename,
            "chunks": chunks,
            "total": total_chunks,
            "next_to_send": 2,  # SEQ=1 sent immediately below
            "in_flight": {},    # SEQ -> timestamp
            "acked": set(),
            "rwnd": 1,          # Unknown initially, send 1 first
        }

        with self.ft_lock:
            self.file_transfers_out[target_ip] = ft

        self.print_pp(f'[File Transfer] Sending "{filename}" ({len(file_data)} bytes, {total_chunks} chunks)')

        # Send first packet (don't know RWND yet, so send just 1)
        with self.ft_lock:
            self._send_chunk(target_ip, ft, 1)

        while True:
            self.ft_event.wait(timeout=0.1)
            self.ft_event.clear()

            with self.ft_lock:
                # Check if all chunks are ACKed
                if len(ft["acked"]) == total_chunks:
                    break

                # Retransmit packets not ACKed within ACK_TIMEOUT
                now = time.time()
                for seq in list(ft["in_flight"].keys()):
                    if now - ft["in_flight"][seq] > self.ACK_TIMEOUT:
                        self._send_chunk(target_ip, ft, seq)

                # Send new packets based on flow control
                in_flight_count = len(ft["in_flight"])
                can_send = ft["rwnd"] - in_flight_count
                while can_send > 0 and ft["next_to_send"] <= total_chunks:
                    seq = ft["next_to_send"]
                    ft["next_to_send"] += 1
                    if seq not in ft["acked"]:
                        self._send_chunk(target_ip, ft, seq)
                        can_send -= 1

        with self.ft_lock:
            del self.file_transfers_out[target_ip]

        self.print_pp(f'[File Transfer] "{filename}" sent successfully!')

    def _sendfile_command(self):
        target = input("Send file to (username) > ").strip()
        if target not in self.table or target == self.username:
            print("[INFO] Invalid target user!")
            return
        filepath = input("File path > ").strip()
        if not os.path.isfile(filepath):
            print("[INFO] File not found!")
            return
        target_ip = self.table[target]
        t = threading.Thread(target=self._send_file, args=(target_ip, filepath), daemon=True)
        t.start()

    def _str_mem_size(self, s):
        """
        We cannot assume that each character in a string
        is 1 byte in size. Since we are designing a chat
        app we need to assume that there might be charac-
        ters such as emojis or non-Latin chars that are
        not 1-byte.
        """
        return len(s.encode("utf-8"))

    def _MESSAGE(self):
        while not self._stop_event.is_set():
            try:
                self.current_prompt = "Who to chat with (or enter a command, e.g., /users) > "
                target = input(self.current_prompt).strip()

                if target in self.KEYWORDS:
                    self.KEYWORDS[target]()
                    continue

                if self.table.get(target):
                    target_ip = self.table.get(target)
                    self.current_prompt = f"Message to {target} > "
                    msg = input(self.current_prompt).strip()

                    if msg in self.KEYWORDS:
                        self.KEYWORDS[msg]()
                        continue

                    if self._str_mem_size(msg) > self.MAX_STR_SIZE:
                        print("[WARNING] Message is too large! This message is not sent. Please enter a shorter message.")
                        continue

                    payload = json.dumps({"type": "MESSAGE", "SENDER_IP": self.HOST, "SENDER_NAME": self.username, "PAYLOAD": msg})
                    self._tcp_send(target_ip, payload)
                else:
                    print("[INFO] There is no active user with this name!")
            except KeyboardInterrupt:
                self._exit_app()

    def start(self):
        tcp_listen_thread = threading.Thread(target=self._tcp_listen, daemon=True)
        udp_listen_thread = threading.Thread(target=self._udp_listen, daemon=True)
        scan_thread   = threading.Thread(target=self._scan, daemon=True)
        msg_thread    = threading.Thread(target=self._MESSAGE, daemon=True)

        # First listen, then speak
        tcp_listen_thread.start()
        udp_listen_thread.start()
        scan_thread.start()
        msg_thread.start()

    def _exit_app(self):
        print("\nExiting...")
        self._stop_event.set()
        os.system("stty sane")
        os._exit(0)

    def _print_users(self):
        print("\n--- Active Users ---")
        for name in self.table.keys():
            print(f"{name}")
        print("--------------------\n")

    def _print_help(self):
        print("\n--- Commands ---")
        for kw in self.KEYWORDS:
            print(kw)
        print("----------------\n")

    def print_pp(self, msg):
        """
        To print the incoming message without any visual bugs.
        """
        sys.stdout.write(f"\r\033[2K{msg}\n")  # \r: move cursor to start, \033[2K: clear the line
        sys.stdout.write(self.current_prompt + readline.get_line_buffer())
        sys.stdout.flush()

if __name__ == "__main__":
    username = input("Please enter your username: ")
    print("\nWrite /help for more information.\n")
    chat = ChatApp(username)
    try:
        chat.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        chat._exit_app()
