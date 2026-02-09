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
    SERVER_PORT, WORKFLOW_DIR, COMFYUI_URL,
    scan_subtypes, scan_files, find_model_on_disk,
)
from cache_manager import model_cache
from civitaidl import CivitaiDownloader

# ============== 下载队列管理（同一时间只执行一个任务） ==============
import queue as _queue_mod

_download_tasks = {}  # task_id -> {status, phase, downloaded, total_size, percent, done, ...}
_tasks_lock = threading.Lock()
_download_queue = _queue_mod.Queue()

# ============== 生成任务跟踪 ==============
_gen_tasks = {}  # prompt_id -> {status, prompt_id, images_count, saved_paths, message, ...}
_gen_lock = threading.Lock()


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

        # 下载成功后自动刷新模型缓存
        if result.get('status') == 'ok':
            try:
                model_cache.refresh_all()
            except Exception:
                pass

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
        resp = sess.get(f'https://civitai.com/api/v1/model-versions/{version_id}',
                        params=params, timeout=15,
                        headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        data = resp.json()
        return {
            'name': data.get('model', {}).get('name', ''),
            'version_name': data.get('name', ''),
            'file_name': data.get('files', [{}])[0].get('name', '') if data.get('files') else '',
            'modelId': data.get('modelId') or data.get('model', {}).get('id'),
        }
    except Exception:
        return {'name': f'version_{version_id}', 'version_name': '', 'file_name': '', 'modelId': None}


def _resolve_model_by_hash(sess, hash_str, token):
    """通过文件 hash 查询模型信息"""
    try:
        url = f'https://civitai.com/api/v1/model-versions/by-hash/{hash_str}'
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
        }
    except Exception:
        return None


