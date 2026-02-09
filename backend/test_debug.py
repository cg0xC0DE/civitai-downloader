#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import uuid
import time
import urllib.request
import websocket

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

os.chdir('C:/workplace/civitai-downloader/backend')
SERVER_ADDRESS = "127.0.0.1:8188"

def get_workflow():
    with open('workflow/nolora.json', 'r') as f:
        return json.load(f)

def main():
    print("="*50)
    print("ComfyUI 测试")
    print("="*50)
    
    # 检查就绪
    try:
        req = urllib.request.Request(f'http://{SERVER_ADDRESS}/api/queue')
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f"ComfyUI 就绪: {r.read().decode('utf-8')}")
    except Exception as e:
        print(f"ComfyUI 未就绪: {e}")
        return
    
    # 加载并修改工作流
    workflow = get_workflow()
    workflow['1']['inputs']['ckpt_name'] = 'hassakuXLIllustrious_v13StyleA.safetensors'
    workflow['6']['inputs']['text'] = 'masterpiece, best quality, anime girl'
    workflow['7']['inputs']['text'] = 'low quality, worst quality'
    workflow['8']['inputs']['width'] = 512
    workflow['8']['inputs']['height'] = 512
    
    print(f"工作流节点: {list(workflow.keys())}")
    
    # 连接 WebSocket
    client_id = str(uuid.uuid4())
    print(f"连接 WebSocket...")
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}")
    
    # 提交任务
    print("提交任务...")
    p = {"prompt": workflow, "client_id": client_id}
    req = urllib.request.Request(f"http://{SERVER_ADDRESS}/prompt", data=json.dumps(p).encode('utf-8'))
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())
        print(f"提交结果: {result}")
        prompt_id = result.get('prompt_id')
    
    # 接收消息
    print("等待完成...")
    images = {}
    received_images = []
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            print(f"消息: {message.get('type')}")
            
            if message['type'] == 'executing':
                data = message['data']
                if data.get('prompt_id') == prompt_id:
                    node = data.get('node')
                    if node is None:
                        print("执行完成!")
                        break
                    else:
                        print(f"  节点 {node} 完成")
        else:
            # 图片数据
            received_images.append(len(out))
            print(f"收到图片数据: {len(out)} bytes")
    
    ws.close()
    
    print(f"\n收到 {len(received_images)} 个图片数据块")
    if received_images:
        print(f"总大小: {sum(received_images)} bytes")
        print("成功!")

if __name__ == '__main__':
    main()
