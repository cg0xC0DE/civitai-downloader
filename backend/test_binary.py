#!/usr/bin/env python3
import os
import sys
import json
import uuid
import urllib.request
import websocket

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

os.chdir('C:/workplace/civitai-downloader/backend')
SERVER_ADDRESS = "127.0.0.1:8188"

def main():
    print("ComfyUI 测试\n")
    
    # 检查就绪
    try:
        with urllib.request.urlopen(f'http://{SERVER_ADDRESS}/api/queue') as r:
            pass
    except Exception as e:
        print(f"ComfyUI 未就绪: {e}")
        return
    
    # 加载工作流
    with open('workflow/nolora.json', 'r') as f:
        workflow = json.load(f)
    
    workflow['6']['inputs']['text'] = 'masterpiece, anime girl'
    workflow['7']['inputs']['text'] = 'blurry, low quality'
    
    # 连接 WebSocket
    client_id = str(uuid.uuid4())
    print(f"Client ID: {client_id}")
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}")
    
    # 提交任务
    p = {"prompt": workflow, "client_id": client_id}
    with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/prompt", data=json.dumps(p).encode('utf-8')) as r:
        result = json.loads(r.read())
        print(f"提交: {result}")
        prompt_id = result['prompt_id']
    
    # 接收所有消息
    print("\n等待消息...")
    binary_count = 0
    binary_total = 0
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            msg = json.loads(out)
            print(f"  [{msg.get('type')}]")
            
            if msg['type'] == 'executing':
                data = msg['data']
                if data.get('prompt_id') == prompt_id:
                    if data.get('node') is None:
                        print("\n完成!")
                        break
        else:
            binary_count += 1
            binary_total += len(out)
            print(f"  [图片数据 #{binary_count}] {len(out)} bytes")
    
    ws.close()
    
    print(f"\n总计: {binary_count} 张图片, {binary_total} bytes")

if __name__ == '__main__':
    main()
