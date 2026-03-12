#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型索引管理
索引文件: backend/cache/model_index.json

磁盘格式（标准 JSON 数组）:
[
  {
    "model_id": "1162518",
    "name": "Plant Milk 🌿 - Model Suite",
    "versions": [
      {
        "version_id": "1714002",
        "version_name": "Walnut",
        "filename": "xxx.safetensors",
        "path": "D:/lora/xl/xxx.safetensors"
      }
    ]
  }
]

内存中构建两份 dict 索引：
  _by_model:   { model_id -> { name, versions: { version_id -> entry } } }
  _by_version: { version_id -> { model_id, name, version_name, filename, path } }
"""

import os
import json
import re
import threading

INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cache', 'model_index.json')

_lock = threading.Lock()
_by_model = {}       # model_id -> { name, versions: { version_id -> entry } }
_by_version = {}     # version_id -> { model_id, name, version_name, filename, path }
_loaded = False


# ============ 序列化 ============

def _to_list():
    """内存 dict → 磁盘数组"""
    result = []
    for model_id, mdata in _by_model.items():
        versions = []
        for vid, entry in mdata.get('versions', {}).items():
            versions.append({
                'version_id': str(vid),
                **entry,
            })
        result.append({
            'model_id': str(model_id),
            'name': mdata.get('name', ''),
            'versions': versions,
        })
    return result


def _from_list(data):
    """磁盘数组 → 内存 dict"""
    global _by_model
    _by_model = {}
    if not isinstance(data, list):
        return
    for item in data:
        mid = str(item.get('model_id', ''))
        if not mid:
            continue
        versions = {}
        for v in item.get('versions', []):
            vid = str(v.get('version_id', ''))
            if not vid:
                continue
            versions[vid] = {
                'version_name': v.get('version_name', ''),
                'filename': v.get('filename', ''),
                'path': v.get('path', ''),
                'trigger_words': v.get('trigger_words', []),
            }
        _by_model[mid] = {
            'name': item.get('name', ''),
            'versions': versions,
        }


def _rebuild_by_version():
    """从 _by_model 重建 _by_version 平铺 dict"""
    global _by_version
    flat = {}
    for model_id, mdata in _by_model.items():
        for version_id, entry in mdata.get('versions', {}).items():
            flat[str(version_id)] = {
                'model_id': str(model_id),
                'name': mdata.get('name', ''),
                **entry,
            }
    _by_version = flat


# ============ 读写 ============

def load():
    """从磁盘加载索引到内存"""
    global _loaded
    with _lock:
        if os.path.exists(INDEX_PATH):
            try:
                with open(INDEX_PATH, 'r', encoding='utf-8') as f:
                    _from_list(json.load(f))
            except (json.JSONDecodeError, IOError):
                _by_model.clear()
        else:
            _by_model.clear()
        _rebuild_by_version()
        _loaded = True


def _ensure_loaded():
    if not _loaded:
        load()


def save():
    """将内存索引写回磁盘"""
    with _lock:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, 'w', encoding='utf-8') as f:
            json.dump(_to_list(), f, ensure_ascii=False, indent=2)


# ============ 增删改查 ============

def upsert(model_id, model_name, version_id, version_name, filename, path, trigger_words=None):
    """插入或更新一条索引记录，同时写回磁盘。"""
    _ensure_loaded()
    model_id = str(model_id)
    version_id = str(version_id)

    with _lock:
        if model_id not in _by_model:
            _by_model[model_id] = {'name': model_name, 'versions': {}}
        elif model_name:
            _by_model[model_id]['name'] = model_name

        existing = _by_model[model_id]['versions'].get(version_id, {})
        tw = trigger_words if trigger_words is not None else existing.get('trigger_words', [])
        _by_model[model_id]['versions'][version_id] = {
            'version_name': version_name or '',
            'filename': filename or '',
            'path': path or '',
            'trigger_words': tw,
        }
        _rebuild_by_version()

    save()


def find_by_version_id(version_id):
    """
    按 modelVersionId 精确查找。
    返回 { model_id, name, version_name, filename, path } 或 None。
    """
    _ensure_loaded()
    entry = _by_version.get(str(version_id))
    if entry and entry.get('path') and os.path.exists(entry['path']):
        return entry
    return None


def find_by_model_id(model_id):
    """
    按 modelId 查找所有本地版本。
    返回 { name, versions: { version_id -> entry } } 或 None。
    """
    _ensure_loaded()
    return _by_model.get(str(model_id))


def get_all():
    """返回完整索引（磁盘数组格式）"""
    _ensure_loaded()
    return _to_list()


# ============ 触发词工具 ============

def extract_trigger_words_from_html(html_text):
    """
    从 Civitai 模型描述 HTML 中提取触发词。
    常见模式：
      - Trigger word(s): xxx, yyy
      - Activation tag(s): xxx
      - Use (the) tag: xxx
      - <code>xxx</code> 紧跟在 trigger 相关文字后
    """
    if not html_text:
        return []
    text = re.sub(r'<[^>]+>', ' ', html_text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')

    results = set()

    patterns = [
        r'(?:trigger|activation)\s*(?:word|tag)s?\s*[:：]\s*([^\n.;]{2,80})',
        r'use\s+(?:the\s+)?tags?\s*[:：]\s*([^\n.;]{2,80})',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = m.group(1).strip()
            parts = re.split(r'[,|、]+', raw)
            for p in parts:
                w = p.strip().strip('"\'` ')
                if w and 2 <= len(w) <= 60:
                    results.add(w)

    for m in re.finditer(r'<code>([^<]{2,60})</code>', html_text, re.IGNORECASE):
        w = m.group(1).strip()
        if w:
            results.add(w)

    return list(results)


def merge_trigger_words(*sources):
    """合并多个触发词列表，去重（大小写不敏感），保留原始大小写。"""
    seen = {}
    for src in sources:
        if not src:
            continue
        for w in src:
            key = w.strip().lower()
            if key and key not in seen:
                seen[key] = w.strip()
    return list(seen.values())


def _norm_path(p):
    if not p:
        return ''
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))
    except Exception:
        return os.path.normcase(os.path.normpath(str(p)))


def remove_by_path(path):
    """按文件路径删除索引中的版本记录（路径大小写/分隔符不敏感）。"""
    _ensure_loaded()
    target = _norm_path(path)
    if not target:
        return {'removed': 0, 'entries': []}

    removed_entries = []
    with _lock:
        for model_id in list(_by_model.keys()):
            mdata = _by_model.get(model_id) or {}
            versions = mdata.get('versions', {})
            for version_id in list(versions.keys()):
                entry = versions.get(version_id) or {}
                entry_path = _norm_path(entry.get('path', ''))
                if entry_path and entry_path == target:
                    removed_entries.append({
                        'model_id': str(model_id),
                        'version_id': str(version_id),
                        'filename': entry.get('filename', ''),
                        'path': entry.get('path', ''),
                    })
                    del versions[version_id]

            if not versions:
                del _by_model[model_id]

        if removed_entries:
            _rebuild_by_version()

    if removed_entries:
        save()
    return {'removed': len(removed_entries), 'entries': removed_entries}


def remove_version(model_id, version_id):
    """删除一条版本记录"""
    _ensure_loaded()
    model_id = str(model_id)
    version_id = str(version_id)

    with _lock:
        mdata = _by_model.get(model_id)
        if mdata and version_id in mdata.get('versions', {}):
            del mdata['versions'][version_id]
            if not mdata['versions']:
                del _by_model[model_id]
            _rebuild_by_version()

    save()
