# -*- coding: utf-8 -*-
"""
discard_log.py — 废弃生成记录

记录被用户手动删除（非标记失败）的生成图的参数指纹，
防止相同参数在自动复刻时重复生成。

数据结构（cache/discard_log.json）:
{
  "<fav_id>": [
    {
      "fingerprint": "<md5>",
      "params_summary": "ckpt=xxx, loras=[...], 1024x1024, steps=20, ...",
      "deleted_at": "2026-02-18T12:00:00"
    },
    ...
  ],
  ...
}
"""

import os
import json
import hashlib
import threading
import datetime

_LOCK = threading.Lock()
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'discard_log.json')


# ============ 内部 IO ============

def _read() -> dict:
    try:
        with open(_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _write(data: dict):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ 指纹计算 ============

def compute_fingerprint(params: dict) -> str:
    """
    计算参数指纹（MD5）。
    params 应包含：checkpoint, loras, prompt, negative_prompt,
                   width, height, steps, cfg, sampler, scheduler, seed
    loras 为 list of {name, weight}，按 name 排序后参与计算。
    """
    loras = sorted(params.get('loras') or [], key=lambda x: x.get('name', ''))
    loras_key = [(l.get('name', ''), round(float(l.get('weight', 1.0)), 3)) for l in loras]

    key_obj = {
        'checkpoint': (params.get('checkpoint') or '').strip(),
        'loras': loras_key,
        'prompt': (params.get('prompt') or '').strip(),
        'negative_prompt': (params.get('negative_prompt') or '').strip(),
        'width': int(params.get('width') or 0),
        'height': int(params.get('height') or 0),
        'steps': int(params.get('steps') or 0),
        'cfg': round(float(params.get('cfg') or 0), 2),
        'sampler': (params.get('sampler') or '').strip(),
        'scheduler': (params.get('scheduler') or '').strip(),
        'seed': int(params.get('seed') if params.get('seed') is not None else -1),
    }
    raw = json.dumps(key_obj, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def params_summary(params: dict) -> str:
    """生成人类可读的参数摘要"""
    loras = sorted(params.get('loras') or [], key=lambda x: x.get('name', ''))
    lora_str = ', '.join(f"{l.get('name','?')}@{round(float(l.get('weight',1.0)),2)}" for l in loras) or '无'
    return (
        f"ckpt={params.get('checkpoint','?')}, "
        f"loras=[{lora_str}], "
        f"{params.get('width','?')}x{params.get('height','?')}, "
        f"steps={params.get('steps','?')}, cfg={params.get('cfg','?')}, "
        f"sampler={params.get('sampler','?')}, seed={params.get('seed','?')}"
    )


# ============ 公开 API ============

def add_entry(fav_id: str, params: dict) -> dict:
    """
    添加一条废弃记录。
    返回 {"status": "ok", "fingerprint": "..."}
    """
    if not fav_id:
        return {"status": "error", "message": "fav_id 不能为空"}
    fp = compute_fingerprint(params)
    summary = params_summary(params)
    now = datetime.datetime.now().isoformat(timespec='seconds')

    with _LOCK:
        data = _read()
        entries = data.setdefault(fav_id, [])
        # 去重：同一指纹不重复记录
        if not any(e.get('fingerprint') == fp for e in entries):
            entries.append({
                'fingerprint': fp,
                'params_summary': summary,
                'deleted_at': now,
            })
            _write(data)
    return {"status": "ok", "fingerprint": fp}


def check_fingerprint(fav_id: str, fingerprint: str) -> bool:
    """检查某 fav_id 下是否存在该指纹"""
    with _LOCK:
        data = _read()
    entries = data.get(fav_id, [])
    return any(e.get('fingerprint') == fingerprint for e in entries)


def check_params(fav_id: str, params: dict) -> dict:
    """
    检查参数是否已被废弃。
    返回 {"found": bool, "fingerprint": str, "entry": dict|None}
    """
    fp = compute_fingerprint(params)
    with _LOCK:
        data = _read()
    entries = data.get(fav_id, [])
    for e in entries:
        if e.get('fingerprint') == fp:
            return {"found": True, "fingerprint": fp, "entry": e}
    return {"found": False, "fingerprint": fp, "entry": None}


def get_entries(fav_id: str) -> list:
    """返回某 fav_id 的所有废弃记录"""
    with _LOCK:
        data = _read()
    return data.get(fav_id, [])


def clear_fav(fav_id: str) -> dict:
    """
    清除某 fav_id 的所有废弃记录（美学分析完成后调用）。
    返回 {"status": "ok", "cleared": N}
    """
    with _LOCK:
        data = _read()
        entries = data.pop(fav_id, [])
        if entries:
            _write(data)
    return {"status": "ok", "cleared": len(entries)}