def parse_civitai_image(image_url):
    """Parse Civitai image URL to extract generation parameters and check D: drive"""
    import requests as _requests
    from config import CIVITAI_API_TOKEN

    match = re.search(r'civitai\.com/images/(\d+)', image_url)
    if not match:
        return {'status': 'error', 'message': '无法解析图片URL，请输入 civitai.com/images/xxxxx 格式'}

    image_id = int(match.group(1))
    sess = _requests.Session()
    sess.trust_env = False
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # 使用 TRPC image.getGenerationData 获取准确的生成参数
    try:
        import json as _json
        params = {'input': _json.dumps({'json': {'id': image_id}})}
        if CIVITAI_API_TOKEN:
            params['token'] = CIVITAI_API_TOKEN
        resp = sess.get('https://civitai.com/api/trpc/image.getGenerationData',
                        params=params, timeout=30, headers=headers)
        resp.raise_for_status()
        gen_data = resp.json().get('result', {}).get('data', {}).get('json', {})
    except Exception as e:
        return {'status': 'error', 'message': f'Civitai API 请求失败: {e}'}

    meta = gen_data.get('meta', {})
    if not meta:
        return {'status': 'error', 'message': '该图片没有生成参数信息'}

    # 基本参数
    result = {
        'status': 'success',
        'prompt': meta.get('prompt', ''),
        'negative_prompt': meta.get('negativePrompt', ''),
        'sampler': meta.get('sampler', 'dpmpp_2m'),
        'steps': meta.get('steps', 20),
        'cfg': meta.get('cfgScale', 7),
        'seed': meta.get('seed', -1),
        'width': meta.get('width', 1024),
        'height': meta.get('height', 1024),
        'clip_skip': meta.get('clipSkip'),
        'base_model': meta.get('baseModel', ''),
    }

    # 从 Size 字段补充尺寸（兼容旧格式）
    size_str = str(meta.get('Size', ''))
    if 'x' in size_str and not meta.get('width'):
        parts = size_str.split('x')
        try:
            result['width'] = int(parts[0])
            result['height'] = int(parts[1])
        except:
            pass

    # 解析 civitaiResources → 通过 modelVersionId 获取模型名
    civitai_resources = meta.get('civitaiResources', [])
    loras = []
    checkpoint = None
    checkpoint_alt = []
    checkpoint_version_id = None
    checkpoint_model_id = None

    for res in civitai_resources:
        rtype = res.get('type', '')
        version_id = res.get('modelVersionId')
        if not version_id:
            continue
        info = _resolve_model_version(sess, version_id, CIVITAI_API_TOKEN)
        if rtype == 'checkpoint':
            checkpoint = info.get('name', '')
            checkpoint_alt = [info.get('file_name', ''), info.get('version_name', '')]
            checkpoint_version_id = version_id
            checkpoint_model_id = info.get('modelId')
        elif rtype == 'lora':
            loras.append({
                'name': info.get('name', ''), 'weight': res.get('weight', 1.0),
                'alt_names': [info.get('file_name', ''), info.get('version_name', '')],
                'modelVersionId': version_id,
                'modelId': info.get('modelId')
            })

    # Fallback: 旧格式 — resources / Model / hashes 字段
    if not checkpoint and not loras:
        # Checkpoint: 从 resources(type=model) 或 Model 字段 + hash 反查
        old_resources = meta.get('resources', [])
        model_name = meta.get('Model', '')
        model_hash = meta.get('Model hash', '')

        # 先尝试从 resources 里找 checkpoint
        for res in old_resources:
            if res.get('type') in ('model', 'checkpoint'):
                model_name = model_name or res.get('name', '')
                model_hash = model_hash or res.get('hash', '')

        # 通过 hash 反查 Civitai 获取完整信息
        if model_hash:
            info = _resolve_model_by_hash(sess, model_hash, CIVITAI_API_TOKEN)
            if info:
                checkpoint = info.get('name', '') or model_name
                checkpoint_alt = [info.get('file_name', ''), info.get('version_name', ''), model_name]
                checkpoint_version_id = info.get('modelVersionId')
                checkpoint_model_id = info.get('modelId')
        if not checkpoint and model_name:
            checkpoint = model_name

        # LoRAs: 从 hashes 字段提取（key 格式 "LORA:filename.safetensors"）
        hashes = meta.get('hashes', {})
        for key, h in hashes.items():
            if not key.upper().startswith('LORA:'):
                continue
            lora_filename = key[5:]  # 去掉 "LORA:" 前缀
            lora_info = _resolve_model_by_hash(sess, h, CIVITAI_API_TOKEN)
            if lora_info:
                loras.append({
                    'name': lora_info.get('name', '') or lora_filename,
                    'weight': 1.0,
                    'alt_names': [lora_info.get('file_name', ''), lora_info.get('version_name', ''), lora_filename],
                    'modelVersionId': lora_info.get('modelVersionId'),
                    'modelId': lora_info.get('modelId')
                })
            else:
                loras.append({
                    'name': lora_filename, 'weight': 1.0,
                    'alt_names': [lora_filename],
                    'modelVersionId': None, 'modelId': None
                })

    result['checkpoint'] = checkpoint or ''
    result['loras'] = loras

    # Check D: drive for required models
    checks = {'checkpoint': None, 'loras': []}
    if checkpoint:
        checks['checkpoint'] = find_model_on_disk(checkpoint, 'ckpt', alt_names=checkpoint_alt)
        checks['checkpoint']['modelVersionId'] = checkpoint_version_id
        checks['checkpoint']['modelId'] = checkpoint_model_id
    for lora in loras:
        check = find_model_on_disk(lora['name'], 'lora', alt_names=lora.get('alt_names'))
        check['weight'] = lora.get('weight', 1.0)
        check['requested_name'] = lora['name']
        check['modelVersionId'] = lora.get('modelVersionId')
        check['modelId'] = lora.get('modelId')
        checks['loras'].append(check)
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
    result['all_models_found'] = all_found
    result['missing_models'] = missing

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


def get_images_ws(ws, prompt_id: str) -> list:
    """通过 WebSocket 获取图片数据"""
    images = []
    current_node = ""
    
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data.get('prompt_id') == prompt_id:
                    if data.get('node') is None:
                        break  # 执行完成
                    else:
                        current_node = data.get('node', '')
        else:
            # 图片数据（跳过前8字节的头），不限定节点 ID
            if current_node:
                images.append(out[8:])
    
    return images


def get_images_ws_batch(ws, prompt_ids: list) -> dict:
    """通过 WebSocket 获取多个任务的图片数据"""
    result = {pid: [] for pid in prompt_ids}
    current_node = ""
    current_prompt = ""
    completed = set()

    while len(completed) < len(prompt_ids):
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                pid = data.get('prompt_id')
                if pid in result:
                    if data.get('node') is None:
                        completed.add(pid)
                        current_node = ""
                        current_prompt = ""
                    else:
                        current_node = data.get('node', '')
                        current_prompt = pid
        else:
            if current_node and current_prompt in result:
                result[current_prompt].append(out[8:])

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
    """按 class_type 查找所有匹配节点，返回 [(nid, node), ...]"""
    results = []
    for nid, node in workflow.items():
        if isinstance(node, dict) and node.get('class_type') == class_type:
            results.append((nid, node))
    return results


