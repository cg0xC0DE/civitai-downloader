#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ComfyUI 完整测试 - 提交任务并获取图片"""
import os
import sys
import json
import uuid
import time
import urllib.request
import websocket

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

os.chdir('C:/workplace/civitai-downloader/backend')
SERVER_ADDRESS = "127.0.0.1:8188"

def main():
    print("="*50)
    print("ComfyUI 绘图测试")
    print("="*50)
    
    # 1. 检查就绪
    print("检查 ComfyUI...")
    try:
        with urllib.request.urlopen(f'http://{SERVER_ADDRESS}/api/queue') as r:
            print(f"  就绪: {r.read().decode('utf-8')}")
    except Exception as e:
        print(f"  未就绪: {e}")
        return
    
    # 2. 加载工作流
    print("\n加载工作流...")
    with open('workflow/nolora.json', 'r') as f:
        workflow = json.load(f)
    
    # 3. 设置参数
    workflow['6']['inputs']['text'] = 'masterpiece, best quality, anime girl, blue eyes'
    workflow['7']['inputs']['text'] = 'low quality, worst quality, blurry'
    
    print(f"  Checkpoint: {workflow['1']['inputs']['ckpt_name']}")
    print(f"  提示词: {workflow['6']['inputs']['text']}")
    
    # 4. 连接 WebSocket
    client_id = str(uuid.uuid4())
    print(f"\n连接 WebSocket...")
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}")
    
    # 5. 提交任务
    print("提交任务...")
    p = {"prompt": workflow, "client_id": client_id}
    result = None
    try:
        with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/prompt", data=json.dumps(p).encode('utf-8')) as r:
            result = json.loads(r.read())
            print(f"  提交成功: {result}")
    except Exception as e:
        print(f"  提交失败: {e}")
        ws.close()
        return
    
    if not result or 'prompt_id' not in result:
        print("  无 prompt_id!")
        ws.close()
        return
    
    prompt_id = result['prompt_id']
    
    # 6. 等待完成
    print("\n等待生成完成...")
    images = []
    node_stats = {}
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            msg = json.loads(out)
            msg_type = msg.get('type', 'unknown')
            
            if msg_type == 'executing':
                data = msg['data']
                if data.get('prompt_id') == prompt_id:
                    node = data.get('node')
                    if node is None:
                        print("  完成!")
                        break
                    else:
                        node_stats[node] = node_stats.get(node, 0) + 1
                        print(f"  节点 {node} 完成")
        else:
            # 图片数据
            images.append(len(out))
            print(f"  收到图片: {len(out)} bytes")
    
    ws.close()
    
    # 7. 结果
    print("\n" + "="*50)
    print(f"生成完成!")
    print(f"  图片数: {len(images)}")
    print(f"  总大小: {sum(images)} bytes")
    if images:
        print(f"  平均大小: {sum(images)//len(images)} bytes")
    print("="*50)

if __name__ == '__main__':
    main()
