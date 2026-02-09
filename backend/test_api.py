import sys
sys.path.insert(0, '.')

from server import wait_for_comfyui

print('Testing API directly...')

# Test health endpoint
from http.server import HTTPServer
from server import APIHandler

import threading

def start_server():
    server = HTTPServer(('127.0.0.1', 53134), APIHandler)
    server.handle_request()  # Handle one request
    server.server_close()

# Start server in thread
t = threading.Thread(target=start_server)
t.start()

import urllib.request
import time
time.sleep(0.5)

# Test health
try:
    req = urllib.request.Request('http://127.0.0.1:53134/api/health')
    with urllib.request.urlopen(req, timeout=5) as r:
        print('Health:', r.read().decode())
except Exception as e:
    print('Error:', e)
