import socket
import json
import subprocess
import ipaddress
from concurrent.futures import ThreadPoolExecutor
import time
import threading
import readline
import os
import sys
import platform

NC = "/usr/bin/nc"

class ChatApp:
    def __init__(self, username):
        # Constants
        self.PORT = "12487"
        self.MSG_TYPES = ["ASK", "REPLY", "MESSAGE"]
        self.SCAN_INTERVAL = 20
        self.KEYWORDS = {
                "/exit": self._exit_app,
                "/users": self._print_users,
                "/help": self._print_help
                }

        # Attributes
        self.username = username

        # Below line did not work as intended in Ubuntu. I suspect that
        # Debian- and Red Hat-based OSs return differently.
        # self.IPAddr = socket.gethostbyname(socket.gethostname())

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 48778))
        self.IPAddr = s.getsockname()[0]

        self.table = {self.username: self.IPAddr}  # <NAME>: <IP>

        # Some useful attributes for self._listen()
        self._stop_event = threading.Event()
        self.listen_proc = None
        # --------------------------------

        self.current_prompt = ""
        self.system = platform.system().lower()

        network = ipaddress.IPv4Interface(f"{self.IPAddr}/24").network
        network_hosts = network.hosts()  # An iterable generator object containing subnet IPs
        self.target_ips = []
        sender_last_byte = self.IPAddr.split(sep=".")[-1]

        for ip in network_hosts:
            target_ip = str(ip)
            if target_ip.split(sep=".")[-1] not in ["0", "1", "255", sender_last_byte]:
                self.target_ips.append(target_ip)

        # print(f"Welcome, {self.username}!")
        # print(f"Your IP Address: {self.IPAddr}...")
        # print("Your initialized table:")
        # print(json.loads(self.table))

    def _listen(self):
        while not self._stop_event.is_set():
            if self.system == "darwin":
                command = [NC, "-l", "-k", self.PORT]
            else:
                command = [NC, "-l", "-k", self.PORT]
            # print("Listen")
            self.listen_proc = subprocess.Popen(command,stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
            for line in self.listen_proc.stdout:
                try:
                    rcv_payload = json.loads(line.strip())
                    # print(rcv_payload)
                    payload_typ = rcv_payload["type"]

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
                except json.JSONDecodeError:  # An invalid message has arrived
                    pass
            self.listen_proc.wait()

    def _nc_send(self, target_ip, payload):
        if self.system == "darwin":
            # Apple nc: use timeout-based close to avoid hanging.
            command = [NC, "-w", "1", target_ip, self.PORT]
        else:
            # OpenBSD nc (Linux): close socket after stdin EOF.
            command = [NC, "-N", target_ip, self.PORT]

        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True) as proc:
            try:
                proc.communicate(payload, timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _ASK(self):
        """
        Users shall ask frequently to the subnet if there 
        are active users.
        """
        payload = json.dumps({"type": "ASK", "SENDER_IP": self.IPAddr}) + "\n"  # The new line character is needed to iterate through lines in _listen()

        # print("Scan started!")
        # start_time = time.time()

        with ThreadPoolExecutor(253) as executor:  # https://superfastpython.com/threadpoolexecutor-number-of-threads/
            for ip in self.target_ips:
                executor.submit(self._nc_send, ip, payload)

        # print(f"Scan complete in {time.time() - start_time:.2f} seconds!")

    def _scan(self):
        """
        Scans the network every self.SCAN_INTERVAL seconds.
        """
        while True:
            self._ASK()
            time.sleep(self.SCAN_INTERVAL)

    def _REPLY(self, target_ip):
        payload = json.dumps({"type": "REPLY", "RECEIVER_NAME": self.username, "RECEIVER_IP": self.IPAddr}) + "\n"
        self._nc_send(target_ip, payload)

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

                    payload = json.dumps({"type": "MESSAGE", "SENDER_IP": self.IPAddr, "SENDER_NAME": self.username, "PAYLOAD": msg}) + "\n"
                    self._nc_send(target_ip, payload)
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
