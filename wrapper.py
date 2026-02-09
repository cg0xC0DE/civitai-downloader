#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Downloader Wrapper
判断请求类型：下载(download) 或 绘图(draw)

Usage:
    python wrapper.py <action> <data_json>

Actions:
    download  - 下载模型
    draw      - 触发ComfyUI绘图 (暂未实现)
    status    - 查询状态
"""

import sys
import json
import os

# 添加 backend 目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from civitaidl import CivitaiDownloader

def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            "status": "error",
            "message": "Usage: python wrapper.py <action> <data_json>"
        }))
        sys.exit(1)
    
    action = sys.argv[1]
    data_json = sys.argv[2]
    
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "status": "error",
            "message": f"Invalid JSON: {e}"
        }))
        sys.exit(1)
    
    downloader = CivitaiDownloader()
    
    if action == "download":
        result = downloader.download(
            url=data.get("url"),
            type_subtype=data.get("type"),
            auto_proxy=data.get("auto_proxy", True)
        )
        print(json.dumps(result, ensure_ascii=False))
        
    elif action == "draw":
        # TODO: 实现ComfyUI绘图触发
        print(json.dumps({
            "status": "pending",
            "message": "ComfyUI drawing feature not implemented yet"
        }))
        
    elif action == "status":
        print(json.dumps({
            "status": "ok",
            "message": "Civitai Downloader is running"
        }))
        
    else:
        print(json.dumps({
            "status": "error",
            "message": f"Unknown action: {action}"
        }))


if __name__ == "__main__":
    main()
