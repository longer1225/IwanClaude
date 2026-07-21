import socket
import json

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(('127.0.0.1', 7437))
    print('Connected to daemon')
    
    cmd = {"method": "ping", "params": {}, "id": 1}
    s.sendall((json.dumps(cmd) + "\n").encode())
    
    import time
    time.sleep(1)
    
    data = s.recv(4096)
    print('Response:', data.decode())
except Exception as e:
    print('Error:', e)
finally:
    s.close()