def _set_lora_nodes(workflow: Dict, loras: list):
    """
    动态替换工作流中的 LoRA 节点。
    loras: [{"name": "subtype/filename.safetensors", "weight": 0.8}, ...]
    支持 LoraLoader（单 LoRA）和 Lora Loader Stack (rgthree)（多 LoRA）。
    """
    if not loras:
        return

    # Windows 路径分隔符
    normalized = []
    for l in loras:
        normalized.append({
            'name': l['name'].replace('/', '\\'),
            'weight': float(l.get('weight', 1.0))
        })

    # 1) 尝试 Lora Loader Stack (rgthree) —— 支持多 LoRA
    stack_nodes = _find_nodes_by_type(workflow, 'Lora Loader Stack (rgthree)')
    if stack_nodes:
        # 把所有 LoRA 填进第一个 Stack 节点，多余的 slot 设为 None
        node = stack_nodes[0][1]
        inp = node['inputs']
        # 先清空所有现有 lora slot（lora_01..lora_N, strength_01..strength_N）
        existing_lora_keys = sorted([k for k in inp if k.startswith('lora_')])
        existing_str_keys = sorted([k for k in inp if k.startswith('strength_')])
        max_slots = max(len(existing_lora_keys), len(normalized))
        for i in range(max_slots):
            idx = f"{i+1:02d}"
            lora_key = f"lora_{idx}"
            str_key = f"strength_{idx}"
            if i < len(normalized):
                inp[lora_key] = normalized[i]['name']
                inp[str_key] = normalized[i]['weight']
            else:
                inp[lora_key] = 'None'
                inp[str_key] = 1.0
        # 如果有多个 Stack 节点，其余的全部清空
        for _, extra_node in stack_nodes[1:]:
            for k in list(extra_node['inputs'].keys()):
                if k.startswith('lora_'):
                    extra_node['inputs'][k] = 'None'
                elif k.startswith('strength_'):
                    extra_node['inputs'][k] = 1.0
        return

    # 2) 尝试 LoraLoader（单 LoRA）
    lora_nodes = _find_nodes_by_type(workflow, 'LoraLoader')
    if lora_nodes and normalized:
        node = lora_nodes[0][1]
        node['inputs']['lora_name'] = normalized[0]['name']
        node['inputs']['strength_model'] = normalized[0]['weight']
        node['inputs']['strength_clip'] = normalized[0]['weight']
        return


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
    seed: int = None,
    loras: list = None,
    batch_size: int = 4,
    vary_sizes: bool = False
) -> Dict:
    """运行 ComfyUI 工作流（支持 UI 导出格式，自动转换为 API 格式）
    batch_size: 一次提交多少个请求，默认 4
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
            # ComfyUI checkpoint 列表是扁平文件名（无子目录前缀）
            ckpt_name = checkpoint.replace('/', '\\')
            # 去掉子目录前缀，只保留文件名
            if '\\' in ckpt_name:
                ckpt_name = ckpt_name.rsplit('\\', 1)[-1]
            ckpt_nodes[0][1]['inputs']['ckpt_name'] = ckpt_name

        # KSampler → 同时通过连接关系定位 positive/negative 节点
        sampler_nodes = _find_nodes_by_type(workflow, 'KSampler')
        positive_nid = negative_nid = None
        if sampler_nodes:
            s_inputs = sampler_nodes[0][1]['inputs']
            s_inputs['steps'] = steps
            s_inputs['cfg'] = cfg
            s_inputs['sampler_name'] = _normalize_sampler(sampler)
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
            size_nodes[0][1]['inputs']['width'] = width
            size_nodes[0][1]['inputs']['height'] = height

        # 5. 确保有 WebSocket 图片输出节点
        has_ws_output = bool(_find_nodes_by_type(workflow, 'SaveImageWebsocket'))
        if not has_ws_output:
            # 有 SaveImage → 替换为 SaveImageWebsocket
            save_nodes = _find_nodes_by_type(workflow, 'SaveImage')
            if save_nodes:
                nid = save_nodes[0][0]
                images_input = save_nodes[0][1]['inputs'].get('images')
                workflow[nid] = {
                    'class_type': 'SaveImageWebsocket',
                    'inputs': {'images': images_input} if images_input else {}
                }
                has_ws_output = True

        if not has_ws_output:
            # 没有 SaveImage → 找 VAEDecode 的输出，新增 SaveImageWebsocket
            vae_nodes = _find_nodes_by_type(workflow, 'VAEDecode')
            if vae_nodes:
                vae_nid = vae_nodes[0][0]
                new_id = str(max((int(k) for k in workflow if k.isdigit()), default=0) + 1)
                workflow[new_id] = {
                    'class_type': 'SaveImageWebsocket',
                    'inputs': {'images': [vae_nid, 0]}
                }
                has_ws_output = True

        if not has_ws_output:
            return {'status': 'error', 'message': '工作流缺少图片输出节点（SaveImage 或 VAEDecode）'}

        # 6. 连接 WebSocket
        client_id = str(uuid.uuid4())
        ws = websocket.WebSocket()
        ws.connect(f"ws://{COMFYUI_URL}/ws?clientId={client_id}")

        # 7. 批量提交（不同尺寸 + 随机 seed）
        import random as _random
        import copy as _copy

        # SDXL 标准直出尺寸
        _XL_LANDSCAPE = [(1152, 896), (1216, 832), (1344, 768), (1536, 640)]
        _XL_PORTRAIT  = [(896, 1152), (832, 1216), (768, 1344), (640, 1536)]

        batch_id = str(uuid.uuid4())[:8]
        prompt_ids = []
        sampler_nodes = _find_nodes_by_type(workflow, 'KSampler')
        size_nodes = _find_nodes_by_type(workflow, 'EmptyLatentImage')

        # 根据输入尺寸判断横竖，选对应的标准尺寸列表
        if vary_sizes and batch_size <= 4:
            if width > height:
                batch_sizes_list = _XL_LANDSCAPE[:batch_size]
            else:
                batch_sizes_list = _XL_PORTRAIT[:batch_size]
        else:
            batch_sizes_list = [(width, height)] * batch_size

        for idx in range(batch_size):
            # 每次提交前修改 seed
            if sampler_nodes:
                sampler_nodes[0][1]['inputs']['seed'] = _random.randint(0, 2**32 - 1)
            # 修改尺寸
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
            print(f"[ComfyUI] 批次 {batch_id} 第{idx+1}/{batch_size}个已提交: {result['prompt_id']} ({bw}x{bh})")

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
                '_start_time': time.time()
            }

        # 8. 后台线程等待全部完成并保存
        def _wait_and_save_batch():
            try:
                all_images = get_images_ws_batch(ws, prompt_ids)
                ws.close()

                output_dir = os.path.join(os.path.dirname(__file__), 'output')
                os.makedirs(output_dir, exist_ok=True)
                saved_paths = []
                total_count = 0
                for pid in prompt_ids:
                    imgs = all_images.get(pid, [])
                    for i, img_data in enumerate(imgs):
                        fname = f"{batch_id}_{pid[:8]}_{i}.png"
                        fpath = os.path.join(output_dir, fname)
                        with open(fpath, 'wb') as fout:
                            fout.write(img_data)
                        saved_paths.append(fpath)
                        total_count += 1
                        print(f"[ComfyUI] 图片已保存: {fpath}")

                with _gen_lock:
                    _gen_tasks[batch_id].update({
                        'status': 'success',
                        'completed': len(prompt_ids),
                        'images_count': total_count,
                        'saved_paths': saved_paths,
                        'message': f'批次完成，共 {total_count} 张图片已保存'
                    })
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

        elif path == '/api/comfyui/queue':
            ready, data = wait_for_comfyui(timeout=10)
            if ready:
                self.send_json({'status': 'success', 'queue': data})
            else:
                self.send_json({'status': 'error', 'message': 'ComfyUI not ready'})
        
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
                seed=data.get('seed'),
                loras=data.get('loras'),
                batch_size=data.get('batch_size', 4),
                vary_sizes=data.get('vary_sizes', False)
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
            # 重启 ComfyUI：杀掉进程，watchdog 会自动重启
            import subprocess as _subprocess
            try:
                # 找到占用 8188 端口的进程并杀掉
                kill_result = _subprocess.run(
                    ['powershell', '-Command',
                     "Get-NetTCPConnection -LocalPort 8188 -ErrorAction SilentlyContinue | "
                     "Select-Object -ExpandProperty OwningProcess -Unique | "
                     "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"],
                    capture_output=True, text=True, timeout=10
                )
                print(f"[ComfyUI] 重启请求：已终止 ComfyUI 进程，等待 watchdog 重启...")
                self.send_json({
                    'status': 'success',
                    'message': '已终止 ComfyUI 进程，watchdog 将自动重启（约10-30秒）'
                })
            except Exception as e:
                self.send_json({'status': 'error', 'message': f'重启失败: {str(e)}'}, 500)

        elif path == '/api/cache/refresh':
            model_cache.refresh_all()
            self.send_json({'status': 'success', 'message': 'Cache refreshed'})
        
        else:
            self.send_json({'error': 'Not Found'}, 404)


def run_server():
    """启动 API 服务器"""
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
    print(f"========================================")
    
    server.serve_forever()


if __name__ == '__main__':
    run_server()
