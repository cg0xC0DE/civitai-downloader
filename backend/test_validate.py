#!/usr/bin/env python3
import os
import json
import urllib.request
import websocket

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

os.chdir('C:/workplace/civitai-downloader/backend')
SERVER_ADDRESS = "127.0.0.1:8188"

# 加载工作流
with open('workflow/nolora.json', 'r') as f:
    workflow = json.load(f)

# 修改
workflow['1']['inputs']['ckpt_name'] = 'hassakuXLIllustrious_v13StyleA.safetensors'
workflow['6']['inputs']['text'] = 'masterpiece, best quality'
workflow['7']['inputs']['text'] = 'low quality, worst quality'

print(f"节点: {list(workflow.keys())}")

# 检查工作流
client_id = 'test123'
p = {"prompt": workflow, "client_id": client_id}

try:
    req = urllib.request.Request(f"http://{SERVER_ADDRESS}/prompt", data=json.dumps(p).encode('utf-8'))
    with urllib.request.urlopen(req) as r:
        print(f"成功: {r.read().decode('utf-8')}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.reason}")
    print(f"错误详情: {e.read().decode('utf-8')}")
