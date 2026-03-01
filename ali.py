import socket
import json
from concurrent.futures import ThreadPoolExecutor
import time
import threading
import readline
import os
import sys
import platform

import nmap

class ChatApp:
    def __init__(self, username):
        # Constants
        self.PORT = 12487
        self.MSG_TYPES = ["ASK", "REPLY", "MESSAGE"]
        self.SCAN_INTERVAL = 10
        self.KEYWORDS = {
                "/exit": self._exit_app,
                "/users": self._print_users,
                "/help": self._print_help
                }
        self.MAX_STR_SIZE = 2048  # In bytes

        # Attributes
        self.username = username

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 48778))
        self.HOST = s.getsockname()[0]

        # Are there more than one people with the same name?
        self.table = {self.username: self.HOST}  # <NAME>: <IP>

        # Some useful attributes for self._listen()
        self._stop_event = threading.Event()
        self.listen_proc = None
        # --------------------------------

        self.current_prompt = ""
        self.system = platform.system().lower()

    def _listen(self):
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

    def _nmap_scan(self):
        nm = nmap.PortScanner()
        nm.scan(hosts=f"{self.HOST}/24", arguments="-sn")
        return nm.all_hosts()

    def _send(self, target_ip, payload):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect((target_ip, self.PORT))
                s.sendall(payload.encode("utf-8"))  # Converting into byte-string
            except ConnectionRefusedError:
                pass

    def _ASK(self):
        """
        Users shall ask frequently to the subnet if there 
        are active users.
        """
        payload = json.dumps({"type": "ASK", "SENDER_IP": self.HOST})
        # print("Scan started!")
        # start_time = time.time()
        with ThreadPoolExecutor(253) as executor:  # https://superfastpython.com/threadpoolexecutor-number-of-threads/
            for ip in self._nmap_scan():
                executor.submit(self._send, ip, payload)
        # print(f"Scan complete in {time.time() - start_time:.2f} seconds!")

    def _scan(self):
        """
        Scans the network every self.SCAN_INTERVAL seconds.
        """
        while True:
            self._ASK()
            time.sleep(self.SCAN_INTERVAL)

    def _REPLY(self, target_ip):
        payload = json.dumps({"type": "REPLY", "RECEIVER_NAME": self.username, "RECEIVER_IP": self.HOST})
        self._send(target_ip, payload)

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
                    self._send(target_ip, payload)
                else:
                    print("[INFO] There is no active user with this name!")
            except KeyboardInterrupt:
                self._exit_app()

    def start(self):
        listen_thread = threading.Thread(target=self._listen, daemon=True)
        scan_thread   = threading.Thread(target=self._scan, daemon=True)
        msg_thread    = threading.Thread(target=self._MESSAGE, daemon=True)

        # First listen, then speak
        listen_thread.start()
        scan_thread.start()
        msg_thread.start()

    def _exit_app(self):
        print("\nExiting...")
        self._stop_event.set()
        if self.listen_proc:
            self.listen_proc.kill()
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
