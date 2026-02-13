#!/usr/bin/env python3
"""
Civitai Downloader + ComfyUI 心跳健康检查
"""

import socket
import urllib.request
import json
import time

def check_port(port, host='localhost', timeout=3):
    """检查端口是否开放"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except:
        return False
    finally:
        s.close()

def check_http(url, timeout=5):
    """检查 HTTP 服务"""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except:
        return False

def check_comfyui_queue(timeout=10):
    """检查 ComfyUI 队列状态"""
    try:
        req = urllib.request.Request('http://localhost:8188/api/queue', timeout=5)
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return True, 'running'
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return False, 'loading'
        return False, 'error'
    except Exception:
        return False, 'not responding'

def main():
    results = {}
    issues = []
    
    print('=' * 55)
    print('Civitai Downloader + ComfyUI Heartbeat Check')
    print('=' * 55)
    
    # 1. 检查 nginx (80) - 用于 Civitai 访问
    results['nginx'] = check_port(80)
    print(f'\n[1] nginx (80): {"OK" if results["nginx"] else "FAIL"}')
    if not results['nginx']:
        issues.append('nginx')
    
    # 2. 检查 Backend API (53133)
    results['backend'] = check_port(53133)
    print(f'[2] Backend API (53133): {"OK" if results["backend"] else "FAIL"}')
    if not results['backend']:
        issues.append('backend')
    
    # 3. 检查 Frontend (53134)
    results['frontend'] = check_port(53134)
    print(f'[3] Frontend (53134): {"OK" if results["frontend"] else "FAIL"}')
    if not results['frontend']:
        issues.append('frontend')
    
    # 4. 检查 ComfyUI
    comfy_ready, comfy_status = check_comfyui_queue()
    results['comfyui'] = comfy_ready
    status_map = {'running': 'OK', 'loading': 'LOADING', 'not responding': 'FAIL', 'error': 'FAIL'}
    print(f'[4] ComfyUI (8188): {status_map.get(comfy_status, "FAIL")} ({comfy_status})')
    if not comfy_ready and comfy_status not in ['loading']:
        issues.append('comfyui')
    
    # 5. 检查 ngrok tunnel (公网访问)
    ngrok_ok = check_http('https://dentiled-gennie-stichometrical.ngrok-free.dev/civitaidl/')
    results['ngrok'] = ngrok_ok
    print(f'[5] ngrok tunnel: {"OK" if ngrok_ok else "FAIL"}')
    if not ngrok_ok:
        issues.append('ngrok')
    
    # 6. 检查模型列表文件
    print(f'\n[6] Model Lists:')
    try:
        with open(r'C:\workplace\civitai-downloader\backend\models\ckpt_list.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f'    - Checkpoints: {data.get("total_models", 0)} models')
    except Exception:
        print(f'    - Checkpoints: NOT FOUND')
    
    try:
        with open(r'C:\workplace\civitai-downloader\backend\models\lora_list.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f'    - LoRAs: {data.get("total_models", 0)} models')
    except Exception:
        print(f'    - LoRAs: NOT FOUND')
    
    print('\n' + '=' * 55)
    print(f'Result: {"[OK] All OK" if not issues else "[!] Issues: " + ", ".join(issues)}')
    print('=' * 55)
    
    return len(issues) == 0

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
