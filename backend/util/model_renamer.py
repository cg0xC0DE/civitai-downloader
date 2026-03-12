#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai 模型重命名 & 索引构建工具

功能：
- 遍历 CKPT_BASE_DIR / LORA_BASE_DIR 下所有子目录
- 读取 txt 文件获取 Civitai URL → 解析 modelId / versionId
- 调用 Civitai API 获取 model name / version name / file name
- 重命名模型文件为: {title}_{version}_{file.name}.{ext}
- 同时更新 cache/model_index.json 索引

规则：
- 模型没有对应 txt → 仅跳过重命名，但仍然不会入索引
- txt 里面没有 URL → 跳过

Usage:
    python -m util.model_renamer          # 在 backend/ 目录下执行
    python -m util.model_renamer --dry    # 仅预览，不执行
"""

import os
import sys
import io
import json
import re
import time

# 保证能 import 同级和上级模块
_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from config import (
    CKPT_BASE_DIR, LORA_BASE_DIR, EMBEDDING_BASE_DIR, MODEL_EXTENSIONS,
    CIVITAI_API_URL, CIVITAI_API_TOKEN,
)
from util import model_index

# UTF-8 stdout
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============ API 缓存 ============
# 磁盘格式: [ { model_id, version_id, model_name, version_name, file_name }, ... ]
_CACHE_FILE = os.path.join(_BACKEND_DIR, 'cache', 'renamer_api_cache.json')


def _load_api_cache():
    """加载为 list，内部用时转成 dict 查找"""
    try:
        with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _save_api_cache(cache_list):
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_list, f, ensure_ascii=False, indent=2)


def _find_in_cache(cache_list, model_id, version_id):
    """在缓存列表中查找匹配项"""
    for item in cache_list:
        if str(item.get('model_id')) == str(model_id):
            if version_id is None and item.get('version_id') is None:
                return item
            if version_id is not None and str(item.get('version_id')) == str(version_id):
                return item
    return None


# ============ Civitai API ============
import requests

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
})


def _fetch_model_info(model_id, version_id=None):
    """
    调用 Civitai API，返回 dict:
      { model_name, version_id, version_name, file_name }
    或 None。
    """
    try:
        url = f"{CIVITAI_API_URL}/{model_id}"
        params = {}
        if CIVITAI_API_TOKEN:
            params['token'] = CIVITAI_API_TOKEN
        resp = _session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        model_name = data.get('name', 'Unknown')

        # 找目标版本
        target = None
        for v in data.get('modelVersions', []):
            if version_id and v.get('id') == version_id:
                target = v
                break
            elif version_id is None:
                target = v
                break

        if not target:
            return None

        files = target.get('files', [])
        file_name = files[0].get('name', '') if files else ''

        # 合并触发词：API trainedWords + 描述 HTML 中提取
        api_words = target.get('trainedWords', [])
        desc_words = model_index.extract_trigger_words_from_html(
            target.get('description', '') or data.get('description', '')
        )
        merged_words = model_index.merge_trigger_words(api_words, desc_words)

        return {
            'model_name': model_name,
            'version_id': target.get('id'),
            'version_name': target.get('name', 'v1'),
            'file_name': file_name,
            'trained_words': merged_words,
        }
    except Exception as e:
        print(f"  [API ERR] {e}")
        return None


def _fetch_cached(model_id, version_id=None):
    """带磁盘缓存的 API 调用"""
    cache_list = _load_api_cache()
    hit = _find_in_cache(cache_list, model_id, version_id)

    if hit is not None:
        print(f"  [CACHE] model={model_id} version={version_id}")
        return {
            'model_name': hit.get('model_name', ''),
            'version_id': hit.get('version_id'),
            'version_name': hit.get('version_name', ''),
            'file_name': hit.get('file_name', ''),
            'trained_words': hit.get('trained_words', []),
        }

    print(f"  [API]   model={model_id} version={version_id}")
    info = _fetch_model_info(model_id, version_id)
    if info:
        cache_list.append({
            'model_id': str(model_id),
            'version_id': info.get('version_id'),
            'model_name': info.get('model_name', ''),
            'version_name': info.get('version_name', ''),
            'file_name': info.get('file_name', ''),
            'trained_words': info.get('trained_words', []),
        })
        _save_api_cache(cache_list)
    time.sleep(0.3)  # rate limit
    return info


# ============ txt 解析 ============
def parse_txt(txt_path):
    """
    从 txt 中提取 Civitai URL → (model_id, version_id)
    返回 (model_id: str, version_id: int|None) 或 (None, None)
    """
    try:
        with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        url_match = re.search(r'civitai\.com/models/(\d+)([^\s]*)', content)
        if not url_match:
            return None, None

        model_id = url_match.group(1)
        rest = url_match.group(2)

        version_match = re.search(r'[?&](?:modelV|v)ersionId=(\d+)', rest, re.IGNORECASE)
        version_id = int(version_match.group(1)) if version_match else None

        return model_id, version_id
    except Exception:
        return None, None


# ============ 文件名清洗 ============
def _clean_filename(name):
    if not name:
        return ''
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip()


# ============ 单文件处理 ============
def process_file(txt_path, dry_run=False):
    """
    处理一个 txt 文件：
    1. 找对应的模型文件
    2. 解析 txt → modelId / versionId
    3. 调 API → 获取 model_name / version_name / file_name
    4. 重命名模型文件和 txt
    5. 写入索引

    返回 True 表示处理成功，False 表示跳过。
    """
    base_name = os.path.splitext(txt_path)[0]

    # 1. 找对应的模型文件
    model_path = None
    for ext in MODEL_EXTENSIONS:
        candidate = base_name + ext
        if os.path.exists(candidate):
            model_path = candidate
            break
    if not model_path:
        return False

    # 2. 解析 txt
    model_id, version_id = parse_txt(txt_path)
    if not model_id:
        print(f"  [SKIP] No URL: {os.path.basename(txt_path)}")
        return False

    # 3. 调 API
    info = _fetch_cached(model_id, version_id)
    if not info:
        print(f"  [SKIP] API failed: model={model_id}")
        return False

    api_model_name = info['model_name']
    api_version_id = info['version_id']
    api_version_name = info['version_name']
    api_file_name = info['file_name']
    api_trained_words = info.get('trained_words', [])

    if not api_file_name:
        print(f"  [SKIP] No file_name: model={model_id}")
        return False

    # 4. 构建新文件名: {title}_{version}_{file.name}.{ext}
    model_ext = os.path.splitext(model_path)[1]
    file_name_clean = api_file_name
    if file_name_clean.lower().endswith(model_ext.lower()):
        file_name_clean = file_name_clean[:-len(model_ext)]

    new_base = _clean_filename(f"{api_model_name}_{api_version_name}_{file_name_clean}")
    new_model_path = os.path.join(os.path.dirname(model_path), f"{new_base}{model_ext}")
    new_txt_path = os.path.join(os.path.dirname(txt_path), f"{new_base}.txt")

    # 跳过已经是目标名称的文件
    if os.path.abspath(model_path) == os.path.abspath(new_model_path):
        # 文件名已标准化，只需确保索引存在
        model_index.upsert(
            model_id=model_id,
            model_name=api_model_name,
            version_id=api_version_id,
            version_name=api_version_name,
            filename=os.path.basename(model_path),
            path=os.path.abspath(model_path),
            trigger_words=api_trained_words,
        )
        print(f"  [IDX]  {os.path.basename(model_path)}")
        return True

    if dry_run:
        print(f"  [DRY]  {os.path.basename(model_path)} → {new_base}{model_ext}")
        return True

    # 5. 执行重命名
    try:
        os.rename(model_path, new_model_path)
        os.rename(txt_path, new_txt_path)
        print(f"  [OK]   {new_base}{model_ext}")
    except Exception as e:
        print(f"  [FAIL] {e}")
        return False

    # 6. 写索引
    model_index.upsert(
        model_id=model_id,
        model_name=api_model_name,
        version_id=api_version_id,
        version_name=api_version_name,
        filename=f"{new_base}{model_ext}",
        path=os.path.abspath(new_model_path),
        trigger_words=api_trained_words,
    )

    return True


# ============ 目录遍历 ============
def process_directory(directory, dry_run=False):
    """处理单个目录下的所有 txt 文件"""
    if not os.path.exists(directory):
        print(f"[WARN] Not found: {directory}")
        return 0

    txt_files = sorted(f for f in os.listdir(directory) if f.lower().endswith('.txt'))
    count = 0
    for txt_file in txt_files:
        if process_file(os.path.join(directory, txt_file), dry_run=dry_run):
            count += 1
    return count


def run_all(dry_run=False):
    """遍历所有仓库目录：ckpt/** 、lora/** 和 embeddings/**"""
    print("=" * 60)
    print("Civitai 模型重命名 & 索引构建")
    print(f"CKPT: {CKPT_BASE_DIR}")
    print(f"LORA: {LORA_BASE_DIR}")
    print(f"EMBEDDING: {EMBEDDING_BASE_DIR}")
    if dry_run:
        print("模式: DRY RUN（仅预览）")
    print("=" * 60)

    total = 0

    for base_dir in [CKPT_BASE_DIR, LORA_BASE_DIR, EMBEDDING_BASE_DIR]:
        if not os.path.exists(base_dir):
            print(f"\n[WARN] 目录不存在: {base_dir}")
            continue
        for sub in sorted(os.listdir(base_dir)):
            subdir = os.path.join(base_dir, sub)
            if not os.path.isdir(subdir):
                continue
            print(f"\n{'='*60}")
            print(f"处理: {subdir}")
            print(f"{'='*60}")
            total += process_directory(subdir, dry_run=dry_run)

    print(f"\n{'='*60}")
    print(f"完成！共处理 {total} 个模型")
    print(f"索引文件: {model_index.INDEX_PATH}")
    print("=" * 60)


# ============ CLI ============
def main():
    dry_run = '--dry' in sys.argv
    run_all(dry_run=dry_run)


if __name__ == '__main__':
    main()
