#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工作流处理器
加载 workflow.json，替换节点值，提交到 ComfyUI

Usage:
    from workflow_processor import WorkflowProcessor
    
    processor = WorkflowProcessor()
    result = processor.run(
        workflow_name="txt2img",
        ckpt_name="model.safetensors",
        lora_name="lora.safetensors",
        prompt="anime girl",
        strength_model=0.8,
        strength_clip=0.8
    )
"""

import os
import sys
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from cache_manager import model_cache


class WorkflowProcessor:
    """工作流处理器"""
    
    def __init__(self, workflows_dir: str = None):
        self.workflows_dir = workflows_dir or os.path.join(
            os.path.dirname(__file__), 'workflows'
        )
    
    def load_workflow(self, name: str) -> Optional[Dict]:
        """加载工作流"""
        # 尝试直接名称或完整文件名
        if not name.endswith('.json'):
            name = name + '.json'
        
        filepath = os.path.join(self.workflows_dir, name)
        
        if not os.path.exists(filepath):
            return None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_replaceable_nodes(self, workflow: Dict) -> Dict:
        """从 workflow metadata 获取可替换节点信息"""
        metadata = workflow.get('_metadata', {})
        return metadata.get('nodes', {})
    
    def replace_checkpoint(self, workflow: Dict, ckpt_name: str) -> Dict:
        """替换 Checkpoint"""
        nodes = workflow.get('nodes', [])
        replaced = False
        
        for node in nodes:
            node_type = node.get('type', '')
            
            # CheckpointLoaderSimple / CheckpointLoader
            if node_type in ['CheckpointLoaderSimple', 'CheckpointLoader']:
                if 'inputs' in node and 'ckpt_name' in node['inputs']:
                    node['inputs']['ckpt_name'] = ckpt_name
                    print(f"[Workflow] Replaced checkpoint: {ckpt_name}")
                    replaced = True
                elif 'widgets_values' in node:
                    node['widgets_values'][0] = ckpt_name
                    print(f"[Workflow] Replaced checkpoint (widgets_values): {ckpt_name}")
                    replaced = True
        
        return workflow
    
    def replace_lora(
        self,
        workflow: Dict,
        lora_name: str,
        strength_model: float = 1.0,
        strength_clip: float = 1.0,
        node_index: int = 0
    ) -> Dict:
        """替换 LoRA - 支持多种 LoRA 节点类型"""
        nodes = workflow.get('nodes', [])
        replaced = False
        
        for node in nodes:
            node_type = node.get('type', '')
            
            # 1. 单个 LoRA Loader
            if node_type in ['LoraLoader', 'LoraLoaderModelOnly']:
                if 'inputs' in node:
                    node['inputs']['lora_name'] = lora_name
                    node['inputs']['strength_model'] = strength_model
                    node['inputs']['strength_clip'] = strength_clip
                    print(f"[Workflow] Replaced LoraLoader: {lora_name}")
                    replaced = True
            
            elif 'widgets_values' in node:
                node['widgets_values'][0] = lora_name
                print(f"[Workflow] Replaced LoraLoader (widgets_values): {lora_name}")
                replaced = True
            
            # 2. Lora Loader Stack (rgthree) - 堆叠多个 LoRA
            # widgets_values: [lora1, strength1, lora2, strength2, ...]
            elif node_type in ['Lora Loader Stack (rgthree)', 'LoraLoaderStack']:
                if 'widgets_values' in node:
                    # 找到对应的 LoRA 位置 (偶数索引是 lora 名，奇数是 strength)
                    idx = node_index * 2
                    if idx < len(node['widgets_values']):
                        old_name = node['widgets_values'][idx]
                        node['widgets_values'][idx] = lora_name
                        # 更新强度
                        if idx + 1 < len(node['widgets_values']):
                            node['widgets_values'][idx + 1] = strength_model
                        print(f"[Workflow] Replaced LoraLoaderStack[{node_index}]: {old_name} -> {lora_name}")
                        replaced = True
        
        return workflow
    
    def replace_lora_stack(
        self,
        workflow: Dict,
        loras: List[Dict]  # [{"name": "lora1.safetensors", "strength": 0.5}, ...]
    ) -> Dict:
        """替换 Lora Loader Stack 的所有 LoRA"""
        nodes = workflow.get('nodes', [])
        
        for node in nodes:
            node_type = node.get('type', '')
            
            if node_type in ['Lora Loader Stack (rgthree)', 'LoraLoaderStack']:
                if 'widgets_values' in node:
                    # 构建新的 widgets_values
                    new_values = []
                    for lora in loras:
                        new_values.append(lora['name'])
                        new_values.append(lora.get('strength', 1.0))
                    
                    # 填充到原长度（补 None）
                    while len(new_values) < len(node['widgets_values']):
                        new_values.append(None)
                    
                    node['widgets_values'] = new_values[:len(node['widgets_values'])]
                    print(f"[Workflow] Replaced LoraLoaderStack with {len(loras)} LoRAs")
        
        return workflow
    
    def replace_prompt(self, workflow: Dict, positive: str, negative: str = "") -> Dict:
        """替换提示词"""
        nodes = workflow.get('nodes', [])
        clip_count = 0
        
        for node in nodes:
            node_type = node.get('type', '')
            
            if node_type == 'CLIPTextEncode':
                if clip_count == 0:
                    # 正向
                    if 'widgets_values' in node:
                        node['widgets_values'][0] = positive
                    print(f"[Workflow] Set positive prompt: {positive[:50]}...")
                elif clip_count == 1 and negative:
                    # 负向
                    if 'widgets_values' in node:
                        node['widgets_values'][0] = negative
                    print(f"[Workflow] Set negative prompt: {negative[:50]}...")
                clip_count += 1
        
        return workflow
    
    def replace_size(self, workflow: Dict, width: int, height: int) -> Dict:
        """替换图片尺寸"""
        nodes = workflow.get('nodes', [])
        
        for node in nodes:
            node_type = node.get('type', '')
            
            if node_type in ['EmptyLatentImage', 'LatentImage']:
                if 'widgets_values' in node:
                    node['widgets_values'][0] = width
                    node['widgets_values'][1] = height
                print(f"[Workflow] Set size: {width}x{height}")
                break
        
        return workflow
    
    def replace_by_config(self, workflow: Dict, config: Dict) -> Dict:
        """根据配置字典批量替换"""
        # 替换 checkpoint
        if 'ckpt_name' in config:
            workflow = self.replace_checkpoint(workflow, config['ckpt_name'])
        
        # 替换 lora
        if 'lora_name' in config:
            workflow = self.replace_lora(
                workflow,
                config['lora_name'],
                strength_model=config.get('strength_model', 1.0),
                strength_clip=config.get('strength_clip', 1.0),
                node_index=config.get('lora_index', 0)
            )
        
        # 替换 prompt
        if 'prompt' in config:
            workflow = self.replace_prompt(
                workflow,
                config['prompt'],
                config.get('negative_prompt', '')
            )
        
        # 替换 size
        if 'width' in config and 'height' in config:
            workflow = self.replace_size(
                workflow,
                config['width'],
                config['height']
            )
        
        return workflow
    
    def run(
        self,
        workflow_name: str,
        ckpt_name: str,
        lora_name: Optional[str] = None,
        prompt: str = "",
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        strength_model: float = 1.0,
        strength_clip: float = 1.0,
        **extra_config
    ) -> Dict:
        """
        运行工作流
        
        Args:
            workflow_name: 工作流名称（不含 .json）
            ckpt_name: Checkpoint 模型名
            lora_name: LoRA 模型名（可选）
            prompt: 正向提示词
            negative_prompt: 负向提示词
            width: 宽度
            height: 高度
            strength_model: LoRA 模型强度
            strength_clip: LoRA CLIP 强度
        
        Returns:
            Dict with status, message, and optionally prompt_id
        """
        # 1. 加载工作流
        workflow = self.load_workflow(workflow_name)
        if not workflow:
            return {
                'status': 'error',
                'message': f"Workflow not found: {workflow_name}"
            }
        
        # 2. 替换节点
        config = {
            'ckpt_name': ckpt_name,
            'lora_name': lora_name,
            'prompt': prompt,
            'negative_prompt': negative_prompt,
            'width': width,
            'height': height,
            'strength_model': strength_model,
            'strength_clip': strength_clip,
            **extra_config
        }
        
        workflow = self.replace_by_config(workflow, config)
        
        # 3. 清理 metadata（避免提交时出错）
        if '_metadata' in workflow:
            del workflow['_metadata']
        
        # 4. 返回处理后的工作流
        return {
            'status': 'success',
            'workflow': workflow,
            'config': config
        }


class WorkflowTemplates:
    """预定义工作流模板"""
    
    @staticmethod
    def create_txt2img_workflow() -> Dict:
        """创建基础 txt2img 工作流"""
        return {
            "_metadata": {
                "description": "基础文生图工作流",
                "nodes": {
                    "checkpoint": {
                        "type": "CheckpointLoaderSimple",
                        "field": "ckpt_name",
                        "description": "Checkpoint 模型"
                    },
                    "lora": {
                        "type": "LoraLoader",
                        "optional": True,
                        "description": "LoRA 模型"
                    },
                    "positive": {
                        "type": "CLIPTextEncode",
                        "index": 0,
                        "field": "text",
                        "description": "正向提示词"
                    },
                    "negative": {
                        "type": "CLIPTextEncode", 
                        "index": 1,
                        "field": "text",
                        "description": "负向提示词"
                    },
                    "size": {
                        "type": "EmptyLatentImage",
                        "fields": ["width", "height"],
                        "description": "图片尺寸"
                    }
                }
            },
            "nodes": [
                {
                    "id": 3,
                    "type": "CheckpointLoaderSimple",
                    "pos": [0, 0],
                    "size": [315, 118],
                    "inputs": {"ckpt_name": ""}
                },
                {
                    "id": 4,
                    "type": "CLIPTextEncode",
                    "pos": [0, 200],
                    "size": [400, 200],
                    "widgets_values": [""]
                },
                {
                    "id": 5,
                    "type": "CLIPTextEncode",
                    "pos": [0, 400],
                    "size": [400, 200],
                    "widgets_values": ["blurry, low quality"]
                },
                {
                    "id": 6,
                    "type": "EmptyLatentImage",
                    "pos": [0, 600],
                    "size": [315, 106],
                    "widgets_values": [1024, 1024, 1]
                },
                {
                    "id": 7,
                    "type": "KSampler",
                    "pos": [400, 300],
                    "inputs": {
                        "model": 3,
                        "positive": 4,
                        "negative": 5,
                        "latent_image": 6
                    }
                },
                {
                    "id": 8,
                    "type": "VAEDecode",
                    "pos": [800, 300],
                    "inputs": {"samples": 7, "vae": 3}
                },
                {
                    "id": 9,
                    "type": "SaveImage",
                    "pos": [1200, 300],
                    "inputs": {"images": 8}
                }
            ]
        }


# 创建示例工作流
def create_sample_workflows():
    """创建示例工作流文件"""
    workflows_dir = os.path.join(os.path.dirname(__file__), 'workflows')
    os.makedirs(workflows_dir, exist_ok=True)
    
    # txt2img 基础工作流
    txt2img = WorkflowTemplates.create_txt2img_workflow()
    
    with open(os.path.join(workflows_dir, 'txt2img.json'), 'w', encoding='utf-8') as f:
        json.dump(txt2img, f, indent=2, ensure_ascii=False)
    
    print(f"Created: {workflows_dir}/txt2img.json")
    
    # img2img 工作流（占位）
    img2img = {
        "_metadata": {
            "description": "图生图工作流",
            "nodes": {
                "checkpoint": {"type": "CheckpointLoaderSimple", "field": "ckpt_name"},
                "image": {"type": "LoadImage", "field": "image", "description": "输入图片"}
            }
        },
        "nodes": []
    }
    
    with open(os.path.join(workflows_dir, 'img2img.json'), 'w', encoding='utf-8') as f:
        json.dump(img2img, f, indent=2, ensure_ascii=False)
    
    print(f"Created: {workflows_dir}/img2img.json")


if __name__ == "__main__":
    # 创建示例工作流
    create_sample_workflows()
    
    # 测试
    print("\n" + "="*50)
    print("Workflow Processor Test")
    print("="*50)
    
    processor = WorkflowProcessor()
    
    # 测试加载和替换
    result = processor.run(
        workflow_name="txt2img",
        ckpt_name="hassakuXLHentai_v13.safetensors",
        lora_name="example_lora.safetensors",
        prompt="beautiful anime girl",
        negative_prompt="blurry, low quality",
        width=512,
        height=512
    )
    
    if result['status'] == 'success':
        print("\nWorkflow prepared successfully!")
        print(f"Config: {result['config']}")
    else:
        print(f"\nError: {result['message']}")
