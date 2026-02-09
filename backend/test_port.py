import socket

try:
    s = socket.socket()
    s.bind(('', 53133))
    s.close()
    print("Port OK")
except Exception as e:
    print(f"Port error: {e}")
