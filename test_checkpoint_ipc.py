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
    
    # 创建 session
    result = send_cmd(s, "session.create", {"mode": "chat"})
    session_id = result.get('result', {}).get('session_id')
    print(f'Session ID: {session_id}')
    
    # 发送消息
    result = send_cmd(s, "session.send_message", {
        "session_id": session_id, 
        "content": "Hello"
    })
    print(f'Run ID: {result.get("result", {}).get("run_id")}')
    time.sleep(15)
    
    # 使用IPC命令列出checkpoints（用户手动操作）
    result = send_cmd(s, "session.checkpoint.list", {"session_id": session_id})
    print(f'\n=== IPC List Checkpoints ===')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # 使用IPC命令恢复checkpoint（用户手动操作）
    checkpoints = result.get('result', {}).get('checkpoints', [])
    if checkpoints:
        # 恢复到第一个checkpoint（step=-1，初始状态）
        cp_id = checkpoints[-1]['checkpoint_id']
        result = send_cmd(s, "session.checkpoint.restore", {
            "session_id": session_id, 
            "checkpoint_id": cp_id
        })
        print(f'\n=== IPC Restore Checkpoint ===')
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()
finally:
    s.close()