#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版 Azure Blob 测试 - 直接上传测试文件
绕过 ComfyUI WebSocket 问题，直接测试 Azure 上传功能
"""

import os
import sys
import time
import json
import socket
import base64

COMFYUI_HOST = "127.0.0.1"
COMFYUI_PORT = 8188
OUTPUT_DIR = "C:/workplace/civitai-downloader/backend/output"

def http_request(host, port, method, path, data=None):
    """发送 HTTP 请求"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((host, port))

    body = json.dumps(data) if data else ""
    request = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    sock.send(request.encode())

    # 读取响应
    response = b''
    for _ in range(50):
        try:
            chunk = sock.recv(8192)
            if not chunk:
                break
            response += chunk
            if b'\r\n\r\n' in response:
                time.sleep(0.2)
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
                    sock.setblocking(True)
                    time.sleep(0.1)
                break
        except socket.timeout:
            break

    sock.close()

    if b"\r\n\r\n" in response:
        _, body = response.split(b"\r\n\r\n", 1)
        try:
            return json.loads(body.decode('utf-8'))
        except:
            return {}
    return {}

def check_comfyui():
    """检查 ComfyUI 服务"""
    print("="*60)
    print("STEP: Check ComfyUI Service")
    print("="*60)

    result = http_request(COMFYUI_HOST, COMFYUI_PORT, "GET", "/api/queue")
    print(f"Response: {json.dumps(result, indent=2)}")
    return result.get('queue_running') is not None

def create_test_image():
    """创建测试图片（一个简单的红色方块）"""
    print("\n" + "="*60)
    print("STEP: Create Test Image")
    print("="*60)

    # 创建一个简单的 64x64 红色图片（PNG 格式的最小有效数据）
    # 这是最简单的方式创建一个有效的 PNG
    import zlib

    # PNG 文件头和 IHDR
    png_header = bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG 签名
        0x00, 0x00, 0x00, 0x0D,  # IHDR 长度
        0x49, 0x48, 0x44, 0x52,  # IHDR
        0x00, 0x00, 0x00, 0x40,  # Width: 64
        0x00, 0x00, 0x00, 0x40,  # Height: 64
        0x02,  # Bit depth: 2 (索引色)
        0x00,  # Color type: 0 (灰度)
        0x00, 0x00, 0x00,  # Compression, Filter, Interlace
    ])
    ihdr_crc = zlib.crc32(b"IHDR" + bytes([0x00, 0x00, 0x00, 0x40, 0x00, 0x00, 0x00, 0x40, 0x02, 0x00, 0x00, 0x00]))
    png_header += ihdr_crc.to_bytes(4, 'big')

    # 创建图像数据（64x64 红色像素）
    raw_data = b''
    for _ in range(64):
        raw_data += b'\x00'  # Filter byte
        raw_data += bytes([255, 0, 0] * 64)  # Red pixels (RGB)

    compressed = zlib.compress(raw_data)
    idat_length = len(compressed)
    idat_data = bytes([0x49, 0x44, 0x41, 0x54]) + compressed
    idat_crc = zlib.crc32(idat_data)
    idat = idat_length.to_bytes(4, 'big') + idat_data + idat_crc.to_bytes(4, 'big')

    # IEND
    iend = bytes([0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82])

    png_data = png_header + idat + iend

    # 保存到文件
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    test_file = os.path.join(OUTPUT_DIR, "test_azure_upload.png")
    with open(test_file, 'wb') as f:
        f.write(png_data)

    print(f"Created test image: {test_file}")
    print(f"Size: {len(png_data)} bytes")
    return test_file

def upload_to_azure(file_path):
    """上传到 Azure Blob"""
    print("\n" + "="*60)
    print("STEP: Upload to Azure Blob")
    print("="*60)

    # 直接使用 azure_blob 模块
    sys.path.insert(0, 'C:/workplace/civitai-downloader/backend')
    from azure_blob import BlobStorage

    with open(file_path, 'rb') as f:
        data = f.read()

    blob = BlobStorage(container='civitaidl')
    url = blob.put_bytes(data, f"generated/test_{int(time.time())}.png")

    print(f"Uploaded: {url}")
    return url

def check_azure_list():
    """检查 Azure Blob 列表"""
    print("\n" + "="*60)
    print("STEP: Call /api/azure/list")
    print("="*60)

    result = http_request("localhost", 53133, "GET", "/api/azure/list")
    print(f"Response: {json.dumps(result, indent=2)}")

    if result.get('status') == 'success':
        blobs = result.get('blobs', [])
        print(f"Found {len(blobs)} blobs")
        for url in blobs[:5]:
            print(f"  - {url}")
        return blobs
    return []

def main():
    print("="*60)
    print("Azure Blob Storage Test (Simplified)")
    print("="*60)

    # 1. 检查服务
    print("\n[1/4] Check services...")
    if not check_comfyui():
        print("ComfyUI not available, skip ComfyUI test")
    else:
        print("ComfyUI OK")

    # 2. 创建测试图片
    print("\n[2/4] Create test image...")
    test_file = create_test_image()

    # 3. 上传到 Azure
    print("\n[3/4] Upload to Azure...")
    try:
        url = upload_to_azure(test_file)
        print(f"Upload OK: {url}")
    except Exception as e:
        print(f"Upload failed: {e}")
        return

    # 4. 检查列表
    print("\n[4/4] Check Azure list...")
    blobs = check_azure_list()

    # 结果汇总
    print("\n" + "="*60)
    print("TEST RESULT")
    print("="*60)
    print(f"Test image created: {test_file}")
    print(f"Uploaded to Azure: {url}")
    print(f"Azure list count: {len(blobs)}")

    if blobs and any('generated/' in b for b in blobs):
        print("\n[OK] TEST PASSED!")
    else:
        print("\n[FAIL] TEST FAILED")

if __name__ == '__main__':
    main()
