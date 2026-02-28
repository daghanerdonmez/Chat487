import socket

HOST = '192.168.0.30'
PORT = 12345

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((HOST, PORT))
    s.sendall(b'DISCOVER ME')
    data = s.recv(1024)

print('Received', repr(data))
