#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集中式路径与模型仓库配置
所有模型目录、扫描路径、下载路径均在此定义，其他模块统一引用。
"""

import os

# ============ 模型仓库根目录 ============
# Checkpoint 和 LoRA 的根目录（D 盘）
CKPT_BASE_DIR = "D:/ckpt"
LORA_BASE_DIR = "D:/lora"

# 模型文件扩展名
MODEL_EXTENSIONS = ('.safetensors', '.ckpt', '.pth')

# ============ ComfyUI ============
COMFYUI_URL = "127.0.0.1:8188"
COMFYUI_PATH = os.environ.get('COMFYUI_PATH', 'C:/ComfyUI_windows_portable/ComfyUI')
COMFYUI_WORKFLOWS_DIR = os.path.join(COMFYUI_PATH, 'user', 'default', 'workflows')

# ============ 后端服务 ============
SERVER_PORT = 53133
WORKFLOW_DIR = os.path.join(os.path.dirname(__file__), 'workflows')
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')

# ============ Civitai API ============
CIVITAI_API_URL = "https://civitai.com/api/v1/models"
# Civitai API Token（部分模型需要登录才能下载）
# 获取方式：https://civitai.com/user/account -> API Keys
# 可通过环境变量 CIVITAI_API_TOKEN 设置，或直接填写在此处
CIVITAI_API_TOKEN = os.environ.get('CIVITAI_API_TOKEN', '9b64ab43d94baf41602ae45fa17accfc')


# ============ 动态路径工具函数 ============

def get_base_dir(main_type):
    """根据主类型返回仓库根目录"""
    if main_type == 'ckpt':
        return CKPT_BASE_DIR
    elif main_type == 'lora':
        return LORA_BASE_DIR
    else:
        raise ValueError(f"Unknown main_type: {main_type}")


def get_subtype_dir(main_type, subtype):
    """根据主类型+子类型返回完整子目录路径"""
    return os.path.join(get_base_dir(main_type), subtype)


def resolve_type_subtype(type_subtype):
    """
    解析 'ckpt.xl' / 'lora.xl-style' 格式为 (main_type, subtype, full_path)
    返回: (main_type, subtype, target_dir) 或 None
    """
    parts = type_subtype.split('.', 1)
    if len(parts) != 2:
        return None
    main_type, subtype = parts
    if main_type not in ('ckpt', 'lora') or not subtype:
        return None
    target_dir = get_subtype_dir(main_type, subtype)
    return main_type, subtype, target_dir


def scan_subtypes(main_type):
    """扫描仓库根目录下的子文件夹，返回 [{key, count}]"""
    base_dir = get_base_dir(main_type)
    if not os.path.exists(base_dir):
        return []
    subtypes = []
    for name in sorted(os.listdir(base_dir)):
        full_path = os.path.join(base_dir, name)
        if os.path.isdir(full_path):
            count = sum(1 for f in os.listdir(full_path)
                        if f.lower().endswith(MODEL_EXTENSIONS))
            subtypes.append({'key': name, 'count': count})
    return subtypes


def scan_files(main_type, subtype):
    """扫描指定子文件夹下的模型文件，返回 [{filename, size}]"""
    folder = get_subtype_dir(main_type, subtype)
    if not os.path.exists(folder):
        return []
    files = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(MODEL_EXTENSIONS):
            full = os.path.join(folder, f)
            files.append({'filename': f, 'size': os.path.getsize(full)})
    return files


def find_model_on_disk(model_name, main_type=None, alt_names=None):
    """
    在仓库目录中搜索模型文件，支持多个搜索名。
    匹配优先级：
      1) 精确文件名匹配（忽略大小写）
      2) alt_names 中的 file_name 精确匹配
      3) 清理后的关键词单向 contains（关键词 ⊂ 文件名，且关键词 >= 8 字符）
    """
    import re as _re

    names = [model_name]
    if alt_names:
        names.extend(alt_names if isinstance(alt_names, list) else [alt_names])
    # 去空
    names = [n for n in names if n]

    if not names:
        return {'found': False, 'name': model_name}

    # 收集所有候选文件
    search_types = [main_type] if main_type else ['ckpt', 'lora']
    all_files = []  # [(mt, sub, filename, filepath), ...]
    for mt in search_types:
        base_dir = get_base_dir(mt)
        if not os.path.exists(base_dir):
            continue
        for sub in os.listdir(base_dir):
            subdir = os.path.join(base_dir, sub)
            if not os.path.isdir(subdir):
                continue
            for f in os.listdir(subdir):
                if f.lower().endswith(MODEL_EXTENSIONS):
                    all_files.append((mt, sub, f, os.path.join(subdir, f)))

    def _make_result(mt, sub, f, fp):
        return {'found': True, 'type': mt, 'subtype': sub, 'filename': f, 'path': fp}

    # Round 1: 精确文件名匹配（name 可能自带扩展名，也可能不带）
    for n in names:
        n_lower = n.strip().lower()
        for mt, sub, f, fp in all_files:
            if f.lower() == n_lower or os.path.splitext(f)[0].lower() == os.path.splitext(n_lower)[0]:
                return _make_result(mt, sub, f, fp)

    # Round 2: file_name 精确匹配（alt_names 里通常有 Civitai 返回的原始文件名）
    for n in names[1:]:  # 跳过 model_name（已在 Round 1 试过）
        n_stem = os.path.splitext(n.strip())[0].lower()
        if len(n_stem) < 4:
            continue
        for mt, sub, f, fp in all_files:
            f_stem = os.path.splitext(f)[0].lower()
            if n_stem == f_stem:
                return _make_result(mt, sub, f, fp)

    # Round 3: 清理后关键词 单向 contains（关键词 ⊂ 文件名），要求长度 >= 8
    keywords = []
    for n in names:
        stem = os.path.splitext(n)[0] if '.' in n else n
        clean = _re.sub(r'[^a-z0-9]', '', stem.lower())
        if clean and len(clean) >= 8 and clean not in keywords:
            keywords.append(clean)

    if keywords:
        for mt, sub, f, fp in all_files:
            clean_f = _re.sub(r'[^a-z0-9]', '', os.path.splitext(f)[0].lower())
            for kw in keywords:
                if kw in clean_f:
                    return _make_result(mt, sub, f, fp)

    return {'found': False, 'name': model_name}


def get_all_model_paths():
    """
    动态扫描仓库，返回 cache_manager 兼容的 MODEL_PATHS 格式：
    {'checkpoints': [path1, ...], 'loras': [path1, ...]}
    """
    result = {'checkpoints': [], 'loras': []}
    for main_type, key in [('ckpt', 'checkpoints'), ('lora', 'loras')]:
        base_dir = get_base_dir(main_type)
        if not os.path.exists(base_dir):
            continue
        for name in sorted(os.listdir(base_dir)):
            full = os.path.join(base_dir, name)
            if os.path.isdir(full):
                result[key].append(full)
    return result
