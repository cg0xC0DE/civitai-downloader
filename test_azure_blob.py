#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Azure Blob 存储和获取接口
验证流程：触发绘图 -> 检查本地保存 -> 检查 Azure 上传 -> 查询接口
"""

import os
import sys
import time
import json
import socket
import subprocess

HOST = "localhost"
PORT = 53133
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'backend', 'output')

def http_request(method, path, data=None):
    """使用 socket 发送 HTTP 请求"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((HOST, PORT))

    body = json.dumps(data) if data else ""
    request = f"{method} {path} HTTP/1.1\r\nHost: {HOST}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    sock.send(request.encode())

    # 读取所有响应
    response = b''
    import time
    for _ in range(20):  # 最多等待 2 秒
        try:
            chunk = sock.recv(8192)
            if not chunk:
                break
            response += chunk
            # 检查是否收到完整响应（包含 \r\n\r\n 分隔符）
            if b'\r\n\r\n' in response:
                # 读取剩余数据
                while True:
                    sock.setblocking(False)
                    try:
                        extra = sock.recv(8192)
                        if extra:
                            response += extra
                        else:
                            break
                    except BlockingIOError:
                        break
                    finally:
                        sock.setblocking(True)
                    time.sleep(0.1)
                break
        except socket.timeout:
            break

    sock.close()

    # 分离 header 和 body
    if b"\r\n\r\n" in response:
        _, body = response.split(b"\r\n\r\n", 1)
        try:
            return json.loads(body.decode('utf-8'))
        except:
            return {}
    return {}

def print_step(msg):
    print(f"\n{'='*60}")
    print(f"STEP: {msg}")
    print('='*60)

def print_result(msg, ok=True):
    symbol = "[OK]" if ok else "[FAIL]"
    print(f"{symbol} {msg}")

def trigger_workflow(prompt="a beautiful cat", batch_size=1):
    """触发 ComfyUI 绘图"""
    print_step("Trigger ComfyUI Workflow")
    data = {
        "workflow": "xl.text2img.basic",
        "prompt": prompt,
        "batch_size": batch_size
    }
    result = http_request("POST", "/api/workflow/run", data)
    print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
    return result.get('prompt_id') if result.get('status') == 'submitted' else None

def wait_for_completion(prompt_id, timeout=120):
    """等待绘图完成"""
    print_step(f"Wait for completion (prompt_id={prompt_id})")
    start = time.time()
    while time.time() - start < timeout:
        status = http_request("GET", f"/api/workflow/status?prompt_id={prompt_id}")
        print(f"Status: {json.dumps(status, indent=2, ensure_ascii=False)}")

        if status.get('status') == 'success':
            print_result("Draw completed!")
            return status
        elif status.get('status') == 'error':
            print_result(f"Draw failed: {status.get('message')}", ok=False)
            return None

        time.sleep(2)

    print_result("Timeout", ok=False)
    return None

def check_local_output():
    """检查本地 output 目录"""
    print_step("Check local output directory")
    if not os.path.exists(OUTPUT_DIR):
        print_result(f"Directory not exists: {OUTPUT_DIR}", ok=False)
        return []

    files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.png')]
    print(f"Found {len(files)} PNG files: {files[:5]}...")
    print_result(f"Local save OK ({len(files)} files)")
    return files

def check_azure_blobs(expected_count=1):
    """检查 Azure Blob 列表"""
    print_step("Call /api/azure/list")
    try:
        result = http_request("GET", "/api/azure/list")
        print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")

        if result.get('status') != 'success':
            print_result("API returned error", ok=False)
            return []

        blobs = result.get('blobs', [])
        print_result(f"Azure Blob list OK ({len(blobs)} files)")

        # 显示最新的几个
        if blobs:
            print("\nLatest files:")
            for url in blobs[:5]:
                print(f"  - {url}")

        return blobs
    except Exception as e:
        print_result(f"API call failed: {e}", ok=False)
        return []

def verify_upload(azure_blobs, local_files):
    """验证上传成功"""
    print_step("验证上传对应关系")

    if not local_files:
        print_result("无本地文件可验证", ok=False)
        return False

    if not azure_blobs:
        print_result("Azure 无文件", ok=False)
        return False

    # 检查最新的 Azure blob 是否对应刚生成的文件
    # 本地文件名格式: batch_id_prompt[:8]_index.png
    latest_azure = azure_blobs[0]
    print(f"最新 Azure URL: {latest_azure}")

    # 简单验证：URL 包含 generated/ 路径
    if "generated/" in latest_azure and ".png" in latest_azure:
        print_result("URL 格式正确 (generated/xxx.png)")
        return True

    print_result("URL 格式不符合预期", ok=False)
    return False

def main():
    print("\n" + "="*60)
    print("Azure Blob Storage Test")
    print("="*60)

    # 1. 检查服务健康
    print_step("Check service health")
    try:
        health = http_request("GET", "/api/health")
        if health.get('status') == 'ok':
            print_result("Service is healthy")
        else:
            print_result(f"Service error: {health}", ok=False)
            return
    except Exception as e:
        print_result(f"Cannot connect to service: {e}", ok=False)
        return

    # 2. 触发绘图
    prompt_id = trigger_workflow(prompt="a cute orange cat", batch_size=1)
    if not prompt_id:
        print_result("Trigger workflow failed")
        return

    # 3. 等待完成
    result = wait_for_completion(prompt_id)
    if not result:
        return

    # 4. 检查本地
    local_files = check_local_output()

    # 5. 检查 Azure
    time.sleep(1)  # 等待 Azure 同步
    azure_blobs = check_azure_blobs()

    # 6. 验证
    print_step("Test Summary")
    print(f"Local files: {len(local_files)}")
    print(f"Azure blobs: {len(azure_blobs)}")

    if local_files and azure_blobs:
        print_result("TEST PASSED! Azure storage works correctly")
    else:
        print_result("TEST FAILED")

if __name__ == '__main__':
    main()
