#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Downloader Backend + ComfyUI 集成
支持模型下载和 AI 图像生成

Usage:
    python server.py
    启动后访问: http://localhost:53133/api/health
"""

import os
import sys
import json
import time
import uuid
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from typing import Dict, Optional
import re
import urllib.request
import websocket

# 添加 backend 目录到路径
sys.path.insert(0, os.path.dirname(__file__))

from config import (
    SERVER_PORT, WORKFLOW_DIR, COMFYUI_URL, OUTPUT_DIR, CIVITAI_API_BASE,
    scan_subtypes, scan_files, find_model_on_disk, find_embedding_on_disk,
)
from cache_manager import model_cache
from civitaidl import CivitaiDownloader
from azure_blob import BlobStorage

# ============== 下载队列管理（同一时间只执行一个任务） ==============
import queue as _queue_mod

_download_tasks = {}  # task_id -> {status, phase, downloaded, total_size, percent, done, ...}
_tasks_lock = threading.Lock()
_download_queue = _queue_mod.Queue()

# ============== 生成任务跟踪 ==============
_gen_tasks = {}  # prompt_id -> {status, prompt_id, images_count, saved_paths, message, ...}
_gen_lock = threading.Lock()

# ============== 美学分析任务跟踪 ==============
_aesthetic_tasks = {}  # task_id -> {status, image_url, result, message, ...}
_aesthetic_lock = threading.Lock()
_AESTHETIC_CACHE_BLOB_PREFIX = 'data/aesthetic'  # Azure Blob 前缀

def _aesthetic_cache_key(image_url: str) -> str:
    """从图片 URL 生成文件系统安全的缓存 key"""
    import hashlib
    # 用 URL 的 md5 前 16 位 + 文件名片段
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:16]
    # 提取文件名部分（去掉查询参数）
    base = image_url.split('?')[0].split('/')[-1].replace('.', '_')[:30]
    return f"{base}_{url_hash}"


def _restart_comfyui(wait_ready=True, ready_timeout=120):
    """重启 ComfyUI：杀掉进程，watchdog 会自动重启。
    wait_ready=True 时会阻塞直到 ComfyUI 重新就绪（模型加载完成）。
    """
    import subprocess as _subprocess
    _comfy_port = COMFYUI_URL.rsplit(':', 1)[-1].split('/')[0]
    _cmd = (f"Get-NetTCPConnection -LocalPort {_comfy_port} -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty OwningProcess -Unique | "
            "Where-Object { $_ -gt 0 } | "
            "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }")
    _subprocess.run(
        ['powershell', '-Command', _cmd],
        capture_output=True, text=True, timeout=10
    )
    print(f"[ComfyUI] 已终止 ComfyUI 进程，等待 watchdog 重启...")

    if not wait_ready:
        return

    # 等待 ComfyUI 完全就绪（能响应 /api/object_info 说明模型已加载）
    _start = time.time()
    _ready = False
    time.sleep(5)  # 先等 watchdog 拉起进程
    while time.time() - _start < ready_timeout:
        try:
            req = urllib.request.Request(f'http://{COMFYUI_URL}/api/object_info/CheckpointLoaderSimple')
            with _local_opener.open(req, timeout=5) as r:
                data = json.loads(r.read())
                # 如果能获取到 CheckpointLoaderSimple 节点信息，说明模型列表已加载
                if data and 'CheckpointLoaderSimple' in data:
                    _ready = True
                    break
        except Exception:
            pass
        time.sleep(3)

    elapsed = round(time.time() - _start)
    if _ready:
        print(f"[ComfyUI] 重启完成，模型已加载 ({elapsed}s)")
    else:
        print(f"[ComfyUI] ⚠️ 等待 {elapsed}s 后仍未就绪，继续执行...")


def _download_worker():
    """队列消费者：逐个执行下载任务"""
    while True:
        task_id, model_info, target_dir = _download_queue.get()
        try:
            _run_single_download(task_id, model_info, target_dir)
        except Exception:
            pass
        finally:
            _download_queue.task_done()


def _run_single_download(task_id, model_info, target_dir):
    """执行单个下载任务（预检已完成，只做纯下载）"""
    def progress_cb(downloaded, total):
        with _tasks_lock:
            task = _download_tasks.get(task_id)
            if task:
                task['downloaded'] = downloaded
                task['total_size'] = total
                task['percent'] = round(downloaded / total * 100, 1) if total > 0 else 0
                task['phase'] = '下载中'

    with _tasks_lock:
        _download_tasks[task_id]['phase'] = '下载中'
        _download_tasks[task_id]['status'] = 'downloading'

    try:
        downloader = CivitaiDownloader()
        result = downloader.execute_download(
            model_info=model_info,
            target_dir=target_dir,
            progress_callback=progress_cb
        )

        with _tasks_lock:
            task = _download_tasks[task_id]
            task.update(result)
            task['phase'] = '完成' if result.get('status') != 'error' else '失败'
            task['done'] = True
            task['_finish_time'] = time.time()

        # 下载成功后自动刷新模型缓存 + 重启 ComfyUI（使其加载新模型）
        if result.get('status') == 'ok':
            try:
                model_cache.refresh_all()
            except Exception:
                pass
            try:
                _restart_comfyui()
            except Exception as _e:
                print(f"[Download] ComfyUI 重启失败（不影响下载）: {_e}")

    except Exception as e:
        with _tasks_lock:
            task = _download_tasks[task_id]
            task['status'] = 'error'
            task['message'] = str(e)
            task['phase'] = '失败'
            task['done'] = True
            task['_finish_time'] = time.time()


# 启动单个消费者线程
_worker_thread = threading.Thread(target=_download_worker, daemon=True)
_worker_thread.start()


def _resolve_model_version(sess, version_id, token):
    """通过 modelVersionId 查询模型名称"""
    try:
        params = {}
        if token:
            params['token'] = token
        resp = sess.get(f'{CIVITAI_API_BASE}/v1/model-versions/{version_id}',
                        params=params, timeout=15,
                        headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        data = resp.json()
        return {
            'name': data.get('model', {}).get('name', ''),
            'version_name': data.get('name', ''),
            'file_name': data.get('files', [{}])[0].get('name', '') if data.get('files') else '',
            'modelId': data.get('modelId') or data.get('model', {}).get('id'),
            'trainedWords': data.get('trainedWords', []),
        }
    except Exception:
        return {'name': f'version_{version_id}', 'version_name': '', 'file_name': '', 'modelId': None, 'trainedWords': []}


def _resolve_model_by_hash(sess, hash_str, token):
    """通过文件 hash 查询模型信息"""
    try:
        url = f'{CIVITAI_API_BASE}/v1/model-versions/by-hash/{hash_str}'
        params = {}
        if token:
            params['token'] = token
        resp = sess.get(url, params=params, timeout=15,
                        headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        data = resp.json()
        return {
            'name': data.get('model', {}).get('name', ''),
            'version_name': data.get('name', ''),
            'file_name': data.get('files', [{}])[0].get('name', '') if data.get('files') else '',
            'modelId': data.get('modelId') or data.get('model', {}).get('id'),
            'modelVersionId': data.get('id'),
            'trainedWords': data.get('trainedWords', []),
        }
    except Exception:
        return None


def parse_civitai_image(image_url):
    """Parse Civitai image URL to extract generation parameters and check D: drive.
    Returns param_sources dict marking each parameter as original/approximate/default/missing.
    """
    import requests as _requests
    import json as _json
    from config import CIVITAI_API_TOKEN

    match = re.search(r'civitai\.com/images/(\d+)', image_url)
    if not match:
        return {'status': 'error', 'message': '无法解析图片URL，请输入 civitai.com/images/xxxxx 格式'}

    image_id = int(match.group(1))
    sess = _requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # -------- 参数溯源追踪 --------
    # original=API精确获取  approximate=获取但可能不精确  default=无数据用默认  missing=关键缺失
    ps = {}  # param_sources

    def _src(key, value, raw_value, default_value=None):
        """记录参数来源：有原始值→original，否则→default"""
        if raw_value is not None:
            ps[key] = 'original'
        elif default_value is not None:
            ps[key] = 'default'
        else:
            ps[key] = 'missing'
        return value

    # -------- 获取生成参数 --------
    try:
        params = {'input': _json.dumps({'json': {'id': image_id}})}
        if CIVITAI_API_TOKEN:
            params['token'] = CIVITAI_API_TOKEN
        resp = sess.get(f'{CIVITAI_API_BASE}/trpc/image.getGenerationData',
                        params=params, timeout=30, headers=headers)
        resp.raise_for_status()
        gen_data = resp.json().get('result', {}).get('data', {}).get('json', {})
    except Exception as e:
        return {'status': 'error', 'message': f'Civitai API 请求失败: {e}'}

    meta = gen_data.get('meta') or {}  # 可能返回 None，统一为空 dict
    _is_partial = not meta  # meta 为空时标记为 partial 模式

    # -------- 基本参数（逐个追踪来源） --------
    result = {'status': 'partial' if _is_partial else 'success'}
    if _is_partial:
        print(f'[Parse] 图片 {image_id} 无 meta，进入 partial 模式（仅提取 resources）')

    result['prompt'] = _src('prompt', meta.get('prompt', ''), meta.get('prompt'))
    result['negative_prompt'] = _src('negative_prompt',
                                     meta.get('negativePrompt', ''), meta.get('negativePrompt'))
    result['sampler'] = _src('sampler', meta.get('sampler', 'dpmpp_2m'),
                             meta.get('sampler'), 'dpmpp_2m')
    result['scheduler'] = _src('scheduler', meta.get('scheduler', ''),
                               meta.get('scheduler'), '')
    result['steps'] = _src('steps', meta.get('steps', 20), meta.get('steps'), 20)
    result['cfg'] = _src('cfg', meta.get('cfgScale', 7), meta.get('cfgScale'), 7)
    # base_model: 优先 meta，fallback gen_data.resources
    _base_model = meta.get('baseModel', '')
    if not _base_model:
        for _res in gen_data.get('resources', []):
            _bm = _res.get('baseModel', '')
            if _bm and _bm.lower() != 'other':
                _base_model = _bm
                break
    result['base_model'] = _base_model

    # clip_skip: 有原始值则用原始值；否则二次元系模型默认 -2
    raw_clip = meta.get('clipSkip')
    if raw_clip is not None:
        result['clip_skip'] = raw_clip
        ps['clip_skip'] = 'original'
    else:
        _anime_bases = ('illustrious', 'pony', 'animagine', 'nai', 'novelai', 'anime')
        base_lower = _base_model.lower()
        if any(b in base_lower for b in _anime_bases):
            result['clip_skip'] = -2
            ps['clip_skip'] = 'default'
        else:
            result['clip_skip'] = None
            ps['clip_skip'] = 'missing'

    # Seed: -1 或 None 视为缺失
    raw_seed = meta.get('seed')
    seed_val = raw_seed if (raw_seed is not None and int(raw_seed) >= 0) else -1
    if seed_val >= 0:
        ps['seed'] = 'original'
    else:
        ps['seed'] = 'missing'
    result['seed'] = seed_val

    # -------- 尺寸（多级 fallback + 来源追踪） --------
    has_upscaler = False  # 稍后检测

    # 来源 A: meta 字段中的 Hires Fix 标记（SD WebUI / Forge 等常见格式）
    _hires_upscaler = meta.get('Hires upscaler') or meta.get('hpiUpscaler') or ''
    _hires_upscale = meta.get('Hires upscale') or meta.get('Hires resize') or ''
    if _hires_upscaler or _hires_upscale:
        has_upscaler = True
        print(f'[Parse] meta Hires 检测: upscaler={_hires_upscaler}, scale={_hires_upscale}')

    # 优先级 1: meta.width / meta.height
    raw_w, raw_h = meta.get('width'), meta.get('height')
    # 优先级 2: meta.Size 字段（旧格式 "1024x1024"）
    if not raw_w:
        size_str = str(meta.get('Size', ''))
        if 'x' in size_str:
            parts = size_str.split('x')
            try:
                raw_w, raw_h = int(parts[0]), int(parts[1])
            except:
                pass

    if raw_w and raw_h:
        result['width'] = int(raw_w)
        result['height'] = int(raw_h)
        ps['size'] = 'original'  # 暂定，后续检测 upscaler 可能改为 approximate
    else:
        # 优先级 3: image.get 接口（图片实际尺寸，可能含 upscale）
        result['width'] = 1024
        result['height'] = 1024
        ps['size'] = 'default'
        try:
            img_params = {'input': _json.dumps({'json': {'id': image_id}})}
            img_resp = sess.get(f'{CIVITAI_API_BASE}/trpc/image.get',
                                params=img_params, timeout=15, headers=headers)
            img_resp.raise_for_status()
            img_data = img_resp.json().get('result', {}).get('data', {}).get('json', {})
            img_w = img_data.get('width') or (img_data.get('metadata') or {}).get('width')
            img_h = img_data.get('height') or (img_data.get('metadata') or {}).get('height')
            if img_w and img_h:
                result['width'] = int(img_w)
                result['height'] = int(img_h)
                ps['size'] = 'approximate'  # 来自图片实际尺寸，非生成参数
                print(f"[Parse] 从 image.get 获取尺寸: {img_w}×{img_h}")
        except Exception as e:
            print(f"[Parse] image.get 获取尺寸失败: {e}")

    # -------- Embedding 检测（prompt 正则 + Civitai resources 合并） --------
    embeddings = []  # [{'name': str, 'modelVersionId': int|None, 'modelId': int|None, 'source': str}]
    _emb_seen = set()  # 去重

    # 1) 从 prompt 文本正则提取
    prompt_text = result.get('prompt', '') + ' ' + result.get('negative_prompt', '')
    for emb_match in re.finditer(r'(?:embedding:|embed:)([^\s,<>]+)', prompt_text):
        name = emb_match.group(1)
        key = name.lower()
        if key not in _emb_seen:
            _emb_seen.add(key)
            embeddings.append({'name': name, 'modelVersionId': None, 'modelId': None, 'source': 'prompt'})

    # 2) 从 Civitai resources 提取 TextualInversion（如果有的话）
    for _src_list in [meta.get('civitaiResources', []), gen_data.get('resources', [])]:
        for res in _src_list:
            model_type = (res.get('type') or res.get('modelType') or '').lower()
            if model_type in ('textualinversion', 'embedding'):
                vid = res.get('modelVersionId') or res.get('versionId')
                mid = res.get('modelId')
                mname = res.get('modelName', '')
                # 尝试获取精确文件名
                if vid:
                    info = _resolve_model_version(sess, vid, CIVITAI_API_TOKEN)
                    fname = info.get('file_name', '') if info else ''
                else:
                    fname = ''
                display_name = fname or mname
                key = display_name.lower()
                if key and key not in _emb_seen:
                    _emb_seen.add(key)
                    embeddings.append({
                        'name': display_name, 'modelVersionId': vid,
                        'modelId': mid, 'source': 'civitai_resource',
                    })
                # 补全已有条目的 versionId
                elif key in _emb_seen and vid:
                    for emb in embeddings:
                        if emb['name'].lower() == key and not emb.get('modelVersionId'):
                            emb['modelVersionId'] = vid
                            emb['modelId'] = mid

    result['embeddings'] = embeddings

    # -------- 模型资源解析（checkpoint / lora / upscaler） --------
    loras = []
    checkpoint = None
    checkpoint_alt = []
    checkpoint_version_id = None
    checkpoint_model_id = None
    _all_trigger_words = []  # 收集所有模型的 trainedWords，用于空提示词反推

    # --- 来源 1: meta.civitaiResources ---
    civitai_resources = meta.get('civitaiResources', [])
    for res in civitai_resources:
        rtype = res.get('type', '')
        version_id = res.get('modelVersionId')
        if not version_id:
            continue
        info = _resolve_model_version(sess, version_id, CIVITAI_API_TOKEN)
        _all_trigger_words.extend(info.get('trainedWords', []))
        if rtype == 'upscaler':
            has_upscaler = True
            continue
        elif rtype == 'checkpoint':
            checkpoint = info.get('name', '')
            checkpoint_alt = [info.get('file_name', ''), info.get('version_name', '')]
            checkpoint_version_id = version_id
            checkpoint_model_id = info.get('modelId')
        elif rtype == 'lora':
            _raw_w = res.get('weight')
            loras.append({
                'name': info.get('name', ''), 'weight': _raw_w if _raw_w is not None else 1.0,
                'alt_names': [info.get('file_name', ''), info.get('version_name', '')],
                'modelVersionId': version_id,
                'modelId': info.get('modelId'),
                '_weight_known': _raw_w is not None,
            })

    # --- 来源 2: gen_data 顶层 resources ---
    if not checkpoint and not loras:
        top_resources = gen_data.get('resources', [])
        for res in top_resources:
            model_type = (res.get('modelType') or '').lower()
            version_id = res.get('modelVersionId') or res.get('versionId')
            model_id = res.get('modelId')
            model_name = res.get('modelName', '')
            version_name = res.get('versionName', '')

            if not version_id:
                continue

            if model_type == 'upscaler':
                has_upscaler = True
                continue

            info = _resolve_model_version(sess, version_id, CIVITAI_API_TOKEN)
            _all_trigger_words.extend(info.get('trainedWords', []))
            file_name = info.get('file_name', '') if info else ''

            if model_type == 'checkpoint':
                checkpoint = model_name
                checkpoint_alt = [file_name, version_name]
                checkpoint_version_id = version_id
                checkpoint_model_id = model_id
            elif model_type == 'lora':
                _raw_s = res.get('strength')
                loras.append({
                    'name': model_name, 'weight': _raw_s if _raw_s is not None else 1.0,
                    'alt_names': [file_name, version_name],
                    'modelVersionId': version_id,
                    'modelId': model_id,
                    '_weight_known': _raw_s is not None,
                })

    # --- 来源 3: 旧格式 meta.resources / Model / hashes ---
    if not checkpoint and not loras:
        old_resources = meta.get('resources', [])
        model_name = meta.get('Model', '')
        model_hash = meta.get('Model hash', '')

        for res in old_resources:
            if res.get('type') in ('model', 'checkpoint'):
                model_name = model_name or res.get('name', '')
                model_hash = model_hash or res.get('hash', '')

        if model_hash:
            info = _resolve_model_by_hash(sess, model_hash, CIVITAI_API_TOKEN)
            if info:
                _all_trigger_words.extend(info.get('trainedWords', []))
                checkpoint = info.get('name', '') or model_name
                checkpoint_alt = [info.get('file_name', ''), info.get('version_name', ''), model_name]
                checkpoint_version_id = info.get('modelVersionId')
                checkpoint_model_id = info.get('modelId')
        if not checkpoint and model_name:
            checkpoint = model_name

        hashes = meta.get('hashes', {})
        for key, h in hashes.items():
            if not key.upper().startswith('LORA:'):
                continue
            lora_filename = key[5:]
            lora_info = _resolve_model_by_hash(sess, h, CIVITAI_API_TOKEN)
            if lora_info:
                _all_trigger_words.extend(lora_info.get('trainedWords', []))
                loras.append({
                    'name': lora_info.get('name', '') or lora_filename,
                    'weight': 0.5,
                    'alt_names': [lora_info.get('file_name', ''), lora_info.get('version_name', ''), lora_filename],
                    'modelVersionId': lora_info.get('modelVersionId'),
                    'modelId': lora_info.get('modelId'),
                    '_weight_known': False,
                })
            else:
                loras.append({
                    'name': lora_filename, 'weight': 0.5,
                    'alt_names': [lora_filename],
                    'modelVersionId': None, 'modelId': None,
                    '_weight_known': False,
                })

    # 模型来源追踪
    ps['checkpoint'] = 'original' if checkpoint else 'missing'
    ps['loras'] = 'original' if loras else ('missing' if not loras and meta.get('hashes') else 'original')

    # LoRA weight 来源追踪
    for lora in loras:
        if lora.get('_weight_known', True):
            lora['weight_source'] = 'original'
        else:
            lora['weight_source'] = 'default'

    # 将 LoRA weights 纳入参数精确度计算
    if loras:
        _any_default = any(l.get('weight_source') == 'default' for l in loras)
        ps['lora_weights'] = 'default' if _any_default else 'original'
    else:
        ps['lora_weights'] = 'original'  # 无 LoRA 时不扣分

    # Embedding 来源追踪
    ps['embeddings'] = 'original' if embeddings else 'original'  # prompt 中有引用就是 original

    # -------- 尺寸 + upscaler 联合判定 --------
    # SDXL 标准直出尺寸
    _XL_SIZES = [
        (1024, 1024), (1152, 896), (896, 1152), (1216, 832), (832, 1216),
        (1344, 768), (768, 1344), (1536, 640), (640, 1536),
    ]

    # 来源 C: 启发式 —— 尺寸明显超过 base model 最大标准尺寸
    if not has_upscaler and ps.get('size') != 'default':
        w, h = result['width'], result['height']
        _max_side = max(w, h)
        # SDXL / Illustrious / Pony 系列最大边 1536；SD1.5 最大 768
        _base_lower = _base_model.lower()
        if any(b in _base_lower for b in ('xl', 'sdxl', 'illustrious', 'pony', 'animagine')):
            _threshold = 1536
        else:
            _threshold = 768
        if _max_side > _threshold * 1.15:  # 允许 15% 容差
            has_upscaler = True
            print(f'[Parse] 启发式 Upscaler 检测: {w}×{h} 超过 {_base_model} 阈值 {_threshold}')

    if has_upscaler and ps['size'] != 'default':
        w, h = result['width'], result['height']
        # 如果当前尺寸已经是标准 XL 尺寸，无需回退
        if (w, h) in _XL_SIZES:
            print(f'[Parse] {w}×{h} 已是标准 XL 尺寸，跳过 Upscaler 回退')
        else:
            # 用比例找最接近的 XL 标准尺寸
            aspect = w / h if h else 1.0
            best, best_dist = _XL_SIZES[0], float('inf')
            for sw, sh in _XL_SIZES:
                sa = sw / sh if sh else 1.0
                dist = abs(aspect - sa)
                if dist < best_dist:
                    best_dist = dist
                    best = (sw, sh)
            result['width'], result['height'] = best
            result['_original_image_size'] = (w, h)
            ps['size'] = 'approximate'
            result['_size_note'] = f'原图 {w}×{h} 使用了 Upscaler，已匹配最近 XL 标准尺寸 {best[0]}×{best[1]}'

    result['checkpoint'] = checkpoint or ''
    result['loras'] = loras
    result['has_upscaler'] = has_upscaler

    # -------- D 盘模型检查 --------
    checks = {'checkpoint': None, 'loras': []}
    if checkpoint:
        checks['checkpoint'] = find_model_on_disk(checkpoint, 'ckpt', alt_names=checkpoint_alt, version_id=checkpoint_version_id, model_id=checkpoint_model_id)
        checks['checkpoint']['modelVersionId'] = checkpoint_version_id
        checks['checkpoint']['modelId'] = checkpoint_model_id
        checks['checkpoint']['versionName'] = checkpoint_alt[1] if len(checkpoint_alt) > 1 else ''
    for lora in loras:
        check = find_model_on_disk(lora['name'], 'lora', alt_names=lora.get('alt_names'), version_id=lora.get('modelVersionId'), model_id=lora.get('modelId'))
        check['weight'] = lora.get('weight', 1.0)
        check['weight_source'] = lora.get('weight_source', 'original')
        check['requested_name'] = lora['name']
        check['modelVersionId'] = lora.get('modelVersionId')
        check['modelId'] = lora.get('modelId')
        checks['loras'].append(check)
    # Embedding 磁盘检查
    checks['embeddings'] = []
    for emb in embeddings:
        emb_check = find_embedding_on_disk(emb['name'])
        emb_check['requested_name'] = emb['name']
        emb_check['modelVersionId'] = emb.get('modelVersionId')
        emb_check['modelId'] = emb.get('modelId')
        checks['embeddings'].append(emb_check)
    result['checks'] = checks

    all_found = True
    missing = []
    if checkpoint and checks['checkpoint'] and not checks['checkpoint'].get('found'):
        all_found = False
        missing.append(f"Checkpoint: {checkpoint}")
    for lc in checks['loras']:
        if not lc.get('found'):
            all_found = False
            missing.append(f"LoRA: {lc.get('requested_name', 'unknown')}")
    for ec in checks['embeddings']:
        if not ec.get('found'):
            all_found = False
            missing.append(f"Embedding: {ec.get('requested_name', 'unknown')}")
    result['all_models_found'] = all_found
    result['missing_models'] = missing

    # -------- 空提示词反推（Gemini + trigger words） --------
    result['prompt_reverse_tagged'] = False
    if not result.get('prompt', '').strip():
        # 去重 trigger words
        _tw_dedup = list(dict.fromkeys(w.strip() for w in _all_trigger_words if w.strip()))
        try:
            # 获取图片 URL 用于 Gemini
            _img_params = {'input': _json.dumps({'json': {'id': image_id}})}
            if CIVITAI_API_TOKEN:
                _img_params['token'] = CIVITAI_API_TOKEN
            _img_resp = sess.get(f'{CIVITAI_API_BASE}/trpc/image.get',
                                 params=_img_params, timeout=15, headers=headers)
            _img_data = _img_resp.json().get('result', {}).get('data', {}).get('json', {})
            _raw_img_url = _img_data.get('url', '')
            _CDN = 'https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA'
            if _raw_img_url and not _raw_img_url.startswith('http'):
                _img_url = f'{_CDN}/{_raw_img_url}/width=1024'
            elif _raw_img_url:
                _img_url = re.sub(r'/width=\d+', '/width=1024', _raw_img_url)
            else:
                _img_url = ''

            if _img_url:
                print(f'[Parse] 提示词为空，调用 AI 反推标签...')
                from llm.gemini_client import reverse_tag_image
                _tags, _provider = reverse_tag_image(_img_url)
                # 组合：trigger words 在前，反推标签在后
                if _tw_dedup:
                    result['prompt'] = ', '.join(_tw_dedup) + ', ' + _tags
                else:
                    result['prompt'] = _tags
                result['prompt_reverse_tagged'] = True
                result['reverse_tag_provider'] = _provider
                result['trigger_words'] = _tw_dedup
                ps['prompt'] = 'ai_reverse'
                print(f'[Parse] 反推完成 (via {_provider})，trigger words: {len(_tw_dedup)} 个')
        except Exception as e:
            print(f'[Parse] AI 反推失败: {e}')
            # 至少把 trigger words 放进去
            if _tw_dedup:
                result['prompt'] = ', '.join(_tw_dedup)
                result['prompt_reverse_tagged'] = True
                result['trigger_words'] = _tw_dedup
                ps['prompt'] = 'ai_reverse'

    # -------- 复刻完整度总结 --------
    _track_keys = [k for k in ps if not k.startswith('_')]
    ps['_summary'] = {
        'total': len(_track_keys),
        'original': sum(1 for k in _track_keys if ps[k] == 'original'),
        'approximate': sum(1 for k in _track_keys if ps[k] == 'approximate'),
        'ai_reverse': sum(1 for k in _track_keys if ps[k] == 'ai_reverse'),
        'default': sum(1 for k in _track_keys if ps[k] == 'default'),
        'missing': sum(1 for k in _track_keys if ps[k] == 'missing'),
    }
    result['param_sources'] = ps

    # -------- 控制变量法：生成 variations 列表 --------
    # 基准参数集
    base_vars = {
        'sampler': result['sampler'],
        'scheduler': result.get('scheduler', ''),
        'width': result['width'],
        'height': result['height'],
    }
    variations = [{'label': '基准', 'params': dict(base_vars)}]

    # 采样器/调度器缺失 → euler a + dpmpp_2m karras 各一张
    if ps.get('sampler') in ('default', 'missing'):
        variations = []  # 清掉基准，用两个候选替代
        variations.append({
            'label': '采样器: Euler a',
            'params': {**base_vars, 'sampler': 'Euler a', 'scheduler': 'normal'},
        })
        variations.append({
            'label': '采样器: DPM++ 2M Karras',
            'params': {**base_vars, 'sampler': 'dpmpp_2m', 'scheduler': 'karras'},
        })

    result['variations'] = variations
    result['total_images'] = len(variations)

    return result


# ============== ComfyUI API ==============
# 无代理 opener（ComfyUI 是本地服务，不走系统代理）
_no_proxy_handler = urllib.request.ProxyHandler({})
_local_opener = urllib.request.build_opener(_no_proxy_handler)

def wait_for_comfyui(timeout=5):
    """快速检查 ComfyUI 是否就绪（仅用于API快速失败）"""
    try:
        req = urllib.request.Request(f'http://{COMFYUI_URL}/api/queue')
        with _local_opener.open(req, timeout=2) as r:
            data = json.loads(r.read())
            return True, data
    except Exception:
        return False, None


def get_comfyui_checkpoints():
    """获取 ComfyUI 可用 checkpoints"""
    # 1. 尝试从 ComfyUI API 获取
    try:
        with _local_opener.open(f'http://{COMFYUI_URL}/api/models/checkpoints', timeout=5) as r:
            data = json.loads(r.read())
            if data:
                return data
    except Exception:
        pass
    
    # 2. 后备：扫描本地 checkpoints 目录
    from config import CKPT_BASE_DIR, COMFYUI_PATH
    local_paths = [
        os.path.join(CKPT_BASE_DIR, 'xl'),
        os.path.join(COMFYUI_PATH, 'models', 'checkpoints'),
    ]
    
    checkpoints = []
    for path in local_paths:
        if os.path.exists(path):
            for f in os.listdir(path):
                if f.endswith('.safetensors') or f.endswith('.ckpt'):
                    checkpoints.append(f)
    
    return list(set(checkpoints))  # 去重


def queue_prompt(prompt: Dict, client_id: str) -> Dict:
    """提交提示词到 ComfyUI"""
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode('utf-8')
    req = urllib.request.Request(f"http://{COMFYUI_URL}/prompt", data=data)
    try:
        with _local_opener.open(req) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        try:
            err_data = json.loads(body)
            detail = err_data.get('error', {}).get('message', '') or err_data.get('node_errors', '')
        except:
            detail = body[:500]
        return {'error': f'ComfyUI rejected prompt ({e.code})', 'detail': str(detail)[:500]}
    except Exception as e:
        return {'error': f'ComfyUI connection failed: {e}'}


def wait_for_prompt_ws(ws, prompt_id: str):
    """通过 WebSocket 等待单个任务完成（不接收图片数据）"""
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data.get('prompt_id') == prompt_id and data.get('node') is None:
                    break  # 执行完成


def wait_for_batch_ws(ws, prompt_ids: list, timeout: int = 600):
    """通过 WebSocket 等待多个任务完成（不接收图片数据）"""
    completed = set()
    errors = {}
    start_time = time.time()
    prompt_set = set(prompt_ids)

    while len(completed) + len(errors) < len(prompt_ids):
        if time.time() - start_time > timeout:
            raise TimeoutError(f"等待图片超时 ({timeout}秒)")

        try:
            out = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except websocket.WebSocketConnectionClosedException:
            raise ConnectionError("WebSocket 连接已断开，请重试")

        if isinstance(out, str):
            message = json.loads(out)
            msg_type = message.get('type', '')
            data = message.get('data', {})
            pid = data.get('prompt_id')

            if msg_type == 'executing':
                # node=None 表示该 prompt 的所有节点执行完毕
                if pid in prompt_set and data.get('node') is None:
                    completed.add(pid)

            elif msg_type == 'execution_error':
                # ComfyUI 执行出错（LoRA 找不到、节点报错等）
                if pid in prompt_set:
                    err_msg = data.get('exception_message', data.get('error', '执行错误'))
                    node_type = data.get('node_type', '')
                    errors[pid] = f"{node_type}: {err_msg}" if node_type else err_msg
                    print(f"[ComfyUI] ⚠️ prompt {pid[:8]} 执行错误: {errors[pid]}")

            elif msg_type == 'execution_interrupted':
                # 被中断（ComfyUI 重启/手动取消）
                if pid in prompt_set:
                    errors[pid] = '执行被中断'
                    print(f"[ComfyUI] ⚠️ prompt {pid[:8]} 被中断")

    if errors:
        print(f"[ComfyUI] 批次中 {len(errors)}/{len(prompt_ids)} 个任务失败: {list(errors.values())[:3]}")
    return completed, errors


def fetch_images_from_history(prompt_id: str) -> list:
    """
    通过 ComfyUI history API 获取已完成任务的输出图片。
    返回的 PNG 数据包含完整的 workflow 元数据（可拖入 ComfyUI 加载）。
    """
    # 1. 查询 history
    url = f"http://{COMFYUI_URL}/history/{prompt_id}"
    req = urllib.request.Request(url)
    with _local_opener.open(req) as resp:
        history = json.loads(resp.read())

    if prompt_id not in history:
        return []

    outputs = history[prompt_id].get('outputs', {})
    images = []

    # 2. 遍历所有节点的输出，找到图片
    for node_id, node_output in outputs.items():
        for img_info in node_output.get('images', []):
            filename = img_info.get('filename', '')
            subfolder = img_info.get('subfolder', '')
            img_type = img_info.get('type', 'output')

            # 3. 通过 /view 端点获取带 metadata 的完整 PNG
            params = urllib.parse.urlencode({
                'filename': filename,
                'subfolder': subfolder,
                'type': img_type,
            })
            view_url = f"http://{COMFYUI_URL}/view?{params}"
            view_req = urllib.request.Request(view_url)
            with _local_opener.open(view_req) as img_resp:
                images.append(img_resp.read())

    return images


def fetch_images_batch(prompt_ids: list) -> dict:
    """批量获取多个任务的输出图片（带 workflow 元数据）"""
    result = {}
    for pid in prompt_ids:
        try:
            result[pid] = fetch_images_from_history(pid)
        except Exception as e:
            print(f"[ComfyUI] 获取图片失败 {pid}: {e}")
            result[pid] = []
    return result


# ---- Sampler 名称映射（Civitai/WebUI 显示名 → ComfyUI 内部名） ----
_SAMPLER_MAP = {
    'euler': 'euler', 'euler a': 'euler_ancestral', 'euler ancestral': 'euler_ancestral',
    'heun': 'heun', 'dpm2': 'dpm_2', 'dpm2 a': 'dpm_2_ancestral',
    'dpm++ 2s a': 'dpmpp_2s_ancestral', 'dpm++ sde': 'dpmpp_sde',
    'dpm++ 2m': 'dpmpp_2m', 'dpm++ 2m sde': 'dpmpp_2m_sde',
    'dpm++ 3m sde': 'dpmpp_3m_sde',
    'dpm fast': 'dpm_fast', 'dpm adaptive': 'dpm_adaptive',
    'lms': 'lms', 'lms karras': 'lms',
    'dpm2 karras': 'dpm_2', 'dpm2 a karras': 'dpm_2_ancestral',
    'dpm++ 2s a karras': 'dpmpp_2s_ancestral', 'dpm++ sde karras': 'dpmpp_sde',
    'dpm++ 2m karras': 'dpmpp_2m', 'dpm++ 2m sde karras': 'dpmpp_2m_sde',
    'dpm++ 3m sde karras': 'dpmpp_3m_sde',
    'ddim': 'ddim', 'plms': 'plms', 'uni_pc': 'uni_pc', 'unipc': 'uni_pc',
    'lcm': 'lcm',
    # Civitai/A1111 变体名 → ComfyUI 内部名
    'euler_ancestral': 'euler_ancestral',
    'euler ancestral sgm uniform': 'euler_ancestral',
    'euler_ancestral_sgm_uniform': 'euler_ancestral',
    'euler a sgm uniform': 'euler_ancestral',
    'dpm++ 2m sgm uniform': 'dpmpp_2m',
    'dpm++ 2m sde sgm uniform': 'dpmpp_2m_sde',
    'dpm++ sde sgm uniform': 'dpmpp_sde',
    'restart': 'restart',
    'ipndm': 'ipndm', 'ipndm_v': 'ipndm_v',
    'deis': 'deis',
}

def _normalize_sampler(name: str) -> str:
    """将 Civitai/WebUI 的 sampler 显示名转为 ComfyUI 内部名"""
    if not name:
        return 'euler'
    lower = name.strip().lower()
    if lower in _SAMPLER_MAP:
        return _SAMPLER_MAP[lower]
    # 已经是 ComfyUI 内部名
    return name


# ---- object_info 缓存（用于 widget 名称解析） ----
_object_info_cache = None
_WIDGET_TYPES = {'INT', 'FLOAT', 'STRING', 'BOOLEAN'}


def _get_object_info() -> Dict:
    """从 ComfyUI /object_info 获取所有节点定义（带缓存）"""
    global _object_info_cache
    if _object_info_cache:
        return _object_info_cache
    try:
        url = f"http://{COMFYUI_URL}/object_info"
        req = urllib.request.Request(url)
        resp = _local_opener.open(req, timeout=15)
        _object_info_cache = json.loads(resp.read())
    except Exception as e:
        print(f"[ComfyUI] 获取 object_info 失败: {e}")
        _object_info_cache = {}
    return _object_info_cache


def _extract_widget_names(object_info: Dict, class_type: str) -> list:
    """
    从 object_info 提取某节点类型的 widget 名称（按顺序）。
    返回 [(name, has_control_after_generate), ...]
    has_control_after_generate=True 表示该 widget 后面跟一个 UI 专用的
    control_after_generate 值，需要在 widgets_values 中跳过。
    """
    node_def = object_info.get(class_type, {})
    inp_def = node_def.get('input', {})
    required = inp_def.get('required', {})
    optional = inp_def.get('optional', {})
    input_order = node_def.get('input_order', {})
    req_order = input_order.get('required', list(required.keys()))
    opt_order = input_order.get('optional', list(optional.keys()))

    all_inputs = {}
    all_inputs.update(required)
    all_inputs.update(optional)

    widgets = []
    for name in req_order + opt_order:
        config = all_inputs.get(name)
        if not config:
            continue
        type_info = config[0] if isinstance(config, (list, tuple)) and config else config

        # COMBO（下拉列表）→ widget
        if isinstance(type_info, list):
            widgets.append((name, False))
            continue

        # 基本类型 → widget
        if isinstance(type_info, str) and type_info in _WIDGET_TYPES:
            has_control = False
            if type_info == 'INT' and len(config) > 1 and isinstance(config[1], dict):
                max_val = config[1].get('max')
                if max_val is not None and max_val > 2**53:
                    has_control = True
            widgets.append((name, has_control))
            continue

        # 其他（MODEL, CLIP, CONDITIONING 等）→ 连接类型，跳过

    return widgets


def _convert_ui_to_api(raw: Dict) -> Dict:
    """将 ComfyUI UI 导出的图格式转换为 API 格式"""
    nodes = raw.get('nodes', [])
    links_arr = raw.get('links', [])
    if not nodes:
        raise ValueError('工作流中没有 nodes 数据')

    # 建立 link_id → (from_node_id, from_output_slot) 映射
    link_map = {}
    for link in links_arr:
        link_map[link[0]] = (link[1], link[2])

    # 获取 object_info 用于动态解析 widget 名称
    object_info = _get_object_info()

    api = {}
    for node in nodes:
        nid = str(node['id'])
        class_type = node['type']
        inputs = {}

        # 1) 从 links 建立连接输入
        for inp in node.get('inputs', []):
            link_id = inp.get('link')
            if link_id is not None and link_id in link_map:
                from_node, from_slot = link_map[link_id]
                inputs[inp['name']] = [str(from_node), from_slot]

        # 2) 用 object_info 映射 widgets_values → 命名参数
        wv = node.get('widgets_values')
        if wv and isinstance(wv, list):
            widget_names = _extract_widget_names(object_info, class_type)
            if widget_names:
                wv_idx = 0
                for wname, has_control in widget_names:
                    if wv_idx >= len(wv):
                        break
                    if wname not in inputs:
                        inputs[wname] = wv[wv_idx]
                    wv_idx += 1
                    if has_control and wv_idx < len(wv):
                        wv_idx += 1  # 跳过 control_after_generate

        api[nid] = {'class_type': class_type, 'inputs': inputs}

    return api


def _find_nodes_by_type(workflow: Dict, class_type: str) -> list:
    """按 class_type 查找所有匹配节点，返回 [(nid, node), ...]，按节点 ID 升序排列"""
    results = []
    for nid, node in workflow.items():
        if isinstance(node, dict) and node.get('class_type') == class_type:
            results.append((nid, node))
    results.sort(key=lambda x: int(x[0]) if x[0].isdigit() else float('inf'))
    return results


def _resolve_comfyui_lora_name(name: str) -> str:
    """
    将我们的 LoRA 名称与 ComfyUI 实际可用列表做匹配。
    ComfyUI 验证时要求名称精确匹配（含大小写），这里做模糊匹配。
    """
    try:
        with _local_opener.open(f'http://{COMFYUI_URL}/api/models/loras', timeout=5) as r:
            comfyui_loras = json.loads(r.read())
    except Exception:
        return name  # 查询失败，原样返回

    # 统一分隔符后做比较
    name_norm = name.replace('\\', '/').lower()

    # 1) 精确匹配（统一分隔符后）
    for cl in comfyui_loras:
        if cl.replace('\\', '/').lower() == name_norm:
            return cl

    # 2) 只比较文件名部分（忽略子目录）
    name_file = name_norm.rsplit('/', 1)[-1]
    for cl in comfyui_loras:
        cl_file = cl.replace('\\', '/').lower().rsplit('/', 1)[-1]
        if cl_file == name_file:
            return cl

    # 3) 子目录匹配 + 文件名 contains（双向）
    name_stem = name_file.rsplit('.', 1)[0] if '.' in name_file else name_file
    name_subdir = name_norm.rsplit('/', 1)[0] if '/' in name_norm else ''
    for cl in comfyui_loras:
        cl_norm = cl.replace('\\', '/').lower()
        cl_file = cl_norm.rsplit('/', 1)[-1]
        cl_stem = cl_file.rsplit('.', 1)[0] if '.' in cl_file else cl_file
        cl_subdir = cl_norm.rsplit('/', 1)[0] if '/' in cl_norm else ''
        # 同子目录下，文件名互相包含
        if name_subdir and cl_subdir and name_subdir == cl_subdir:
            if name_stem in cl_stem or cl_stem in name_stem:
                return cl

    # 4) 不限子目录，文件名 stem 包含匹配（至少 6 字符避免误匹配）
    if len(name_stem) >= 6:
        for cl in comfyui_loras:
            cl_norm = cl.replace('\\', '/').lower()
            cl_stem = cl_norm.rsplit('/', 1)[-1].rsplit('.', 1)[0]
            if name_stem in cl_stem:
                return cl

    print(f"[ComfyUI] ⚠️ LoRA 未在 ComfyUI 列表中找到匹配: {name}")
    return name  # 没找到，原样返回


def compute_weight_sweep(lora_checks: list) -> list | None:
    """
    根据未知权重 LoRA 的数量，生成权重扫描组合。
    lora_checks: checks['loras'] 列表（含 weight_source 字段）
    返回: [{lora_index: weight, ...}, ...] 或 None（无需扫描）
    lora_index 基于 found=True 的 LoRA 在列表中的顺序。
    """
    # 收集 found=True 的 LoRA 中，weight_source=='default' 的索引
    found_idx = 0
    unknown_indices = []
    for lc in lora_checks:
        if lc.get('found'):
            if lc.get('weight_source') == 'default':
                unknown_indices.append(found_idx)
            found_idx += 1

    if len(unknown_indices) == 1:
        idx = unknown_indices[0]
        return [{idx: round(0.1 * w, 1)} for w in range(1, 11)]  # 0.1~1.0, 10组
    elif len(unknown_indices) == 2:
        idx1, idx2 = unknown_indices
        combos = []
        for w1 in range(1, 6):
            for w2 in range(1, 6):
                combos.append({idx1: round(0.2 * w1, 1), idx2: round(0.2 * w2, 1)})
        return combos  # 0.2~1.0 × 0.2~1.0, 25组
    return None


def _update_lora_weights_in_workflow(workflow: Dict, weight_overrides: dict):
    """
    在已设置 LoRA 的工作流中，按 index 覆盖指定 LoRA 的权重。
    weight_overrides: {lora_index: new_weight, ...}
    """
    # Lora Loader Stack (rgthree)
    stack_nodes = _find_nodes_by_type(workflow, 'Lora Loader Stack (rgthree)')
    if stack_nodes:
        global_slot = 0
        for _, snode in stack_nodes:
            inp = snode['inputs']
            slot_keys = sorted([k for k in inp if k.startswith('lora_') and not k.startswith('lora_count')])
            for s in range(len(slot_keys)):
                if global_slot in weight_overrides:
                    idx_str = f"{s+1:02d}"
                    inp[f"strength_{idx_str}"] = weight_overrides[global_slot]
                global_slot += 1
        return

    # LoraLoader (单 LoRA)
    lora_nodes = _find_nodes_by_type(workflow, 'LoraLoader')
    if lora_nodes and 0 in weight_overrides:
        node = lora_nodes[0][1]
        node['inputs']['strength_model'] = weight_overrides[0]
        node['inputs']['strength_clip'] = weight_overrides[0]


def _set_lora_nodes(workflow: Dict, loras: list):
    """
    动态替换工作流中的 LoRA 节点。
    loras: [{"name": "subtype/filename.safetensors", "weight": 0.8}, ...]
    支持 LoraLoader（单 LoRA）和 Lora Loader Stack (rgthree)（多 LoRA）。
    """
    if not loras:
        return

    # 将每个 LoRA 名称与 ComfyUI 实际列表做匹配
    normalized = []
    for l in loras:
        resolved = _resolve_comfyui_lora_name(l['name'])
        normalized.append({
            'name': resolved,
            'weight': float(l.get('weight', 1.0))
        })
        if resolved != l['name']:
            print(f"[ComfyUI] LoRA 名称已匹配: {l['name']} → {resolved}")

    # 1) 尝试 Lora Loader Stack (rgthree) —— 支持多 LoRA
    stack_nodes = _find_nodes_by_type(workflow, 'Lora Loader Stack (rgthree)')
    if stack_nodes:
        # 收集所有 Stack 节点的 slot 数，按顺序分配 LoRA
        lora_idx = 0
        for _, snode in stack_nodes:
            inp = snode['inputs']
            slot_keys = sorted([k for k in inp if k.startswith('lora_')])
            num_slots = len(slot_keys)
            for s in range(num_slots):
                idx = f"{s+1:02d}"
                lora_key = f"lora_{idx}"
                str_key = f"strength_{idx}"
                if lora_idx < len(normalized):
                    inp[lora_key] = normalized[lora_idx]['name']
                    inp[str_key] = normalized[lora_idx]['weight']
                    lora_idx += 1
                else:
                    inp[lora_key] = 'None'
                    inp[str_key] = 1.0
        # 如果 LoRA 数量超过所有节点 slot 总数，打印警告
        if lora_idx < len(normalized):
            print(f"[ComfyUI] ⚠️ LoRA 数量 ({len(normalized)}) 超过工作流 slot 总数 ({lora_idx})，"
                  f"丢弃: {[l['name'] for l in normalized[lora_idx:]]}")
        return

    # 2) 尝试 LoraLoader（单 LoRA）
    lora_nodes = _find_nodes_by_type(workflow, 'LoraLoader')
    if lora_nodes and normalized:
        node = lora_nodes[0][1]
        node['inputs']['lora_name'] = normalized[0]['name']
        node['inputs']['strength_model'] = normalized[0]['weight']
        node['inputs']['strength_clip'] = normalized[0]['weight']
        return


_TRACKING_LOCAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'gen_tracking.json')


def _azure_available() -> bool:
    try:
        from azure_blob.credentials import CONNECTION_STRING
        return bool(CONNECTION_STRING)
    except Exception:
        return False


def _save_gen_tracking(batch_id, favorite_id, source_url='', gen_params=None):
    """保存 batch_id → favorite_id 追踪记录（本地 + Azure 双写）"""
    try:
        existing = _load_gen_tracking()
        existing[batch_id] = {
            'favorite_id': favorite_id,
            'source_url': source_url,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'gen_params': gen_params or {},
        }
        text = json.dumps(existing, ensure_ascii=False, indent=2)

        # 始终写本地
        os.makedirs(os.path.dirname(_TRACKING_LOCAL_PATH), exist_ok=True)
        with open(_TRACKING_LOCAL_PATH, 'w', encoding='utf-8') as f:
            f.write(text)

        # Azure 可用时同步
        if _azure_available():
            try:
                blob = BlobStorage(container='civitaidl')
                blob.put_json('data', 'gen_tracking.json', existing)
            except Exception as e:
                print(f"[Tracking] Azure 写入失败（本地已保存）: {e}")

        print(f"[Tracking] 已保存: batch={batch_id} → fav={favorite_id}")
    except Exception as e:
        print(f"[Tracking] ⚠️ 保存失败: {e}")


def _load_gen_tracking():
    """加载追踪记录（本地优先，Azure 回退迁移）"""
    # 1. 读本地
    if os.path.exists(_TRACKING_LOCAL_PATH):
        try:
            with open(_TRACKING_LOCAL_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass

    # 2. 本地为空，尝试从 Azure 迁移
    if _azure_available():
        try:
            blob = BlobStorage(container='civitaidl')
            data = blob.get_json('data', 'gen_tracking.json') or {}
            if data:
                os.makedirs(os.path.dirname(_TRACKING_LOCAL_PATH), exist_ok=True)
                with open(_TRACKING_LOCAL_PATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[Tracking] 从 Azure 迁移 {len(data)} 条到本地")
                return data
        except Exception:
            pass

    return {}


def run_comfyui_workflow(
    workflow_name: str,
    checkpoint: str,
    positive_prompt: str,
    negative_prompt: str = "low quality, worst quality",
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    cfg: float = 7.0,
    sampler: str = "dpmpp_2m",
    scheduler: str = "",
    seed: int = None,
    loras: list = None,
    batch_size: int = 4,
    vary_sizes: bool = False,
    variations: list = None,
    favorite_id: str = None,
    source_url: str = None,
    upscale_denoise: list = None
) -> Dict:
    """运行 ComfyUI 工作流（支持 UI 导出格式，自动转换为 API 格式）
    variations: 控制变量法参数列表，每个元素 {'label': ..., 'params': {sampler, scheduler, width, height}}
                若提供则忽略 batch_size / vary_sizes，每个 variation 出一张图
    batch_size: 一次提交多少个请求，默认 4（无 variations 时使用）
    vary_sizes: 是否按 XL 标准尺寸变化每个请求的宽高（自动绘制用）
    """

    try:
        # 1. 检查 ComfyUI 就绪
        ready, queue_data = wait_for_comfyui()
        if not ready:
            return {'status': 'error', 'message': 'ComfyUI not ready（无法连接 http://' + COMFYUI_URL + '）'}

        # 2. 加载工作流
        workflow_path = os.path.join(WORKFLOW_DIR, workflow_name + '.json')
        if not os.path.exists(workflow_path):
            return {'status': 'error', 'message': f'Workflow not found: {workflow_name}'}

        with open(workflow_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        # 3. 转换为 API 格式
        #    如果已是 API 格式（顶层 key 是节点 ID + class_type），直接用
        sample_key = next((k for k in raw if k not in (
            'id', 'revision', 'last_node_id', 'last_link_id',
            'nodes', 'links', 'groups', 'config', 'extra', 'version'
        ) and not k.startswith('_')), None)
        if sample_key and isinstance(raw.get(sample_key), dict) and 'class_type' in raw[sample_key]:
            workflow = raw
        else:
            workflow = _convert_ui_to_api(raw)

        # 4. 设置参数
        # Checkpoint
        ckpt_nodes = _find_nodes_by_type(workflow, 'CheckpointLoaderSimple')
        if ckpt_nodes:
            # ComfyUI checkpoint 列表包含子目录前缀（如 'xl\model.safetensors'）
            ckpt_name = checkpoint.replace('/', '\\')

            # 验证 checkpoint 在 ComfyUI 模型列表中（防止重启后模型尚未加载完）
            _ckpt_found = False
            for _attempt in range(12):  # 最多等 ~60s
                try:
                    _oi_req = urllib.request.Request(f'http://{COMFYUI_URL}/api/object_info/CheckpointLoaderSimple')
                    with _local_opener.open(_oi_req, timeout=5) as _oi_r:
                        _oi_data = json.loads(_oi_r.read())
                        _avail = _oi_data.get('CheckpointLoaderSimple', {}).get('input', {}).get('required', {}).get('ckpt_name', [[]])[0]
                        if ckpt_name in _avail:
                            _ckpt_found = True
                            break
                        # 也尝试带子目录前缀匹配
                        if any(n.endswith(ckpt_name) for n in _avail):
                            ckpt_name = next(n for n in _avail if n.endswith(ckpt_name))
                            _ckpt_found = True
                            break
                except Exception:
                    pass
                if _attempt == 0:
                    print(f"[ComfyUI] Checkpoint '{ckpt_name}' 未在模型列表中找到，等待 ComfyUI 加载...")
                time.sleep(5)

            if not _ckpt_found:
                print(f"[ComfyUI] ⚠️ Checkpoint '{ckpt_name}' 最终未找到，仍尝试提交")

            ckpt_nodes[0][1]['inputs']['ckpt_name'] = ckpt_name

        # KSampler → 同时通过连接关系定位 positive/negative 节点
        sampler_nodes = _find_nodes_by_type(workflow, 'KSampler')
        positive_nid = negative_nid = None
        if sampler_nodes:
            s_inputs = sampler_nodes[0][1]['inputs']
            s_inputs['steps'] = steps
            s_inputs['cfg'] = cfg
            s_inputs['sampler_name'] = _normalize_sampler(sampler)
            if scheduler:
                s_inputs['scheduler'] = scheduler
            s_inputs['seed'] = seed if seed else int(uuid.uuid4()) % (2**32)
            # 从 KSampler 的 positive/negative 连接追溯 CLIPTextEncode 节点 ID
            pos_ref = s_inputs.get('positive')
            neg_ref = s_inputs.get('negative')
            if isinstance(pos_ref, list):
                positive_nid = str(pos_ref[0])
            if isinstance(neg_ref, list):
                negative_nid = str(neg_ref[0])

        # 设置正向/负向提示词
        if positive_nid and positive_nid in workflow:
            workflow[positive_nid]['inputs']['text'] = positive_prompt
        if negative_nid and negative_nid in workflow:
            workflow[negative_nid]['inputs']['text'] = negative_prompt

        # LoRA
        if loras:
            _set_lora_nodes(workflow, loras)

        # EmptyLatentImage 尺寸
        size_nodes = _find_nodes_by_type(workflow, 'EmptyLatentImage')
        if size_nodes:
            print(f"[DEBUG] EmptyLatentImage BEFORE: {size_nodes[0][1]['inputs']}")
            size_nodes[0][1]['inputs']['width'] = width
            size_nodes[0][1]['inputs']['height'] = height
            print(f"[DEBUG] EmptyLatentImage AFTER: width={width}, height={height}")
        else:
            print(f"[DEBUG] ⚠️ No EmptyLatentImage node found in workflow!")

        # Upscale denoise: 将 denoise 值注入到非主 KSampler 节点（精绘 pass）
        if upscale_denoise and len(sampler_nodes) > 1:
            upscale_passes = sampler_nodes[1:]  # 跳过主 KSampler
            for i, (nid, node) in enumerate(upscale_passes):
                if i < len(upscale_denoise):
                    node['inputs']['denoise'] = upscale_denoise[i]

        # 5. 确保有 SaveImage 节点（history API 需要它来输出图片）
        #    如果有 SaveImageWebsocket → 替换为 SaveImage
        ws_save_nodes = _find_nodes_by_type(workflow, 'SaveImageWebsocket')
        for nid, node in ws_save_nodes:
            images_input = node['inputs'].get('images')
            workflow[nid] = {
                'class_type': 'SaveImage',
                'inputs': {'images': images_input, 'filename_prefix': 'ComfyUI'} if images_input else {}
            }

        has_save = bool(_find_nodes_by_type(workflow, 'SaveImage'))

        if not has_save:
            # 没有 SaveImage → 找 VAEDecode 的输出，新增 SaveImage
            vae_nodes = _find_nodes_by_type(workflow, 'VAEDecode')
            if vae_nodes:
                vae_nid = vae_nodes[0][0]
                new_id = str(max((int(k) for k in workflow if k.isdigit()), default=0) + 1)
                workflow[new_id] = {
                    'class_type': 'SaveImage',
                    'inputs': {'images': [vae_nid, 0], 'filename_prefix': 'ComfyUI'}
                }
                has_save = True

        if not has_save:
            return {'status': 'error', 'message': '工作流缺少图片输出节点（SaveImage 或 VAEDecode）'}

        # 6. 连接 WebSocket（添加超时和心跳）
        client_id = str(uuid.uuid4())
        ws = websocket.WebSocket()
        ws.settimeout(10)  # 连接超时 10 秒
        ws.connect(f"ws://{COMFYUI_URL}/ws?clientId={client_id}")
        ws.settimeout(600)  # recv 超时 10 分钟

        # 7. 批量提交
        import random as _random
        import copy as _copy

        batch_id = str(uuid.uuid4())[:8]
        prompt_ids = []
        variation_labels = []
        sampler_nodes = _find_nodes_by_type(workflow, 'KSampler')
        size_nodes = _find_nodes_by_type(workflow, 'EmptyLatentImage')

        # 判断是否为"无 seed"模式（seed 缺失时随机 3 张）
        _no_seed = (seed is None or seed == -1)

        # 保存用户指定的原始 seed（无 seed 模式下视为 None，每张都随机）
        _original_seed = None if _no_seed else (sampler_nodes[0][1]['inputs'].get('seed') if sampler_nodes else None)

        if _no_seed:
            print(f"[ComfyUI] 无 seed，将随机生成 3 张")

        if variations:
            # ===== 控制变量法模式（含权重扫描） =====
            total = len(variations)

            # 判断是否为纯权重扫描批次（所有变体都含 lora_weights，控制变量要求同一 seed）
            _is_weight_sweep = all(var.get('params', {}).get('lora_weights') for var in variations)

            # 确定本批次统一 seed：
            #   - 权重扫描：所有变体共用同一 seed（控制变量，只改权重）
            #     · 有原始 seed → 用原始 seed
            #     · 无 seed → 随机一个，整批共用
            #   - 非权重扫描（采样器/尺寸变体）：_no_seed 时各自随机，有 seed 时用原始 seed
            if _is_weight_sweep:
                _sweep_seed = _original_seed if not _no_seed else _random.randint(0, 2**32 - 1)
                print(f"[ComfyUI] 权重扫描模式，统一 seed={_sweep_seed}，共 {total} 个权重组合")
            else:
                _sweep_seed = None  # 非权重扫描，逐张决定

            for idx, var in enumerate(variations):
                vp = var.get('params', {})
                label = var.get('label', f'变体{idx+1}')

                # 应用该 variation 的参数
                if sampler_nodes:
                    s_inp = sampler_nodes[0][1]['inputs']
                    if 'sampler' in vp:
                        s_inp['sampler_name'] = _normalize_sampler(vp['sampler'])
                    if 'scheduler' in vp and vp['scheduler']:
                        s_inp['scheduler'] = vp['scheduler']
                    # seed 策略：
                    #   权重扫描 → 整批统一 seed（控制变量）
                    #   采样器/尺寸变体 → _no_seed 时每张随机，有 seed 时全用原始 seed
                    if _is_weight_sweep:
                        s_inp['seed'] = _sweep_seed
                    elif _no_seed:
                        s_inp['seed'] = _random.randint(0, 2**32 - 1)
                    else:
                        s_inp['seed'] = _original_seed

                bw = vp.get('width', width)
                bh = vp.get('height', height)
                if size_nodes:
                    size_nodes[0][1]['inputs']['width'] = bw
                    size_nodes[0][1]['inputs']['height'] = bh

                # LoRA 权重覆盖（权重扫描用）
                lora_wt = vp.get('lora_weights')
                if lora_wt and isinstance(lora_wt, dict):
                    _update_lora_weights_in_workflow(workflow, {int(k): v for k, v in lora_wt.items()})

                wf_copy = _copy.deepcopy(workflow)
                result = queue_prompt(wf_copy, client_id)
                if 'prompt_id' not in result:
                    if idx == 0:
                        ws.close()
                        detail = result.get('detail', result.get('error', ''))
                        return {'status': 'error', 'message': f'ComfyUI 拒绝了工作流: {detail}'}
                    break
                prompt_ids.append(result['prompt_id'])
                variation_labels.append(label)
                print(f"[ComfyUI] 批次 {batch_id} [{label}] 已提交: {result['prompt_id']} ({bw}x{bh})")
        else:
            # ===== 传统批量模式 =====
            if _no_seed:
                # 无 seed：固定尺寸，随机 3 个 seed
                actual_batch = 3
                batch_sizes_list = [(width, height)] * actual_batch
            else:
                actual_batch = batch_size
                _XL_LANDSCAPE = [(1152, 896), (1216, 832), (1344, 768), (1536, 640)]
                _XL_PORTRAIT  = [(896, 1152), (832, 1216), (768, 1344), (640, 1536)]
                if vary_sizes and batch_size <= 4:
                    if width > height:
                        batch_sizes_list = _XL_LANDSCAPE[:batch_size]
                    else:
                        batch_sizes_list = _XL_PORTRAIT[:batch_size]
                else:
                    batch_sizes_list = [(width, height)] * batch_size

            for idx in range(actual_batch):
                if sampler_nodes:
                    if _no_seed or idx > 0:
                        sampler_nodes[0][1]['inputs']['seed'] = _random.randint(0, 2**32 - 1)
                    else:
                        sampler_nodes[0][1]['inputs']['seed'] = _original_seed
                bw, bh = batch_sizes_list[idx]
                if size_nodes:
                    size_nodes[0][1]['inputs']['width'] = bw
                    size_nodes[0][1]['inputs']['height'] = bh
                wf_copy = _copy.deepcopy(workflow)
                result = queue_prompt(wf_copy, client_id)
                if 'prompt_id' not in result:
                    if idx == 0:
                        ws.close()
                        detail = result.get('detail', result.get('error', ''))
                        return {'status': 'error', 'message': f'ComfyUI 拒绝了工作流: {detail}'}
                    break
                prompt_ids.append(result['prompt_id'])
                variation_labels.append(f'第{idx+1}张')
                print(f"[ComfyUI] 批次 {batch_id} 第{idx+1}/{actual_batch}个已提交: {result['prompt_id']} ({bw}x{bh})")

        # 注册生成任务
        with _gen_lock:
            _gen_tasks[batch_id] = {
                'status': 'running',
                'batch_id': batch_id,
                'prompt_ids': prompt_ids,
                'batch_size': len(prompt_ids),
                'completed': 0,
                'images_count': 0,
                'saved_paths': [],
                'message': f'已提交 {len(prompt_ids)} 个任务，生成中...',
                '_start_time': time.time(),
                'favorite_id': favorite_id or '',
                'source_url': source_url or ''
            }

        # 8. 后台线程等待全部完成并保存
        def _wait_and_save_batch():
            try:
                # WS 只等完成信号，不接收图片二进制
                completed_ids, error_ids = wait_for_batch_ws(ws, prompt_ids)
                ws.close()

                # 只对成功完成的 prompt 获取图片（跳过执行出错的）
                fetch_ids = list(completed_ids) if completed_ids else []
                all_images = fetch_images_batch(fetch_ids) if fetch_ids else {}

                os.makedirs(OUTPUT_DIR, exist_ok=True)
                saved_paths = []
                azure_urls = []
                total_count = 0
                
                # Azure Blob 配置
                azure_container = 'civitaidl'
                today = time.strftime('%Y-%m-%d')
                
                for pid in fetch_ids:
                    imgs = all_images.get(pid, [])
                    for i, img_data in enumerate(imgs):
                        fname = f"{batch_id}_{pid[:8]}_{i}.png"
                        fpath = os.path.join(OUTPUT_DIR, fname)
                        
                        # 保存本地（PNG 已包含 workflow 元数据）
                        with open(fpath, 'wb') as fout:
                            fout.write(img_data)
                        saved_paths.append(fpath)
                        total_count += 1
                        print(f"[ComfyUI] 图片已保存（含workflow）: {fpath}")
                        
                        # 上传 Azure Blob（不影响本地保存）
                        try:
                            blob_path = f"generated/{today}/{fname}"
                            blob = BlobStorage(container=azure_container)
                            url = blob.put_bytes(img_data, blob_path)
                            azure_urls.append(url)
                            print(f"[Azure] 图片已上传: {url}")
                        except Exception as azure_err:
                            print(f"[Azure] ⚠️ 上传失败: {azure_err}")

                error_summary = '; '.join(set(error_ids.values())) if error_ids else ''
                final_status = 'success' if total_count > 0 else ('error' if error_ids else 'success')
                msg = f'批次完成，共 {total_count} 张图片已保存'
                if error_ids:
                    msg += f'，{len(error_ids)} 个任务失败: {error_summary[:120]}'
                with _gen_lock:
                    _gen_tasks[batch_id].update({
                        'status': final_status,
                        'completed': len(completed_ids),
                        'images_count': total_count,
                        'saved_paths': saved_paths,
                        'azure_urls': azure_urls,
                        'errors': error_ids,
                        'message': msg,
                    })

                # 写入追踪记录（batch_id → favorite_id 映射）
                if favorite_id:
                    _gp = {
                        'checkpoint': checkpoint, 'loras': loras or [],
                        'prompt': positive_prompt, 'negative_prompt': negative_prompt,
                        'width': width, 'height': height, 'steps': steps,
                        'cfg': cfg, 'sampler': sampler, 'scheduler': scheduler, 'seed': seed,
                    }
                    try:
                        _save_gen_tracking(batch_id, favorite_id, source_url or '', gen_params=_gp)
                    except Exception as track_err:
                        print(f"[Tracking] ⚠️ 写入追踪记录失败: {track_err}")
                    # 注：不再在生图完成时标记 done，done 仅由美学分析完成后设置
            except Exception as ex:
                try:
                    ws.close()
                except Exception:
                    pass
                with _gen_lock:
                    _gen_tasks[batch_id].update({
                        'status': 'error',
                        'message': f'生成失败: {str(ex)}'
                    })

        # 立即写入追踪记录（让 cleanup 能识别正在生成的任务）
        if favorite_id:
            _gp_early = {
                'checkpoint': checkpoint, 'loras': loras or [],
                'prompt': positive_prompt, 'negative_prompt': negative_prompt,
                'width': width, 'height': height, 'steps': steps,
                'cfg': cfg, 'sampler': sampler, 'scheduler': scheduler, 'seed': seed,
            }
            try:
                _save_gen_tracking(batch_id, favorite_id, source_url or '', gen_params=_gp_early)
            except Exception as _te:
                print(f"[Tracking] ⚠️ 提交时写入追踪失败: {_te}")

        threading.Thread(target=_wait_and_save_batch, daemon=True).start()

        # 立即返回 batch_id，前端轮询状态
        return {
            'status': 'submitted',
            'prompt_id': batch_id,
            'batch_size': len(prompt_ids),
            'message': f'已提交 {len(prompt_ids)} 个任务到 ComfyUI，生成中...'
        }

    except Exception as e:
        return {'status': 'error', 'message': f'生成失败: {str(e)}'}


# ============== HTTP API ==============

class APIHandler(BaseHTTPRequestHandler):
    """HTTP API 处理器"""
    
    def log_message(self, format, *args):
        print(f"[API] {self.address_string()} - {format % args}")
    
    def send_json(self, data: Dict, status: int = 200):
        """发送 JSON 响应"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == '/api/status' or path == '/api/health':
            self.send_json({
                'status': 'ok', 
                'service': 'civitai-downloader-with-comfyui',
                'comfyui_url': f'http://{COMFYUI_URL}'
            })
        
        elif path == '/api/workflows':
            query = parse_qs(parsed.query)
            refresh = query.get('refresh', ['0'])[0] == '1'
            items = model_cache.workflows.get_list(refresh=refresh)
            self.send_json({'status': 'success', 'items': [
                {'name': w['name'], 'source': w.get('source', 'local'), 'description': w.get('description', '')}
                for w in items
            ]})
        
        elif path == '/api/subtypes':
            # 动态扫描 D: 盘子文件夹列表
            query = parse_qs(parsed.query)
            main_type = query.get('type', ['ckpt'])[0]
            if main_type not in ('ckpt', 'lora'):
                self.send_json({'status': 'error', 'message': 'type must be ckpt or lora'}, 400)
                return
            subtypes = scan_subtypes(main_type)
            self.send_json({'status': 'success', 'type': main_type, 'subtypes': subtypes})
        
        elif path == '/api/files':
            # 动态扫描某子文件夹下的模型文件
            query = parse_qs(parsed.query)
            main_type = query.get('type', ['ckpt'])[0]
            subtype = query.get('subtype', [''])[0]
            if main_type not in ('ckpt', 'lora') or not subtype:
                self.send_json({'status': 'error', 'message': 'type and subtype required'}, 400)
                return
            files = scan_files(main_type, subtype)
            self.send_json({'status': 'success', 'type': main_type, 'subtype': subtype, 'files': files, 'count': len(files)})
        
        elif path == '/api/download/status':
            query = parse_qs(parsed.query)
            task_id = query.get('task_id', [''])[0]
            if not task_id:
                self.send_json({'status': 'error', 'message': '缺少 task_id'}, 400)
                return
            with _tasks_lock:
                task = _download_tasks.get(task_id)
            if not task:
                self.send_json({'status': 'error', 'message': '任务不存在'}, 404)
            else:
                self.send_json(task)
        
        elif path == '/api/download/active':
            with _tasks_lock:
                active = {tid: dict(t) for tid, t in _download_tasks.items() if not t.get('done')}
                recent_done = {tid: dict(t) for tid, t in _download_tasks.items()
                               if t.get('done') and time.time() - t.get('_finish_time', 0) < 30}
            self.send_json({'status': 'success', 'active': active, 'recent': recent_done})
        
        elif path.startswith('/api/workflow/status'):
            query = parse_qs(parsed.query)
            prompt_id = query.get('prompt_id', [''])[0]
            if not prompt_id:
                self.send_json({'status': 'error', 'message': 'missing prompt_id'}, 400)
                return
            with _gen_lock:
                task = _gen_tasks.get(prompt_id)
            if not task:
                self.send_json({'status': 'error', 'message': 'task not found'}, 404)
            else:
                # 不返回内部字段
                out = {k: v for k, v in task.items() if not k.startswith('_')}
                self.send_json(out)

        elif path.startswith('/api/aesthetic/status'):
            query = parse_qs(parsed.query)
            task_id = query.get('task_id', [''])[0]
            if not task_id:
                self.send_json({'status': 'error', 'message': 'missing task_id'}, 400)
                return
            with _aesthetic_lock:
                task = _aesthetic_tasks.get(task_id)
            if not task:
                self.send_json({'status': 'error', 'message': 'task not found'}, 404)
            else:
                self.send_json(task)

        elif path.startswith('/api/aesthetic/result'):
            # 按图片 URL 查缓存结果
            query = parse_qs(parsed.query)
            image_url = query.get('image_url', [''])[0]
            if not image_url:
                self.send_json({'status': 'error', 'message': 'missing image_url'}, 400)
                return
            _cache_key = _aesthetic_cache_key(image_url)
            try:
                _rb = BlobStorage(container='civitaidl')
                cached = _rb.get_json(_AESTHETIC_CACHE_BLOB_PREFIX, _cache_key + '.json')
                if cached:
                    self.send_json({'status': 'success', 'blueprint': cached})
                else:
                    self.send_json({'status': 'not_found', 'message': '该图片暂无分析结果'})
            except Exception:
                self.send_json({'status': 'not_found', 'message': '该图片暂无分析结果'})

        elif path == '/api/comfyui/checkpoints':
            try:
                ckpts = get_comfyui_checkpoints()
                self.send_json({'status': 'ok', 'checkpoints': sorted(ckpts)})
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)

        elif path == '/api/comfyui/queue':
            ready, data = wait_for_comfyui(timeout=10)
            if ready:
                self.send_json({'status': 'success', 'queue': data})
            else:
                self.send_json({'status': 'error', 'message': 'ComfyUI not ready'})
        
        elif path == '/api/azure/list':
            # 查询 Azure Blob 列表（按时间倒序，最近100个）+ 追踪数据
            query = parse_qs(parsed.query)
            prefix = query.get('prefix', ['generated/'])[0]
            limit = int(query.get('limit', ['100'])[0])
            limit = min(limit, 500)  # 最大500条
            
            try:
                blob = BlobStorage(container='civitaidl')
                urls = blob.list_recent_blobs(prefix=prefix, max_results=limit, return_urls=True)
                # 加载追踪数据（batch_id → favorite_id 映射）
                tracking = _load_gen_tracking()
                self.send_json({
                    'success': True,
                    'blobs': urls,
                    'tracking': tracking,
                    'total': len(urls)
                })
            except Exception as e:
                self.send_json({'success': False, 'error': str(e)}, 500)
        
        elif path == '/api/azure/delete':
            self.send_json({'error': 'Use POST method'}, 405)

        elif path == '/api/admin/model-index':
            # 返回完整模型索引（Admin 用）
            from util import model_index
            query = parse_qs(parsed.query)
            search = query.get('q', [''])[0].lower()
            items = model_index.get_all()
            # 附加磁盘存在性检查
            for item in items:
                for ver in item.get('versions', []):
                    ver['exists'] = os.path.isfile(ver.get('path', ''))
            if search:
                items = [m for m in items if search in m.get('name', '').lower()
                         or search in str(m.get('model_id', ''))
                         or any(search in v.get('filename', '').lower() for v in m.get('versions', []))]
            self.send_json({'status': 'ok', 'items': items, 'total': len(items)})

        elif path == '/api/admin/disk-files':
            # 扫描磁盘上的模型文件（用于 Admin 手工映射选择文件）
            query = parse_qs(parsed.query)
            main_type = query.get('type', ['ckpt'])[0]
            if main_type not in ('ckpt', 'lora', 'embedding'):
                self.send_json({'status': 'error', 'message': 'type must be ckpt, lora or embedding'}, 400)
                return
            from config import get_base_dir, MODEL_EXTENSIONS
            base = get_base_dir(main_type)
            results = []
            if os.path.exists(base):
                for sub in sorted(os.listdir(base)):
                    sub_path = os.path.join(base, sub)
                    if os.path.isdir(sub_path):
                        for f in sorted(os.listdir(sub_path)):
                            if f.lower().endswith(MODEL_EXTENSIONS):
                                fp = os.path.join(sub_path, f)
                                results.append({'subtype': sub, 'filename': f, 'path': fp,
                                                'size_mb': round(os.path.getsize(fp) / 1048576)})
            self.send_json({'status': 'ok', 'type': main_type, 'files': results, 'count': len(results)})

        elif path == '/api/sync':
            try:
                from data_sync import sync_all
                result = sync_all()
                self.send_json({'status': 'ok', **result})
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)

        elif path.startswith('/api/discard-log/check'):
            # 检查某 fav_id 的废弃记录（供生图 tab 分析后查询）
            from discard_log import get_entries
            query = parse_qs(parsed.query)
            fav_id = query.get('fav_id', [''])[0]
            if not fav_id:
                self.send_json({'status': 'error', 'message': '缺少 fav_id'}, 400)
                return
            entries = get_entries(fav_id)
            self.send_json({'status': 'ok', 'fav_id': fav_id, 'entries': entries, 'count': len(entries)})

        elif path == '/api/favorite/list':
            # 读取收藏队列并返回列表（含 image_id）
            from favorite_images import list_all
            try:
                items = list_all()
                for entry in items:
                    m = re.search(r'/images/(\d+)', entry.get('url', ''))
                    if m:
                        entry['image_id'] = int(m.group(1))
                # 按创建时间倒序（最新在前）
                items.sort(key=lambda x: x.get('created_at', ''), reverse=True)
                self.send_json({'status': 'ok', 'items': items, 'total': len(items)})
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)

        elif path == '/api/image/thumb':
            # 获取 Civitai 图片缩略图 URL（带缓存）
            query = parse_qs(parsed.query)
            image_id = query.get('id', [''])[0]
            if not image_id:
                self.send_json({'status': 'error', 'message': 'missing id'}, 400)
                return
            # 内存缓存
            if not hasattr(self.__class__, '_thumb_cache'):
                self.__class__._thumb_cache = {}
            cache = self.__class__._thumb_cache
            if image_id in cache:
                self.send_json(cache[image_id])
                return
            # 调用 Civitai API 获取图片信息
            try:
                import requests as _requests
                from config import CIVITAI_API_TOKEN
                sess = _requests.Session()
                params = {'input': json.dumps({'json': {'id': int(image_id)}})}
                if CIVITAI_API_TOKEN:
                    params['token'] = CIVITAI_API_TOKEN
                resp = sess.get(f'{CIVITAI_API_BASE}/trpc/image.get',
                                params=params, timeout=15,
                                headers={'User-Agent': 'Mozilla/5.0'})
                resp.raise_for_status()
                img_data = resp.json().get('result', {}).get('data', {}).get('json', {})
                raw_url = img_data.get('url', '')
                # Civitai API 返回的 url 是 UUID，需拼接 CDN 前缀
                CDN_BASE = 'https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA'
                if raw_url and not raw_url.startswith('http'):
                    thumb_url = f'{CDN_BASE}/{raw_url}/width=450'
                elif raw_url and '/width=' not in raw_url:
                    thumb_url = raw_url.rstrip('/') + '/width=450'
                elif raw_url:
                    thumb_url = re.sub(r'/width=\d+', '/width=450', raw_url)
                else:
                    thumb_url = ''
                result = {
                    'status': 'ok',
                    'image_id': int(image_id),
                    'thumb_url': thumb_url,
                    'width': img_data.get('width'),
                    'height': img_data.get('height'),
                    'nsfw_level': img_data.get('nsfwLevel', 0),
                }
                cache[image_id] = result
                self.send_json(result)
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)

        elif path == '/api/auto-replicate/status':
            import auto_replicate
            self.send_json({'status': 'ok', **auto_replicate.get_status()})

        else:
            self.send_json({'error': 'Not Found'}, 404)
    
    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, ngrok-skip-browser-warning')
        self.end_headers()
    
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        if path == '/api/download':
            url = data.get('url')
            type_subtype = data.get('type', 'ckpt.xl')
            
            if not url:
                self.send_json({'status': 'error', 'message': '缺少 url 参数'}, 400)
                return
            
            # 1. URL 去重：检查活跃/排队任务中是否已有相同 URL
            with _tasks_lock:
                for t in _download_tasks.values():
                    if not t.get('done') and t.get('url') == url:
                        self.send_json({'status': 'error', 'message': '该URL已在下载队列中，请勿重复提交'})
                        return
            
            # 2. 同步预检：解析URL + 调用Civitai API + D盘全局判重
            try:
                downloader = CivitaiDownloader()
                prep = downloader.prepare(url, type_subtype)
            except Exception as e:
                self.send_json({'status': 'error', 'message': f'预检失败: {str(e)}'})
                return
            
            # 预检未通过（已存在/解析失败等），直接返回结果
            if prep['status'] != 'ok':
                # 已存在时，确保索引中有记录（文件在磁盘但可能不在索引里）
                if prep['status'] == 'exists' and prep.get('model_info'):
                    try:
                        from util import model_index
                        mi = prep['model_info']
                        model_index.upsert(
                            model_id=mi.get('model_id', ''),
                            model_name=mi.get('title', ''),
                            version_id=mi.get('version_id', ''),
                            version_name=mi.get('version_name', ''),
                            filename=mi.get('file_name', ''),
                            path=os.path.abspath(prep.get('path', '')),
                        )
                    except Exception as e:
                        print(f"[WARN] 更新索引失败(exists): {e}")
                self.send_json(prep)
                return
            
            # 3. 预检通过，加入下载队列
            task_id = str(uuid.uuid4())[:8]
            with _tasks_lock:
                queue_pos = sum(1 for t in _download_tasks.values() if not t.get('done'))
                _download_tasks[task_id] = {
                    'status': 'queued',
                    'phase': '排队中' if queue_pos > 0 else '准备中',
                    'queue_position': queue_pos,
                    'url': url,
                    'type': type_subtype,
                    'title': prep.get('title', ''),
                    'file_name': prep.get('file_name', ''),
                    'downloaded': 0,
                    'total_size': 0,
                    'percent': 0,
                    'done': False,
                }
            
            _download_queue.put((task_id, prep['model_info'], prep['target_dir']))
            
            msg = '下载任务已提交' if queue_pos == 0 else f'已加入队列（前面还有 {queue_pos} 个任务）'
            self.send_json({
                'status': 'accepted', 'task_id': task_id,
                'title': prep.get('title', ''),
                'file_name': prep.get('file_name', ''),
                'queue_position': queue_pos, 'message': msg
            })
        
        elif path == '/api/workflow/run':
            # 运行 ComfyUI 工作流
            print(f"[DEBUG] workflow/run received: width={data.get('width')}, height={data.get('height')}")
            if data.get('variations'):
                for vi, vv in enumerate(data['variations']):
                    vp = vv.get('params', {})
                    print(f"[DEBUG]   variation[{vi}] '{vv.get('label')}': width={vp.get('width')}, height={vp.get('height')}")
            result = run_comfyui_workflow(
                workflow_name=data.get('workflow', 'nolora'),
                checkpoint=data.get('checkpoint', ''),
                positive_prompt=data.get('prompt', ''),
                negative_prompt=data.get('negative_prompt', 'low quality, worst quality'),
                width=data.get('width', 512),
                height=data.get('height', 512),
                steps=data.get('steps', 20),
                cfg=data.get('cfg', 7.0),
                sampler=data.get('sampler', 'dpmpp_2m'),
                scheduler=data.get('scheduler', ''),
                seed=data.get('seed'),
                loras=data.get('loras'),
                batch_size=data.get('batch_size', 4),
                vary_sizes=data.get('vary_sizes', False),
                variations=data.get('variations'),
                favorite_id=data.get('favorite_id', ''),
                source_url=data.get('source_url', ''),
                upscale_denoise=data.get('upscale_denoise')
            )
            
            if result['status'] in ('success', 'submitted'):
                self.send_json(result)
            else:
                self.send_json(result, 500)
        
        elif path == '/api/image/parse':
            # 解析 Civitai 图片URL，提取生成参数，检查D盘模型
            image_url = data.get('url', '')
            if not image_url:
                self.send_json({'status': 'error', 'message': '缺少 url 参数'}, 400)
                return
            result = parse_civitai_image(image_url)
            if result['status'] == 'error':
                self.send_json(result, 400)
            else:
                self.send_json(result)
        
        elif path == '/api/comfyui/restart':
            try:
                _restart_comfyui(wait_ready=False)
                self.send_json({
                    'status': 'success',
                    'message': '已终止 ComfyUI 进程，watchdog 将自动重启（约10-30秒）'
                })
            except Exception as e:
                self.send_json({'status': 'error', 'message': f'重启失败: {str(e)}'}, 500)

        elif path == '/api/aesthetic/analyze':
            # 美学分析（异步）：立即返回 task_id，后台线程执行
            image_url = data.get('image_url', '')
            user_why_good = data.get('user_why_good', '')
            favorite_id = data.get('favorite_id', '')  # 可选：关联的收藏条目 ID

            if not image_url:
                self.send_json({'status': 'error', 'message': '缺少 image_url 参数'}, 400)
                return

            # 检查缓存是否已有结果
            cache_key = _aesthetic_cache_key(image_url)
            _cache_blob = BlobStorage(container='civitaidl')
            if not data.get('force', False):
                try:
                    cached = _cache_blob.get_json(_AESTHETIC_CACHE_BLOB_PREFIX, cache_key + '.json')
                    if cached:
                        self.send_json({'status': 'cached', 'blueprint': cached, 'message': '已有缓存结果'})
                        return
                except Exception:
                    pass

            task_id = str(uuid.uuid4())[:8]
            with _aesthetic_lock:
                _aesthetic_tasks[task_id] = {
                    'status': 'running',
                    'task_id': task_id,
                    'image_url': image_url,
                    'message': '美学分析进行中...',
                    '_start_time': time.time(),
                }

            _fav_id = favorite_id  # 捕获到闭包

            def _run_aesthetic():
                try:
                    from llm.aesthetic import analyze, save_blueprint
                    blueprint = analyze(image_source=image_url, user_why_good=user_why_good)
                    save_blueprint(blueprint)
                    # 按图片缓存结果到 Azure Blob
                    try:
                        _cb = BlobStorage(container='civitaidl')
                        _cb.put_json(_AESTHETIC_CACHE_BLOB_PREFIX, cache_key + '.json', blueprint, indent=2)
                    except Exception as _ce:
                        print(f"[Aesthetic] 缓存写入 blob 失败: {_ce}")
                    with _aesthetic_lock:
                        _aesthetic_tasks[task_id].update({
                            'status': 'success',
                            'blueprint': blueprint,
                            'message': f'分析完成: {blueprint.get("work_title", "?")}',
                        })
                    print(f"[Aesthetic] 任务 {task_id} 完成")
                    # 如果关联了收藏条目，标记为 done 并清除废弃记录
                    if _fav_id:
                        try:
                            from favorite_images import mark_done
                            mark_done(_fav_id)
                            print(f"[Aesthetic] 收藏 {_fav_id} 已标记为 done")
                        except Exception as fav_err:
                            print(f"[Aesthetic] 更新收藏状态失败: {fav_err}")
                        try:
                            from discard_log import clear_fav
                            cleared = clear_fav(_fav_id)
                            if cleared.get('cleared', 0) > 0:
                                print(f"[Aesthetic] 已清除 {_fav_id} 的 {cleared['cleared']} 条废弃记录")
                        except Exception as dl_err:
                            print(f"[Aesthetic] 清除废弃记录失败: {dl_err}")
                except Exception as ex:
                    import traceback
                    traceback.print_exc()
                    with _aesthetic_lock:
                        _aesthetic_tasks[task_id].update({
                            'status': 'error',
                            'message': str(ex),
                        })

            threading.Thread(target=_run_aesthetic, daemon=True).start()
            self.send_json({
                'status': 'submitted',
                'task_id': task_id,
                'message': '美学分析已提交，后台执行中...',
            })

        elif path == '/api/cache/refresh':
            model_cache.refresh_all()
            from util import model_index
            model_index.load()
            self.send_json({'status': 'success', 'message': 'Cache refreshed (including model index)'})
        
        elif path == '/api/favorite/add':
            # 添加收藏图片 URL
            from favorite_images import save_url_to_queue
            url = data.get('url', '')
            result = save_url_to_queue(url)
            status = 200 if result['status'] == 'ok' else 400
            self.send_json(result, status)

        elif path == '/api/favorite/status':
            # 队列状态
            from favorite_images import get_queue_status
            stats = get_queue_status()
            self.send_json({'status': 'ok', 'stats': stats})

        elif path == '/api/favorite/consume':
            # 消费一条（供其他进程调用）
            from favorite_images import consume_one
            result = consume_one()
            self.send_json(result)

        elif path == '/api/favorite/update-status':
            # 按 id 更新收藏条目状态
            from favorite_images import update_status
            item_id = data.get('id', '')
            new_status = data.get('status', '')
            fail_reason = data.get('fail_reason', '') or ''
            retry_reason = data.get('retry_reason', '') or ''
            if not item_id or not new_status:
                self.send_json({'status': 'error', 'message': '缺少 id 或 status 参数'}, 400)
                return
            if new_status not in ('pending', 'processing', 'done', 'fail'):
                self.send_json({'status': 'error', 'message': '无效的 status 值'}, 400)
                return
            result = update_status(item_id, new_status, fail_reason=fail_reason or None, retry_reason=retry_reason or None)
            status_code = 200 if result['status'] == 'ok' else 404
            self.send_json(result, status_code)

        elif path == '/api/favorite/delete':
            # 按 id 删除收藏条目
            from favorite_images import delete_by_id
            item_id = data.get('id', '')
            if not item_id:
                self.send_json({'status': 'error', 'message': '缺少 id 参数'}, 400)
                return
            result = delete_by_id(item_id)
            status_code = 200 if result['status'] == 'ok' else 404
            self.send_json(result, status_code)

        elif path == '/api/favorite/cleanup':
            # 清理已完成的
            from favorite_images import cleanup_done
            result = cleanup_done()
            self.send_json(result)

        elif path == '/api/discard-log/add':
            # 记录一条废弃生成参数（blob 删除且非标记失败时调用）
            from discard_log import add_entry
            fav_id = data.get('fav_id', '')
            params = data.get('params', {})
            if not fav_id or not params:
                self.send_json({'status': 'error', 'message': '缺少 fav_id 或 params'}, 400)
                return
            result = add_entry(fav_id, params)
            self.send_json(result)

        elif path == '/api/discard-log/clear':
            # 清除某 fav_id 的废弃记录（美学分析完成后调用）
            from discard_log import clear_fav
            fav_id = data.get('fav_id', '')
            if not fav_id:
                self.send_json({'status': 'error', 'message': '缺少 fav_id'}, 400)
                return
            result = clear_fav(fav_id)
            self.send_json(result)

        elif path == '/api/auto-replicate/start':
            import auto_replicate
            result = auto_replicate.start()
            self.send_json(result)

        elif path == '/api/auto-replicate/stop':
            import auto_replicate
            result = auto_replicate.stop()
            self.send_json(result)

        elif path == '/api/favorites/cleanup-processing':
            # 清理伪待分析：processing 状态但无对应生成作品 → 改回 pending
            from favorite_images import list_all, update_status as _fav_update
            cleanup_log = []

            # 1. 从 gen_tracking 收集有追踪记录的 favorite_id
            tracking = _load_gen_tracking()
            tracked_fav_ids = set()
            for v in tracking.values():
                fid = v.get('favorite_id', '')
                if fid:
                    tracked_fav_ids.add(fid)
            cleanup_log.append(f"gen_tracking 中有 {len(tracked_fav_ids)} 个收藏有生成记录")

            # 2. 从 _gen_tasks 收集正在生成中的 favorite_id（尚未写入 tracking 的）
            inflight_fav_ids = set()
            with _gen_lock:
                for task in _gen_tasks.values():
                    fid = task.get('favorite_id', '')
                    if fid and task.get('status') in ('running', 'submitted'):
                        inflight_fav_ids.add(fid)
            cleanup_log.append(f"当前正在生成中的收藏: {len(inflight_fav_ids)} 个")

            # 合并：有追踪 + 正在生成 = 不应清理
            protected_ids = tracked_fav_ids | inflight_fav_ids

            entries = list_all()
            processing_entries = [e for e in entries if e.get('status') == 'processing']
            cleanup_log.append(f"processing 状态的收藏共 {len(processing_entries)} 条")

            reverted = []
            kept = []
            for e in processing_entries:
                eid = e['id']
                if eid in protected_ids:
                    kept.append(eid)
                else:
                    _fav_update(eid, 'pending')
                    reverted.append(eid)

            cleanup_log.append(f"保留（有生成记录/正在生成）: {len(kept)} 条")
            cleanup_log.append(f"已清理（无生成记录）: {len(reverted)} 条")

            self.send_json({
                'status': 'ok',
                'reverted': len(reverted),
                'kept': len(kept),
                'total_processing': len(processing_entries),
                'message': f'已将 {len(reverted)} 条伪处理中改回待处理（保留 {len(kept)} 条有生成记录的）',
                'log': cleanup_log,
            })

        elif path == '/api/admin/manual-register':
            # 手动注册：用户已手工下载模型，提供 c 站 URL + 本地文件名 + 失效别名
            import re as _re
            from util import model_index
            from civitaidl import CivitaiDownloader
            from config import LORA_BASE_DIR, CKPT_BASE_DIR, EMBEDDING_BASE_DIR, resolve_type_subtype

            civitai_url = data.get('civitai_url', '').strip()
            local_filename = data.get('local_filename', '').strip()
            type_subtype = data.get('type_subtype', '').strip()  # e.g. lora.xl-style
            alias_name = data.get('alias_name', '').strip()  # e.g. version_1533177

            if not civitai_url or not local_filename or not type_subtype:
                self.send_json({'status': 'error', 'message': '缺少 civitai_url / local_filename / type_subtype'}, 400)
                return

            # 1. 解析目标目录
            resolved = resolve_type_subtype(type_subtype)
            if not resolved:
                self.send_json({'status': 'error', 'message': f'未知类型: {type_subtype}'}, 400)
                return
            main_type, subtype, target_dir = resolved

            # 2. 检查本地文件是否存在
            local_path = os.path.join(target_dir, local_filename)
            if not os.path.isfile(local_path):
                # 也尝试在目标目录下搜索
                found_file = None
                if os.path.isdir(target_dir):
                    for f in os.listdir(target_dir):
                        if f.lower() == local_filename.lower():
                            found_file = f
                            break
                if found_file:
                    local_path = os.path.join(target_dir, found_file)
                    local_filename = found_file
                else:
                    self.send_json({'status': 'error', 'message': f'文件不存在: {local_path}'}, 400)
                    return

            # 3. 从 Civitai URL 获取模型信息
            try:
                dl = CivitaiDownloader()
                parse_result = dl.parse_url(civitai_url)
                if parse_result['status'] != 'ok':
                    self.send_json({'status': 'error', 'message': f'URL 解析失败: {parse_result.get("message")}'}, 400)
                    return
                model_info = dl.get_model_info(parse_result['model_id'], parse_result.get('version_id'))
                if model_info['status'] != 'ok':
                    self.send_json({'status': 'error', 'message': f'获取模型信息失败: {model_info.get("message")}'})
                    return
            except Exception as e:
                self.send_json({'status': 'error', 'message': f'Civitai API 错误: {e}'})
                return

            api_title = model_info.get('title', 'Unknown')
            api_version_name = model_info.get('version_name', 'v1')
            api_file_name = model_info.get('file_name', '')
            api_model_id = str(model_info.get('model_id', parse_result['model_id']))
            api_version_id = str(model_info.get('version_id', ''))
            api_base_model = model_info.get('base_model', '')
            api_trained_words = model_info.get('trained_words', [])

            # 4. 标准重命名: {title}_{version}_{apiFileName}.ext
            model_ext = os.path.splitext(local_filename)[1]
            file_stem = api_file_name
            if file_stem.lower().endswith(model_ext.lower()):
                file_stem = file_stem[:-len(model_ext)]

            def _clean(name):
                return _re.sub(r'[<>:"/\\|?*]', '_', name).strip() if name else ''

            new_base = _clean(f"{api_title}_{api_version_name}_{file_stem}")
            new_filename = f"{new_base}{model_ext}"
            new_path = os.path.join(target_dir, new_filename)

            renamed = False
            if os.path.abspath(local_path) != os.path.abspath(new_path):
                if os.path.exists(new_path):
                    # 目标已存在，不覆盖，使用原文件名
                    new_filename = local_filename
                    new_path = local_path
                else:
                    try:
                        os.rename(local_path, new_path)
                        renamed = True
                    except Exception as e:
                        # 重命名失败，使用原文件名
                        new_filename = local_filename
                        new_path = local_path

            # 5. 写 .txt 元数据
            txt_path = os.path.join(target_dir, f"{os.path.splitext(new_filename)[0]}.txt")
            try:
                from datetime import datetime
                txt_content = (
                    f"URL: https://civitai.com/models/{api_model_id}?versionId={api_version_id}\n\n"
                    f"Title: {api_title}\n\n"
                    f"Base Model: {api_base_model}\n\n"
                    f"Version: {api_version_name}\n\n"
                    f"File: {api_file_name}\n\n"
                    f"Registered: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                if alias_name:
                    txt_content += f"\nAlias: {alias_name}\n"
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(txt_content)
            except Exception:
                pass

            # 6. 注册模型索引（真实 version_id）
            abs_path = os.path.abspath(new_path)
            model_index.upsert(
                model_id=api_model_id, model_name=api_title,
                version_id=api_version_id, version_name=api_version_name,
                filename=new_filename, path=abs_path,
                trigger_words=api_trained_words,
            )

            # 7. 注册别名 version_id（让旧图引用能找到此文件）
            alias_vid = ''
            if alias_name:
                vid_match = _re.search(r'(\d+)', alias_name)
                if vid_match:
                    alias_vid = vid_match.group(1)
                    model_index.upsert(
                        model_id=api_model_id, model_name=api_title,
                        version_id=alias_vid, version_name=f"alias:{alias_name}",
                        filename=new_filename, path=abs_path,
                        trigger_words=api_trained_words,
                    )

            # 8. 刷新模型缓存
            try:
                model_cache.refresh_all()
            except Exception:
                pass

            self.send_json({
                'status': 'ok',
                'message': f'注册成功: {api_title} / {api_version_name}',
                'renamed': renamed,
                'new_filename': new_filename,
                'model_id': api_model_id,
                'version_id': api_version_id,
                'alias_version_id': alias_vid,
                'trained_words': api_trained_words,
                'base_model': api_base_model,
            })

        elif path == '/api/admin/model-index/upsert':
            # 新增或更新模型索引条目
            from util import model_index
            model_id = str(data.get('model_id', '')).strip()
            model_name = data.get('model_name', '').strip()
            version_id = str(data.get('version_id', '')).strip()
            version_name = data.get('version_name', '').strip()
            filename = data.get('filename', '').strip()
            filepath = data.get('path', '').strip()
            trigger_words = data.get('trigger_words', [])
            if not model_id or not version_id:
                self.send_json({'status': 'error', 'message': '缺少 model_id 或 version_id'}, 400)
                return
            if not filepath:
                self.send_json({'status': 'error', 'message': '缺少文件路径 path'}, 400)
                return
            model_index.upsert(
                model_id=model_id, model_name=model_name,
                version_id=version_id, version_name=version_name,
                filename=filename or os.path.basename(filepath),
                path=filepath, trigger_words=trigger_words
            )
            self.send_json({'status': 'ok', 'message': f'已保存: {model_name} / {version_name}'})

        elif path == '/api/admin/model-index/delete':
            # 删除模型索引中的某个版本
            from util import model_index
            model_id = str(data.get('model_id', '')).strip()
            version_id = str(data.get('version_id', '')).strip()
            if not model_id or not version_id:
                self.send_json({'status': 'error', 'message': '缺少 model_id 或 version_id'}, 400)
                return
            model_index.remove_version(model_id, version_id)
            self.send_json({'status': 'ok', 'message': f'已删除版本 {version_id}'})

        elif path == '/api/prompt/edit':
            # AI 编辑提示词：根据用户指令精确修改 prompt
            prompt = data.get('prompt', '')
            instruction = data.get('instruction', '')
            if not prompt or not instruction:
                self.send_json({'status': 'error', 'message': '缺少 prompt 或 instruction 参数'}, 400)
                return
            try:
                from llm.llm_client import chat
                system_msg = (
                    "You are an expert Stable Diffusion prompt editor with deep knowledge of "
                    "danbooru, e621, and other popular SD tag systems.\n\n"
                    "RULES:\n"
                    "1. Follow the user's editing instruction precisely. Only modify what is requested.\n"
                    "2. DO NOT alter, reorder, add, or remove any tags outside the scope of the instruction.\n"
                    "3. Preserve the original structure, order, comma-separated format, and (weight:1.2) syntax exactly.\n"
                    "4. ALL output tags MUST be in English using standard danbooru/SD tag conventions "
                    "(e.g. snake_case like 'long_hair', 'aqua_eyes', 'hatsune_miku').\n"
                    "5. If the instruction mentions a character name (e.g. 初音未来, Hatsune Miku), "
                    "replace it with the correct danbooru character tag AND add key visual traits "
                    "(hair color/style, eye color, iconic outfit/accessories) as separate tags.\n"
                    "6. If there are non-English tags (Chinese/Japanese/Korean) in the existing prompt "
                    "that fall within the scope of the instruction, convert them to proper danbooru English tags.\n"
                    "7. Output ONLY the edited prompt text. No explanations, no quotes, no markdown, no commentary."
                )
                user_msg = f"=== CURRENT PROMPT ===\n{prompt}\n\n=== INSTRUCTION ===\n{instruction}"
                edited = chat(
                    [{"role": "system", "content": system_msg},
                     {"role": "user", "content": user_msg}],
                    temperature=0.3, max_tokens=2048
                )
                self.send_json({'status': 'ok', 'edited_prompt': edited.strip()})
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)

        elif path == '/api/azure/delete':
            # 删除指定的 blob（同时删除本地 OUTPUT_DIR 中的同名文件）
            blob_path = data.get('path', '')
            
            if not blob_path:
                self.send_json({'success': False, 'error': '缺少 path 参数'}, 400)
                return
            
            # 安全检查：防止路径遍历
            if '..' in blob_path or blob_path.startswith('/'):
                self.send_json({'success': False, 'error': '非法的 blob 路径'}, 400)
                return
            
            try:
                blob = BlobStorage(container='civitaidl')
                # delete(subfolder, filename) 格式，需要拆分
                if '/' in blob_path:
                    subfolder = blob_path.rsplit('/', 1)[0]
                    filename = blob_path.rsplit('/', 1)[1]
                else:
                    subfolder = None
                    filename = blob_path
                success = blob.delete(subfolder, filename)

                # 同步删除本地 OUTPUT_DIR 中的同名文件
                local_deleted = False
                local_error = ''
                if filename:
                    local_path = os.path.join(OUTPUT_DIR, filename)
                    if os.path.isfile(local_path):
                        try:
                            os.remove(local_path)
                            local_deleted = True
                            print(f"[Delete] 本地文件已删除: {local_path}")
                        except Exception as le:
                            local_error = str(le)
                            print(f"[Delete] ⚠️ 本地文件删除失败: {le}")

                if success:
                    msg = f'已删除: {blob_path}'
                    if local_deleted:
                        msg += '（含本地文件）'
                    elif local_error:
                        msg += f'（本地文件删除失败: {local_error}）'
                    self.send_json({'success': True, 'message': msg})
                else:
                    # Azure 删除失败，但本地文件可能已删除，仍告知结果
                    self.send_json({'success': False, 'error': '文件不存在或删除失败',
                                    'local_deleted': local_deleted}, 404)
            except Exception as e:
                self.send_json({'success': False, 'error': str(e)}, 500)
        
        else:
            self.send_json({'error': 'Not Found'}, 404)


