#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 ComfyUI - 纯 HTTP API 方式"""
import json
import uuid
import urllib.request
import urllib.parse

SERVER_ADDRESS = "127.0.0.1:8188"

def queue_prompt(prompt, client_id):
    """提交提示词"""
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode('utf-8')
    req = urllib.request.Request(
        f"http://{SERVER_ADDRESS}/prompt",
        data=data
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())

def get_history(prompt_id):
    """获取历史"""
    with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as response:
        return json.loads(response.read())

def get_image(filename, subfolder, folder_type):
    """获取图片"""
    data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/view?{url_values}") as response:
        return response.read()

def run():
    # 加载工作流
    with open('workflow/default.json', 'r', encoding='utf-8') as f:
        workflow = json.load(f)
    
    # 设置 checkpoint
    workflow['1']['inputs']['ckpt_name'] = 'hassakuXLIllustrious_v13StyleA.safetensors'
    
    # 设置提示词
    workflow['6']['inputs']['text'] = 'masterpiece, best quality, anime girl'
    workflow['7']['inputs']['text'] = 'low quality, worst quality, blurry'
    
    # 设置尺寸
    workflow['8']['inputs']['width'] = 512
    workflow['8']['inputs']['height'] = 512
    
    # 提交任务
    client_id = str(uuid.uuid4())
    print(f"提交任务... client_id={client_id}")
    
    result = queue_prompt(workflow, client_id)
    print(f"提交结果: {result}")
    
    prompt_id = result.get('prompt_id')
    if not prompt_id:
        print("提交失败!")
        return
    
    # 等待完成
    print("等待生成完成...")
    while True:
        history = get_history(prompt_id)
        if history.get('status', {}).get('completed'):
            print("生成完成!")
            break
        elif history.get('status', {}).get('failed'):
            print("生成失败!")
            return
        import time
        time.sleep(1)
    
    # 获取图片
    outputs = history.get('outputs', {})
    for node_id, output in outputs.items():
        if 'images' in output:
            for img in output['images']:
                filename = img['filename']
                img_data = get_image(filename, img.get('subfolder', ''), img['type'])
                save_path = f'C:\\ComfyUI_windows_portable\\ComfyUI\\output\\test_{filename}'
                with open(save_path, 'wb') as f:
                    f.write(img_data)
                print(f"图片已保存: {save_path}")

if __name__ == '__main__':
    run()
