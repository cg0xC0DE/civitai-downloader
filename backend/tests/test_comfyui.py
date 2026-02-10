#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ComfyUI 测试 - 包含等待模型加载逻辑"""
import os
import sys
import json
import uuid
import time
import urllib.request
import urllib.parse
import websocket

# 清除代理
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

os.chdir('C:/workplace/civitai-downloader/backend')
SERVER_ADDRESS = "127.0.0.1:8188"

def wait_for_comfyui(timeout=120):
    """等待 ComfyUI 就绪（包括模型加载时间）"""
    print(f"等待 ComfyUI 就绪 (超时 {timeout}秒)...")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f'http://{SERVER_ADDRESS}/api/queue')
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                print(f"  ComfyUI 就绪! 队列: {data}")
                return True
        except Exception as e:
            elapsed = int(time.time() - start)
            sys.stdout.write(f"\r  等待中... {elapsed}s (503 可能是模型加载中)")
            sys.stdout.flush()
            time.sleep(2)
    
    print(f"\n超时!")
    return False

def get_workflow_api(workflow_name):
    with open('workflow/'+workflow_name+'.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def get_config(config_name):
    with open('workflow/'+config_name+'.config', 'r', encoding='utf-8') as f:
        return json.load(f)

def queue_prompt(prompt, client_id):
    p = {"prompt": prompt, "client_id": client_id}
    req = urllib.request.Request(f"http://{SERVER_ADDRESS}/prompt", data=json.dumps(p).encode('utf-8'))
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def get_images(ws, prompt, client_id):
    prompt_id = queue_prompt(prompt, client_id)['prompt_id']
    output_images = {}
    current_node = ""
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data['prompt_id'] == prompt_id:
                    if data['node'] is None:
                        break
                    else:
                        current_node = data['node']
                        print(f"  执行节点: {current_node}")
        else:
            if current_node == '13':
                images_output = output_images.get(current_node, [])
                images_output.append(out[8:])
                output_images[current_node] = images_output
    
    return output_images

def run():
    print("="*50)
    print("ComfyUI 绘图测试")
    print("="*50)
    
    # 1. 等待 ComfyUI 就绪
    if not wait_for_comfyui():
        print("ComfyUI 未就绪!")
        return
    
    # 2. 加载工作流
    print("\n加载工作流...")
    workflow = get_workflow_api('nolora')
    config = get_config('default')
    
    # 3. 设置参数
    workflow['1']['inputs']['ckpt_name'] = config['checkpoint'][0]['name']
    workflow['6']['inputs']['text'] = config['prompts']['positive']
    workflow['7']['inputs']['text'] = config['prompts']['negative']
    workflow['8']['inputs']['width'] = config['latent']['width']
    workflow['8']['inputs']['height'] = config['latent']['height']
    workflow['9']['inputs']['steps'] = config['ksampler']['steps']
    workflow['9']['inputs']['cfg'] = config['ksampler']['cfg']
    workflow['9']['inputs']['sampler_name'] = config['ksampler']['sampler_name']
    
    print(f"Checkpoint: {config['checkpoint'][0]['name']}")
    print(f"尺寸: {config['latent']['width']}x{config['latent']['height']}")
    
    # 4. 连接 WebSocket
    client_id = str(uuid.uuid4())
    print(f"\n连接 WebSocket...")
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={client_id}")
    
    # 5. 获取图片
    print("提交任务并等待完成...")
    images = get_images(ws, workflow, client_id)
    ws.close()
    
    print(f"\n生成完成! 图片节点: {list(images.keys())}")
    if images:
        print(f"生成 {sum(len(imgs) for imgs in images.values())} 张图片")

if __name__ == '__main__':
    run()
