#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描 D 盘 models 文件夹，生成 checkpoints 和 loras 列表
支持子文件夹分类：1.5/xl/flux, face/clothes 等
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from config import CKPT_BASE_DIR, LORA_BASE_DIR, MODEL_EXTENSIONS

# 扫描路径配置（从 config.py 统一引用）
BASE_PATHS = {
    'ckpt': [CKPT_BASE_DIR],
    'lora': [LORA_BASE_DIR],
}

# 子类型映射（文件夹名 -> 显示名称）
TYPE_NAMES = {
    # Checkpoint types
    '1.5': 'SD 1.5',
    'xl': 'SDXL',
    'flux': 'Flux',
    # LoRA types
    '1.5': 'SD 1.5',
    'xl-style': 'XL-Style',
    '1.5-nsfw': 'SD 1.5 NSFW',
    'xl-nsfw': 'XL-NSFW',
    'xl-enhance': 'XL-Enhance',
    'xl-character': 'XL-Character',
    'xl-background': 'XL-Background',
    'xl-pose': 'XL-Pose',
    'xl-face': 'XL-Face',
    'xl-suit': 'XL-Suit',
    'my-lora': 'My LoRA',
    # General types
    'face': 'Face',
    'clothes': 'Clothes',
    'style': 'Style',
    'character': 'Character',
    'background': 'Background',
    'anime': 'Anime',
    'photo': 'Photo',
    'other': 'Other',
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'models')


def scan_folder(base_path, extensions=('.safetensors', '.ckpt')):
    """扫描文件夹，返回文件列表（相对路径）"""
    results = []
    
    if not os.path.exists(base_path):
        return results
    
    for root, dirs, files in os.walk(base_path):
        # 跳过隐藏目录和系统目录
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        
        for file in files:
            if file.endswith(extensions):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_path)
                results.append(rel_path.replace('\\', '/'))
    
    return results


def categorize_models(files, base_path):
    """将文件按子文件夹分类"""
    categories = {}
    
    for file in files:
        # 解析路径：可能的形式：
        # "model.safetensors"
        # "xl/model.safetensors"
        # "face/clothes/model.safetensors"
        
        parts = file.split('/')
        
        if len(parts) == 1:
            # 文件在根目录
            subdir = 'other'
        else:
            # 文件在子目录中
            subdir = parts[0]
        
        # 映射到标准类型
        type_key = subdir.lower()
        if type_key not in TYPE_NAMES:
            type_key = 'other'
        
        if type_key not in categories:
            categories[type_key] = {
                'key': type_key,
                'name': TYPE_NAMES.get(type_key, subdir),
                'models': []
            }
        
        categories[type_key]['models'].append({
            'filename': parts[-1],
            'path': file,
            'size': os.path.getsize(os.path.join(base_path, file))
        })
    
    return list(categories.values())


def scan_all():
    """扫描所有路径，生成列表"""
    all_checkpoints = {}
    all_loras = {}
    
    # 扫描 Checkpoints
    for base_path in BASE_PATHS['ckpt']:
        if os.path.exists(base_path):
            files = scan_folder(base_path)
            if files:
                cats = categorize_models(files, base_path)
                for cat in cats:
                    key = cat['key']
                    if key not in all_checkpoints:
                        all_checkpoints[key] = cat
                    else:
                        # 合并模型列表
                        all_checkpoints[key]['models'].extend(cat['models'])
    
    # 扫描 LoRAs
    for base_path in BASE_PATHS['lora']:
        if os.path.exists(base_path):
            files = scan_folder(base_path)
            if files:
                cats = categorize_models(files, base_path)
                for cat in cats:
                    key = cat['key']
                    if key not in all_loras:
                        all_loras[key] = cat
                    else:
                        all_loras[key]['models'].extend(cat['models'])
    
    return {
        'checkpoints': list(all_checkpoints.values()),
        'loras': list(all_loras.values())
    }


def save_lists(data):
    """保存列表到 JSON 文件"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 保存完整列表
    with open(f'{OUTPUT_DIR}/ckpt_list.json', 'w', encoding='utf-8') as f:
        json.dump({
            'status': 'success',
            'updated': str(Path(__file__).stat().st_mtime),
            'types': data['checkpoints'],
            'total_models': sum(len(t['models']) for t in data['checkpoints'])
        }, f, indent=2, ensure_ascii=False)
    
    with open(f'{OUTPUT_DIR}/lora_list.json', 'w', encoding='utf-8') as f:
        json.dump({
            'status': 'success',
            'updated': str(Path(__file__).stat().st_mtime),
            'types': data['loras'],
            'total_models': sum(len(t['models']) for t in data['loras'])
        }, f, indent=2, ensure_ascii=False)
    
    print('[OK] ckpt_list.json saved')
    print('[OK] lora_list.json saved')


def main():
    print('=' * 50)
    print('Scanning D:/ models folders...')
    print('=' * 50)
    
    data = scan_all()
    
    print('\nCheckpoints:')
    for cat in data['checkpoints']:
        print(f'  - {cat["name"]}: {len(cat["models"])} models')
    
    print('\nLoRAs:')
    for cat in data['loras']:
        print(f'  - {cat["name"]}: {len(cat["models"])} models')
    
    save_lists(data)
    print('\nDone!')


if __name__ == '__main__':
    main()
