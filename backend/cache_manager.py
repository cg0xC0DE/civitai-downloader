#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件缓存管理器
扫描 D 盘模型目录 + ComfyUI 工作流目录，支持增量更新

Usage:
    cache = ModelCache()
    cache.get_checkpoints()  # 获取 checkpoint 列表
    cache.get_loras()       # 获取 lora 列表
    cache.get_workflows()    # 获取工作流列表
    cache.refresh_all()       # 刷新所有缓存
"""

import os
import sys
import json
import time
import glob
import threading
from typing import List, Dict, Optional
from pathlib import Path

# 路径配置（从 config.py 统一引用）
sys.path.insert(0, os.path.dirname(__file__))
from config import (
    CACHE_DIR, COMFYUI_WORKFLOWS_DIR,
    get_all_model_paths, MODEL_EXTENSIONS,
)

CACHE_FILE_CHECKPOINTS = os.path.join(CACHE_DIR, 'checkpoints.json')
CACHE_FILE_LORAS = os.path.join(CACHE_DIR, 'loras.json')
CACHE_FILE_WORKFLOWS = os.path.join(CACHE_DIR, 'workflows.json')

# 动态扫描获取模型目录列表
MODEL_PATHS = get_all_model_paths()

# 缓存有效期（秒）
CACHE_TTL = 300  # 5分钟


class CacheManager:
    """通用缓存管理器"""
    
    def __init__(self, cache_file: str, ttl: int = CACHE_TTL):
        self.cache_file = cache_file
        self.ttl = ttl
        self._cache = None
        self._last_load = 0
        self._lock = threading.Lock()
    
    def _load_from_disk(self) -> Optional[dict]:
        """从磁盘加载缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'updated' in data:
                        if time.time() - data['updated'] < self.ttl:
                            return data
        except Exception:
            pass
        return None
    
    def _save_to_disk(self, data: dict):
        """保存缓存到磁盘"""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            data['updated'] = time.time()
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Cache] Save failed: {e}")
    
    def get(self, refresh: bool = False) -> dict:
        """获取缓存数据"""
        with self._lock:
            if refresh or self._cache is None:
                disk_data = self._load_from_disk()
                if disk_data and not refresh:
                    self._cache = disk_data
                else:
                    self._cache = self._fetch_from_source()
                    self._save_to_disk(self._cache)
                self._last_load = time.time()
            return self._cache
    
    def _fetch_from_source(self) -> dict:
        """子类实现：获取原始数据"""
        raise NotImplementedError


class CheckpointCache(CacheManager):
    """Checkpoint 模型缓存"""
    
    def __init__(self):
        super().__init__(CACHE_FILE_CHECKPOINTS, ttl=600)  # 10分钟
    
    def _fetch_from_source(self) -> dict:
        """扫描 D 盘获取 checkpoint 列表"""
        models = []
        
        for base_path in MODEL_PATHS['checkpoints']:
            if not os.path.exists(base_path):
                continue
            
            for pattern in ['*.safetensors', '*.ckpt', '*.pth']:
                for filepath in glob.glob(os.path.join(base_path, pattern)):
                    filename = os.path.basename(filepath)
                    if filename not in [m['name'] for m in models]:
                        models.append({
                            'name': filename,
                            'path': filepath,
                            'size': os.path.getsize(filepath),
                            'subdir': os.path.basename(base_path)
                        })
        
        return {
            'items': models,
            'count': len(models)
        }
    
    def get_list(self, refresh: bool = False) -> List[str]:
        """获取纯文件名列表"""
        data = self.get(refresh)
        return [m['name'] for m in data.get('items', [])]


class LorasCache(CacheManager):
    """LoRA 模型缓存"""
    
    def __init__(self):
        super().__init__(CACHE_FILE_LORAS, ttl=600)
    
    def _fetch_from_source(self) -> dict:
        """扫描 D 盘获取 lora 列表"""
        models = []
        
        for base_path in MODEL_PATHS['loras']:
            if not os.path.exists(base_path):
                continue
            
            for pattern in ['*.safetensors', '*.ckpt', '*.pth']:
                for filepath in glob.glob(os.path.join(base_path, pattern)):
                    filename = os.path.basename(filepath)
                    if filename not in [m['name'] for m in models]:
                        models.append({
                            'name': filename,
                            'path': filepath,
                            'size': os.path.getsize(filepath),
                            'subdir': os.path.basename(base_path)
                        })
        
        return {
            'items': models,
            'count': len(models)
        }
    
    def get_list(self, refresh: bool = False) -> List[Dict]:
        """获取完整列表（包含 subdir）"""
        data = self.get(refresh)
        return data.get('items', [])


