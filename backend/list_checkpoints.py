#!/usr/bin/env python3
import os
import json
import urllib.request

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

SERVER_ADDRESS = "127.0.0.1:8188"

# 获取 checkpoint 列表
try:
    with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/api/models/checkpoints") as r:
        data = json.loads(r.read())
        print("可用 Checkpoints:")
        for ckpt in data[:20]:
            print(f"  - {ckpt}")
except Exception as e:
    print(f"错误: {e}")
