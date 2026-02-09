#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集中式路径与模型仓库配置
所有模型目录、扫描路径、下载路径均在此定义，其他模块统一引用。
"""

import os

# ============ 模型仓库根目录 ============
CKPT_BASE_DIR = 'D:/ckpt'
LORA_BASE_DIR = 'D:/lora'
EMBEDDING_BASE_DIR = 'D:/embeddings'

# 模型文件扩展名
MODEL_EXTENSIONS = ('.safetensors', '.ckpt', '.pth')
# Embedding 额外支持 .pt 格式
EMBEDDING_EXTENSIONS = ('.safetensors', '.ckpt', '.pth', '.pt')

# ============ ComfyUI ============
COMFYUI_URL = '127.0.0.1:8188'
COMFYUI_PATH = 'C:/ComfyUI_windows_portable/ComfyUI'
COMFYUI_WORKFLOWS_DIR = os.path.join(COMFYUI_PATH, 'user', 'default', 'workflows')

# ============ 后端服务 ============
SERVER_PORT = 53133
WORKFLOW_DIR = os.path.join(os.path.dirname(__file__), 'workflows')
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')

# ============ Civitai API ============
CIVITAI_API_BASE = 'https://civitai.com/api'
CIVITAI_API_URL = f"{CIVITAI_API_BASE}/v1/models"
# Civitai API Token（部分模型需要登录才能下载）
# 获取方式：https://civitai.com/user/account -> API Keys
CIVITAI_API_TOKEN = '9b64ab43d94baf41602ae45fa17accfc'


# ============ 动态路径工具函数 ============

def get_base_dir(main_type):
    """根据主类型返回仓库根目录"""
    if main_type == 'ckpt':
        return CKPT_BASE_DIR
    elif main_type == 'lora':
        return LORA_BASE_DIR
    elif main_type == 'embedding':
        return EMBEDDING_BASE_DIR
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
    if main_type not in ('ckpt', 'lora', 'embedding') or not subtype:
        return None
    # embedding._root → 直接存到 D:/embeddings 根目录
    if main_type == 'embedding' and subtype == '_root':
        return main_type, '', get_base_dir(main_type)
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


def find_model_on_disk(model_name, main_type=None, alt_names=None, version_id=None):
    """
    在仓库目录中搜索模型文件。
    严格匹配：仅通过 modelVersionId 查索引，确保版本精确一致。
    匹配不到则返回 found: False。
    """
    if version_id:
        from util import model_index
        entry = model_index.find_by_version_id(version_id)
        if entry:
            fp = entry['path']
            # 验证文件确实存在于磁盘上
            if not os.path.isfile(fp):
                return {'found': False, 'name': model_name, 'version_id': version_id,
                        'reason': f'索引中有记录但文件不存在: {fp}'}
            # 从路径反推 type / subtype
            for mt in (['ckpt', 'lora', 'embedding'] if not main_type else [main_type]):
                base = get_base_dir(mt)
                if fp.replace('\\', '/').startswith(base.replace('\\', '/')):
                    rel = os.path.relpath(fp, base)
                    sub = rel.split(os.sep)[0] if os.sep in rel else rel.split('/')[0]
                    return {
                        'found': True, 'type': mt, 'subtype': sub,
                        'filename': entry.get('filename', os.path.basename(fp)),
                        'path': fp,
                    }
            # 路径不在已知仓库下，仍然返回 found
            return {
                'found': True, 'type': main_type or 'unknown', 'subtype': '',
                'filename': entry.get('filename', os.path.basename(fp)),
                'path': fp,
            }

    return {'found': False, 'name': model_name, 'version_id': version_id,
            'reason': '索引中无此 versionId 记录，请先下载模型'}


def find_embedding_on_disk(emb_name):
    """
    在 D:/embeddings 中按文件名搜索 embedding。
    支持 .pt / .safetensors / .ckpt / .pth 格式。
    搜索策略（优先级从高到低）：
      1) 精确匹配：文件名(去扩展) == 搜索名
      2) 模糊匹配：文件名以搜索名开头（处理 badhandv4(badhandv4).pt 匹配 badhandv4）
    """
    base = EMBEDDING_BASE_DIR
    if not os.path.exists(base):
        return {'found': False, 'name': emb_name, 'reason': f'目录不存在: {base}'}

    search_name = emb_name.lower().strip()
    # 去掉可能的扩展名
    for ext in EMBEDDING_EXTENSIONS:
        if search_name.endswith(ext):
            search_name = search_name[:-len(ext)]
            break

    def _make_result(fp):
        rel = os.path.relpath(fp, base)
        sub = rel.split(os.sep)[0] if os.sep in rel else ''
        return {'found': True, 'type': 'embedding', 'subtype': sub,
                'filename': os.path.basename(fp), 'path': fp}

    # 收集所有 embedding 文件
    fuzzy_match = None
    for root, dirs, files in os.walk(base):
        for f in files:
            if not f.lower().endswith(EMBEDDING_EXTENSIONS):
                continue
            fname_no_ext = f.lower()
            for ext in EMBEDDING_EXTENSIONS:
                if fname_no_ext.endswith(ext):
                    fname_no_ext = fname_no_ext[:-len(ext)]
                    break
            fp = os.path.join(root, f)
            # 精确匹配
            if fname_no_ext == search_name:
                return _make_result(fp)
            # 模糊匹配：文件名以搜索名开头（如 badhandv4(xxx) 匹配 badhandv4）
            if fuzzy_match is None and fname_no_ext.startswith(search_name):
                fuzzy_match = fp

    if fuzzy_match:
        return _make_result(fuzzy_match)

    return {'found': False, 'name': emb_name,
            'reason': f'D:/embeddings 中未找到 {emb_name}'}


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