def run_server():
    """启动 API 服务器"""
    # 启动时自动同步本地 ↔ Azure 数据
    try:
        from data_sync import sync_all
        sync_all()
    except Exception as e:
        print(f"[Startup] 数据同步失败（不影响启动）: {e}")

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadingHTTPServer(('0.0.0.0', SERVER_PORT), APIHandler)
    print(f"========================================")
    print(f"Civitai Downloader + ComfyUI API Server")
    print(f"========================================")
    print(f"Port: {SERVER_PORT}")
    print(f"ComfyUI: {COMFYUI_URL}")
    print(f"Workflow Dir: {WORKFLOW_DIR}")
    print(f"========================================")
    print(f"Endpoints:")
    print(f"  GET  /api/health                          - Health check")
    print(f"  GET  /api/workflows                       - List workflows")
    print(f"  GET  /api/subtypes?type=ckpt|lora         - D: drive subtypes")
    print(f"  GET  /api/files?type=ckpt&subtype=xl      - Files in subfolder")
    print(f"  GET  /api/comfyui/queue                   - ComfyUI status")
    print(f"  POST /api/download                        - Download from Civitai")
    print(f"  POST /api/image/parse                     - Parse Civitai image URL")
    print(f"  POST /api/workflow/run                    - Run ComfyUI workflow")
    print(f"  POST /api/cache/refresh                   - Refresh cache")
    print(f"  GET  /api/azure/list                      - List recent Azure blobs")
    print(f"  POST /api/azure/delete                    - Delete Azure blob")
    print(f"  POST /api/favorite/add                   - Add favorite image URL")
    print(f"  GET  /api/favorite/status                - Queue status")
    print(f"  GET  /api/favorite/consume                - Consume one URL")
    print(f"  POST /api/favorite/cleanup                - Cleanup done items")
    print(f"========================================")
    
    server.serve_forever()


if __name__ == '__main__':
    run_server()
