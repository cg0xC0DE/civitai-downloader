#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI API Client
通过 HTTP API 控制本地 ComfyUI 服务

Usage:
    from comfyui import ComfyUI
    
    client = ComfyUI()
    # 加载工作流，修改模型，提交生成
"""

import os
import sys
import json
import time
from typing import Optional, Dict, Any
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(__file__))
from config import COMFYUI_URL, WORKFLOW_DIR

# 默认配置（从 config.py 统一引用）
DEFAULT_COMFYUI_URL = f"http://{COMFYUI_URL}"
DEFAULT_WORKFLOW_DIR = WORKFLOW_DIR


class ComfyUI:
    """ComfyUI API 客户端"""
    
    def __init__(
        self,
        server_url: str = DEFAULT_COMFYUI_URL,
        workflow_dir: str = DEFAULT_WORKFLOW_DIR
    ):
        self.server_url = server_url.rstrip('/')
        self.workflow_dir = workflow_dir
        
        # 确保工作流目录存在
        os.makedirs(workflow_dir, exist_ok=True)
    
    def _api_request(self, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """发送 API 请求到 ComfyUI"""
        url = f"{self.server_url}{endpoint}"
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        try:
            if data:
                json_data = json.dumps(data).encode('utf-8')
                req = Request(url, data=json_data, headers=headers, method='POST')
            else:
                req = Request(url, headers=headers, method='GET')
            
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        
        except HTTPError as e:
            raise Exception(f"HTTP Error {e.code}: {e.reason}")
        except URLError as e:
            raise Exception(f"Connection Error: {e.reason}")
    
    def get_queue(self) -> Dict:
        """获取队列状态"""
        return self._api_request('/api/queue')
    
    def load_workflow(self, workflow_path: str) -> Dict:
        """加载工作流 JSON 文件"""
        with open(workflow_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def save_workflow(self, workflow: Dict, filename: str) -> str:
        """保存工作流到文件"""
        filepath = os.path.join(self.workflow_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(workflow, f, indent=2, ensure_ascii=False)
        return filepath
    
    def set_checkpoint(self, workflow: Dict, ckpt_name: str) -> Dict:
        """
        设置 Checkpoint 模型
        
        Args:
            workflow: 工作流字典
            ckpt_name: 模型文件名（如 'model.safetensors'）
        
        Returns:
            修改后的工作流
        """
        for node in workflow.get('nodes', []):
            if node.get('type') == 'CheckpointLoaderSimple':
                if 'inputs' not in node:
                    node['inputs'] = {}
                node['inputs']['ckpt_name'] = ckpt_name
                print(f"[ComfyUI] 已设置 Checkpoint: {ckpt_name}")
                break
        return workflow
    
    def set_lora(
        self,
        workflow: Dict,
        lora_name: str,
        strength_model: float = 1.0,
        strength_clip: float = 1.0,
        lora_node_index: int = 0
    ) -> Dict:
        """
        设置 LoRA
        
        Args:
            workflow: 工作流字典
            lora_name: LoRA 文件名
            strength_model: 模型强度
            strength_clip: CLIP 强度
            lora_node_index: LoRA 节点索引（第几个 LoraLoader）
        """
        lora_count = 0
        for node in workflow.get('nodes', []):
            if node.get('type') == 'LoraLoader':
                if lora_count == lora_node_index:
                    if 'inputs' not in node:
                        node['inputs'] = {}
                    node['inputs']['lora_name'] = lora_name
                    node['inputs']['strength_model'] = strength_model
                    node['inputs']['strength_clip'] = strength_clip
                    print(f"[ComfyUI] 已设置 LoRA: {lora_name} (model={strength_model}, clip={strength_clip})")
                    break
                lora_count += 1
        return workflow
    
    def set_prompt(self, workflow: Dict, prompt: str, node_index: int = 0) -> Dict:
        """
        设置正向提示词
        
        Args:
            workflow: 工作流字典
            prompt: 提示词文本
            node_index: CLIPTextEncode 节点索引
        """
        clip_count = 0
        for node in workflow.get('nodes', []):
            if node.get('type') == 'CLIPTextEncode':
                if clip_count == node_index:
                    if 'widgets_values' in node:
                        node['widgets_values'][0] = prompt
                    print(f"[ComfyUI] 已设置提示词: {prompt[:50]}...")
                    break
                clip_count += 1
        return workflow
    
    def set_negative_prompt(self, workflow: Dict, prompt: str) -> Dict:
        """设置负向提示词"""
        clip_count = 0
        for node in workflow.get('nodes', []):
            if node.get('type') == 'CLIPTextEncode':
                if clip_count == 1:  # 第二个通常是负向
                    if 'widgets_values' in node:
                        node['widgets_values'][0] = prompt
                    print(f"[ComfyUI] 已设置负向提示词: {prompt[:50]}...")
                    break
                clip_count += 1
        return workflow
    
    def set_latent_size(
        self,
        workflow: Dict,
        width: int = 1024,
        height: int = 1024
    ) -> Dict:
        """设置生成图片尺寸"""
        for node in workflow.get('nodes', []):
            if node.get('type') == 'EmptyLatentImage':
                if 'widgets_values' in node:
                    node['widgets_values'][0] = width
                    node['widgets_values'][1] = height
                print(f"[ComfyUI] 已设置尺寸: {width}x{height}")
                break
        return workflow
    
    def queue_prompt(self, workflow: Dict) -> Dict:
        """
        将工作流提交到队列

        Args:
            workflow: 工作流字典

        Returns:
            API 响应
        """
        data = {
            "prompt": workflow,
            "client_id": "civitai-downloader"
        }
        result = self._api_request('/api/prompt', data)
        
        if 'prompt_id' in result:
            print(f"[ComfyUI] 已提交任务，ID: {result['prompt_id']}")
        else:
            print(f"[ComfyUI] 响应: {result}")
        
        return result
    
    def get_history(self, prompt_id: Optional[str] = None) -> Dict:
        """获取生成历史"""
        if prompt_id:
            return self._api_request(f'/api/history/{prompt_id}')
        return self._api_request('/api/history')
    
    def get_image(self, filename: str, subfolder: str = '', folder_type: str = 'output') -> bytes:
        """
        获取生成的图片
        
        Args:
            filename: 图片文件名
            subfolder: 子文件夹
            folder_type: 'output' 或 'temp'
        
        Returns:
            图片二进制数据
        """
        url = f"{self.server_url}/api/view"
        params = f"filename={filename}&type={folder_type}"
        if subfolder:
            params += f"&subfolder={subfolder}"
        
        req = Request(f"{url}?{params}")
        with urlopen(req) as response:
            return response.read()
    
    def wait_for_completion(
        self,
        prompt_id: str,
        poll_interval: int = 2,
        timeout: int = 300
    ) -> Dict:
        """
        等待任务完成
        
        Args:
            prompt_id: 任务 ID
            poll_interval: 轮询间隔（秒）
            timeout: 超时时间（秒）
        
        Returns:
            生成结果
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            history = self.get_history(prompt_id)
            
            if prompt_id in history:
                status = history[prompt_id]
                outputs = status.get('outputs', {})
                
                # 检查是否有图片输出
                for node_id, output in outputs.items():
                    if 'images' in output:
                        return {
                            'status': 'success',
                            'prompt_id': prompt_id,
                            'outputs': outputs
                        }
                
                # 检查是否有错误
                if 'error' in status:
                    return {
                        'status': 'error',
                        'prompt_id': prompt_id,
                        'error': status['error']
                    }
            
            time.sleep(poll_interval)
        
        return {
            'status': 'timeout',
            'prompt_id': prompt_id,
            'message': f'超时（{timeout}秒）'
        }
    
    def generate(
        self,
        workflow_path: Optional[str] = None,
        ckpt_name: Optional[str] = None,
        lora_name: Optional[str] = None,
        prompt: str = "",
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        strength_model: float = 1.0,
        strength_clip: float = 1.0,
        wait: bool = True
    ) -> Dict:
        """
        一键生成图片
        
        Args:
            workflow_path: 工作流文件路径（可选）
            ckpt_name: Checkpoint 模型名
            lora_name: LoRA 模型名（可选）
            prompt: 正向提示词
            negative_prompt: 负向提示词
            width: 宽度
            height: 高度
            strength_model: LoRA 模型强度
            strength_clip: LoRA CLIP 强度
            wait: 是否等待完成
        
        Returns:
            生成结果
        """
        # 加载工作流
        if workflow_path and os.path.exists(workflow_path):
            workflow = self.load_workflow(workflow_path)
        else:
            # 使用默认工作流
            workflow = self._get_default_workflow()
        
        # 设置参数
        if ckpt_name:
            workflow = self.set_checkpoint(workflow, ckpt_name)
        
        if lora_name:
            workflow = self.set_lora(
                workflow, lora_name,
                strength_model=strength_model,
                strength_clip=strength_clip
            )
        
        if prompt:
            workflow = self.set_prompt(workflow, prompt)
        
        if negative_prompt:
            workflow = self.set_negative_prompt(workflow, negative_prompt)
        
        workflow = self.set_latent_size(workflow, width, height)
        
        # 提交任务
        result = self.queue_prompt(workflow)
        
        if wait and 'prompt_id' in result:
            result = self.wait_for_completion(result['prompt_id'])
        
        return result
    
    def _get_default_workflow(self) -> Dict:
        """获取默认工作流"""
        return {
            "last_node_id": 9,
            "last_link_id": 9,
            "nodes": [
                {
                    "id": 5,
                    "type": "CheckpointLoaderSimple",
                    "pos": [0, 0],
                    "size": [315, 118],
                    "inputs": {"ckpt_name": "model.safetensors"}
                },
                {
                    "id": 6,
                    "type": "CLIPTextEncode",
                    "pos": [0, 200],
                    "widgets_values": ["beautiful scenery nature"]
                },
                {
                    "id": 7,
                    "type": "CLIPTextEncode",
                    "pos": [0, 400],
                    "widgets_values": ["bad quality blurry"]
                },
                {
                    "id": 8,
                    "type": "EmptyLatentImage",
                    "pos": [0, 600],
                    "widgets_values": [1024, 1024, 1]
                },
                {
                    "id": 9,
                    "type": "KSampler",
                    "pos": [400, 300],
                    "inputs": {
                        "model": 5,
                        "positive": 6,
                        "negative": 7,
                        "latent_image": 8
                    }
                },
                {
                    "id": 10,
                    "type": "VAEDecode",
                    "pos": [800, 300],
                    "inputs": {
                        "samples": 9,
                        "vae": 5
                    }
                },
                {
                    "id": 11,
                    "type": "SaveImage",
                    "pos": [1200, 300],
                    "inputs": {"images": 10}
                }
            ]
        }


# 便捷函数
def quick_generate(
    prompt: str,
    ckpt_name: str = "model.safetensors",
    **kwargs
) -> Dict:
    """快速生成图片"""
    client = ComfyUI()
    return client.generate(
        workflow_path=None,
        ckpt_name=ckpt_name,
        prompt=prompt,
        **kwargs
    )


if __name__ == "__main__":
    # 测试
    client = ComfyUI()
    
    print("检查 ComfyUI 连接...")
    try:
        queue = client.get_queue()
        print(f"队列状态: {queue}")
        print("\n✅ ComfyUI 连接成功！")
    except Exception as e:
        print(f"❌ 连接失败: {e}")
