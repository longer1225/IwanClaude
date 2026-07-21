import json
import socket
import time

def send_cmd(s, method, params):
    cmd = {"method": method, "params": params, "id": f"test_{time.time()}"}
    s.sendall((json.dumps(cmd) + "\n").encode())
    time.sleep(3)
    data = s.recv(4096)
    if data:
        return json.loads(data.decode())
    return None

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(('127.0.0.1', 7437))
    print('Connected to daemon')
    
    result = send_cmd(s, "session.create", {"mode": "chat"})
    session_id = result.get('result', {}).get('session_id')
    print(f'Session ID: {session_id}')
    
    result = send_cmd(s, "session.send_message", {
        "session_id": session_id, 
        "content": "Hello"
    })
    run_id = result.get('result', {}).get('run_id')
    print(f'Run ID: {run_id}')
    
    time.sleep(15)
    
    result = send_cmd(s, "session.get_history", {"session_id": session_id})
    messages = result.get('result', {}).get('messages', [])
    print(f'\nMessages: {len(messages)}')
    for msg in messages[-2:]:
        print(f"\n{msg.get('role')}: {msg.get('content')}")
    
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()
finally:
    s.close()