class WorkflowCache(CacheManager):
    """工作流缓存 - 支持 Civitai Downloader 和 ComfyUI 两处工作流"""
    
    def __init__(self):
        super().__init__(CACHE_FILE_WORKFLOWS, ttl=60)  # 1分钟
    
    def _fetch_from_source(self) -> dict:
        """扫描 workflows 目录（包括 ComfyUI 目录）"""
        workflows = []
        seen = set()  # 用于去重
        
        # 只扫描项目内 workflows 目录
        default_dir = os.path.join(os.path.dirname(__file__), 'workflows')
        self._scan_workflow_dir(default_dir, workflows, seen, source='local')
        
        # 按名称排序
        workflows.sort(key=lambda x: x['name'])
        
        return {
            'items': workflows,
            'count': len(workflows)
        }
    
    def _scan_workflow_dir(self, workflow_dir: str, workflows: list, seen: set, source: str = 'local'):
        """扫描单个工作流目录"""
        if not os.path.exists(workflow_dir):
            return
        
        for filepath in glob.glob(os.path.join(workflow_dir, '*.json')):
            filename = os.path.basename(filepath)
            
            # 去重
            if filename in seen:
                continue
            seen.add(filename)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    nodes = self._analyze_replaceable_nodes(data)
                    metadata = data.get('_metadata', {})
                    
                    workflows.append({
                        'name': filename.replace('.json', ''),
                        'filename': filename,
                        'path': filepath,
                        'source': source,
                        'description': metadata.get('description', '') or self._guess_description(filename),
                        'nodes': nodes,
                        'ckpt_loader': nodes.get('checkpoint'),
                        'lora_loader': nodes.get('lora') or nodes.get('lora_stack'),
                        'updated': os.path.getmtime(filepath)
                    })
            except Exception as e:
                print(f"[Cache] Error reading workflow {filename}: {e}")
    
    def _analyze_replaceable_nodes(self, workflow: Dict) -> Dict:
        """分析工作流中的可替换节点"""
        nodes_info = {}
        nodes = workflow.get('nodes', [])
        
        for node in nodes:
            node_type = node.get('type', '')
            
            # Checkpoint Loader
            if node_type in ['CheckpointLoaderSimple', 'CheckpointLoader']:
                nodes_info['checkpoint'] = {
                    'type': node_type,
                    'method': 'widgets_values' if 'widgets_values' in node else 'inputs',
                    'index': 0
                }
            
            # LoRA Loader (单个)
            if node_type in ['LoraLoader', 'LoraLoaderModelOnly']:
                if 'lora' not in nodes_info:
                    nodes_info['lora'] = {
                        'type': node_type,
                        'method': 'widgets_values' if 'widgets_values' in node else 'inputs'
                    }
            
            # Lora Loader Stack (rgthree) - 多 LoRA
            if node_type == 'Lora Loader Stack (rgthree)':
                nodes_info['lora_stack'] = {
                    'type': node_type,
                    'method': 'widgets_values'
                }
                if 'lora' not in nodes_info:
                    nodes_info['lora'] = nodes_info['lora_stack']
            
            # CLIP Text Encode
            if node_type == 'CLIPTextEncode':
                if 'positive' not in nodes_info:
                    nodes_info['positive'] = {
                        'type': node_type,
                        'method': 'widgets_values',
                        'index': 0
                    }
                elif 'negative' not in nodes_info:
                    nodes_info['negative'] = {
                        'type': node_type,
                        'method': 'widgets_values',
                        'index': 1
                    }
            
            # Empty Latent Image
            if node_type in ['EmptyLatentImage', 'LatentImage']:
                nodes_info['size'] = {
                    'type': node_type,
                    'method': 'widgets_values',
                    'index': 0
                }
        
        return nodes_info
    
    def _guess_description(self, filename: str) -> str:
        """根据文件名猜测描述"""
        name = filename.lower().replace('.json', '').replace('_', ' ').replace('-', ' ')
        return f"工作流: {name}"
    
    def get_list(self, refresh: bool = False) -> List[Dict]:
        """获取工作流列表"""
        data = self.get(refresh)
        return data.get('items', [])
    
    def get_workflow(self, name: str) -> Optional[Dict]:
        """获取单个工作流详情"""
        workflows = self.get_list()
        for wf in workflows:
            if wf['name'] == name or wf['filename'] == name:
                return wf
        return None


class ModelCache:
    """统一的模型缓存管理器"""
    
    def __init__(self):
        self.checkpoints = CheckpointCache()
        self.loras = LorasCache()
        self.workflows = WorkflowCache()
    
    def refresh_all(self):
        """刷新所有缓存"""
        self.checkpoints.get(refresh=True)
        self.loras.get(refresh=True)
        self.workflows.get(refresh=True)
    
    def get_status(self) -> Dict:
        """获取缓存状态"""
        return {
            'comfyui_workflows_dir': COMFYUI_WORKFLOWS_DIR,
            'checkpoints': {
                'count': len(self.checkpoints.get_list()),
                'updated': self.checkpoints.get().get('updated', 0)
            },
            'loras': {
                'count': len(self.loras.get_list()),
                'updated': self.loras.get().get('updated', 0)
            },
            'workflows': {
                'count': len(self.workflows.get_list()),
                'updated': self.workflows.get().get('updated', 0)
            }
        }


# 单例实例
model_cache = ModelCache()


if __name__ == "__main__":
    print("="*60)
    print("Model Cache System")
    print("="*60)
    print(f"\nComfyUI Workflows Dir: {COMFYUI_WORKFLOWS_DIR}")
    
    print("\n--- Cache Status ---")
    status = model_cache.get_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
    
    print("\n--- Workflows ---")
    for wf in model_cache.workflows.get_list():
        source = wf.get('source', 'local')
        print(f"\n[{source}] {wf['name']}")
        print(f"  Description: {wf.get('description', 'N/A')}")
        print(f"  Nodes: {list(wf.get('nodes', {}).keys())}")
        if wf.get('ckpt_loader'):
            print(f"  Checkpoint: {wf['ckpt_loader']}")
    
    print("\n--- Checkpoints (Top 5) ---")
    for ckpt in model_cache.checkpoints.get_list()[:5]:
        print(f"  - {ckpt}")
