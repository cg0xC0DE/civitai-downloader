#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Civitai 文件重命名工具
# 功能：
# - 遍历 D:\ckpt\xl 和 D:\lora\** 目录
# - 读取 txt 文件获取 URL
# - 调用 Civitai API 获取 file.name
# - 重命名文件为：{title}_{version}_{file.name}.{ext}
#
# 规则：
# - 模型没有对应 txt → 跳过
# - txt 里面没有 URL → 跳过
#
# 使用:
#     python civitai_rename.py
#

import os
import sys
import io
import json
import re
import requests
import time

# UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============ 配置 ============
BASE_DIR = "D:/"
CKPT_DIR = os.path.join(BASE_DIR, "ckpt")
CKPT_XL_DIR = os.path.join(CKPT_DIR, "xl")
LORA_DIR = os.path.join(BASE_DIR, "lora")
API_URL = "https://civitai.com/api/v1/models"
CACHE_FILE = "C:/workplace/civitai_rename_cache.json"

# ============ 缓存 ============
def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# ============ API ============
def get_file_info(model_id, version_id=None):
    try:
        url = f"{API_URL}/{model_id}"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # 找版本
        target_version = None
        for v in data.get("modelVersions", []):
            if version_id and v.get("id") == version_id:
                target_version = v
                break
            elif version_id is None:
                target_version = v
                break
        
        if not target_version:
            return None, None
        
        version_name = target_version.get("name", "v1")
        files = target_version.get("files", [])
        if files:
            file_name = files[0].get("name", "")
            return file_name, version_name
        
        return None, version_name
        
    except Exception as e:
        return None, None

def get_file_info_cached(model_id, version_id=None):
    cache_key = f"{model_id}_{version_id}" if version_id else model_id
    cache = load_cache()
    
    if cache_key in cache:
        print(f"[CACHE] {model_id}")
        return cache[cache_key]
    
    print(f"[API]  {model_id}")
    result = get_file_info(model_id, version_id)
    cache[cache_key] = result
    save_cache(cache)
    time.sleep(0.3)
    return result

def clean_filename(name):
    if not name:
        return ""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip()

# ============ 解析 ============
def parse_txt(txt_path):
    try:
        with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 找 URL - 格式: https://civitai.com/models/{id}?modelVersionId={version}
        url_match = re.search(r'civitai\.com/models/\d+[^\\n]*', content)
        if not url_match:
            return None, None, None
        
        url = url_match.group(0)
        
        # 提取 model_id
        model_id_match = re.search(r'/models/(\d+)', url)
        model_id = model_id_match.group(1) if model_id_match else None
        
        # 提取 version_id
        version_id_match = re.search(r'[?&]modelVersionId=(\d+)', url)
        version_id = int(version_id_match.group(1)) if version_id_match else None
        
        # 提取 title 从 URL slug
        slug_match = re.search(r'civitai\.com/models/\d+/(.+)', url)
        if slug_match:
            slug = slug_match.group(1).split('?')[0]
            title = slug.replace('-', ' ').replace('_', ' ').title()
        else:
            title = "Unknown"
        
        return model_id, version_id, title
        
    except Exception as e:
        return None, None, None

# ============ 重命名 ============
def process_file(txt_path):
    # 获取模型文件路径
    base_name = os.path.splitext(txt_path)[0]
    
    # 查找对应的模型文件
    safetensors_path = None
    for ext in ['.safetensors', '.ckpt', '.pt', '.bin']:
        test_path = base_name + ext
        if os.path.exists(test_path):
            safetensors_path = test_path
            break
    
    if not safetensors_path:
        return  # 模型没有对应 txt，跳过
    
    # 解析 txt 获取 URL
    model_id, version_id, title = parse_txt(txt_path)
    if not model_id:
        # txt 里面没有 URL，跳过
        print(f"[SKIP] No URL: {os.path.basename(txt_path)}")
        return
    
    # API 调用
    file_name, api_version = get_file_info_cached(model_id, version_id)
    if not file_name:
        print(f"[SKIP] No file.name: {model_id}")
        return
    
    if title == "Unknown" or not title:
        title = file_name.split('.')[0]
    
    # 新文件名: {title}_{version}_{file.name}.{ext}
    ext = os.path.splitext(safetensors_path)[1]
    # 防止扩展名重复
    if file_name.endswith(ext):
        file_name_clean = file_name[:-len(ext)]
    else:
        file_name_clean = file_name
    
    new_base = f"{title}_{api_version}_{file_name_clean}"
    new_base = clean_filename(new_base)
    
    new_safetensors = os.path.join(os.path.dirname(safetensors_path), f"{new_base}{ext}")
    new_txt = os.path.join(os.path.dirname(txt_path), f"{new_base}.txt")
    
    # 检查是否已标准化
    current = os.path.basename(safetensors_path)
    if '_v' in current and file_name in current:
        return
    
    try:
        os.rename(safetensors_path, new_safetensors)
        os.rename(txt_path, new_txt)
        print(f"[OK] {new_base}{ext}")
    except Exception as e:
        print(f"[FAIL] {e}")

def process_directory(directory):
    # 处理整个目录
    if not os.path.exists(directory):
        print(f"[WARN] Directory not found: {directory}")
        return
    
    txt_files = sorted([f for f in os.listdir(directory) if f.endswith('.txt')])
    
    for txt_file in txt_files:
        process_file(os.path.join(directory, txt_file))

def main():
    print("=" * 60)
    print("Civitai 文件重命名工具")
    print("范围: ckpt/xl + lora/**/**")
    print("规则: 无 txt 跳过，无 URL 跳过")
    print("=" * 60)
    
    # 1. 处理 ckpt/xl
    if os.path.exists(CKPT_XL_DIR):
        print(f"\n{'='*60}")
        print(f"处理: {CKPT_XL_DIR}")
        print(f"{'='*60}")
        process_directory(CKPT_XL_DIR)
    
    # 2. 处理 lora 子目录
    if os.path.exists(LORA_DIR):
        subdirs = sorted([d for d in os.listdir(LORA_DIR) 
                        if os.path.isdir(os.path.join(LORA_DIR, d))])
        
        for subdir in subdirs:
            subdir_path = os.path.join(LORA_DIR, subdir)
            print(f"\n{'='*60}")
            print(f"处理: {subdir_path}")
            print(f"{'='*60}")
            process_directory(subdir_path)
    
    print("\n" + "=" * 60)
    print("全部完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
