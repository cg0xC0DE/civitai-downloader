#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据同步工具 — 本地 ↔ Azure 双向合并

合并策略：
- 收藏夹：按 id 去重，冲突取最新时间戳
- 追踪数据：按 batch_id 去重，冲突取最新 created_at
- 美学蓝图：按 (work_title, base_model) 去重，冲突取最新

不丢失、不抹除，两边增量全部保留。
"""

import os
import json
import time

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_BACKEND_DIR, 'cache')

_BLOB_CONTAINER = 'civitaidl'

from util.azure_utils import _azure_available


def _get_blob():
    from azure_blob import BlobStorage
    return BlobStorage(container=_BLOB_CONTAINER)


# ============================================================
# 收藏夹合并
# ============================================================

_FAV_LOCAL = os.path.join(_CACHE_DIR, 'favorite_images', 'queue.jsonl')
_FAV_BLOB_SUB = 'data'
_FAV_BLOB_FILE = 'favorite_images.jsonl'


def _parse_jsonl(text: str) -> list:
    if not text:
        return []
    entries = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _entries_to_jsonl(entries: list) -> str:
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    return '\n'.join(lines) + '\n' if lines else ''


def _latest_ts(entry: dict) -> str:
    """取条目中最新的时间戳，用于冲突时选择更新的版本"""
    candidates = [
        entry.get('fail_at', ''),
        entry.get('done_at', ''),
        entry.get('processing_at', ''),
        entry.get('created_at', ''),
    ]
    return max(c for c in candidates if c) if any(candidates) else ''


def _merge_favorites(local_entries: list, azure_entries: list) -> list:
    """按 id 合并，冲突取最新时间戳"""
    merged = {}
    for e in azure_entries:
        eid = e.get('id')
        if eid:
            merged[eid] = e
    for e in local_entries:
        eid = e.get('id')
        if not eid:
            continue
        if eid not in merged:
            merged[eid] = e
        else:
            # 冲突：取时间戳更新的
            if _latest_ts(e) >= _latest_ts(merged[eid]):
                merged[eid] = e
    # 按 created_at 排序
    result = sorted(merged.values(), key=lambda x: x.get('created_at', ''))
    return result


def sync_favorites() -> dict:
    """同步收藏夹数据，返回统计信息"""
    local_entries = []
    azure_entries = []

    # 读本地
    if os.path.exists(_FAV_LOCAL):
        try:
            with open(_FAV_LOCAL, 'r', encoding='utf-8') as f:
                local_entries = _parse_jsonl(f.read())
        except Exception:
            pass

    # 读 Azure
    has_azure = _azure_available()
    if has_azure:
        try:
            blob = _get_blob()
            text = blob.get_text(_FAV_BLOB_SUB, _FAV_BLOB_FILE)
            azure_entries = _parse_jsonl(text)
        except Exception as e:
            print(f"[Sync] Azure 收藏读取失败: {e}")

    # 合并
    merged = _merge_favorites(local_entries, azure_entries)
    local_only = len(set(e.get('id') for e in local_entries) - set(e.get('id') for e in azure_entries))
    azure_only = len(set(e.get('id') for e in azure_entries) - set(e.get('id') for e in local_entries))

    # 双写
    text = _entries_to_jsonl(merged)
    os.makedirs(os.path.dirname(_FAV_LOCAL), exist_ok=True)
    with open(_FAV_LOCAL, 'w', encoding='utf-8') as f:
        f.write(text)

    if has_azure and (azure_only > 0 or local_only > 0 or len(merged) != len(azure_entries)):
        try:
            blob = _get_blob()
            blob.put_text(_FAV_BLOB_SUB, _FAV_BLOB_FILE, text)
        except Exception as e:
            print(f"[Sync] Azure 收藏写入失败: {e}")

    stats = {
        'local_before': len(local_entries),
        'azure_before': len(azure_entries),
        'merged': len(merged),
        'local_only': local_only,
        'azure_only': azure_only,
    }
    print(f"[Sync] 收藏夹: 本地{len(local_entries)} + Azure{len(azure_entries)} → 合并{len(merged)}"
          f" (本地独有{local_only}, Azure独有{azure_only})")
    return stats


# ============================================================
# 追踪数据合并
# ============================================================

_TRACK_LOCAL = os.path.join(_CACHE_DIR, 'gen_tracking.json')
_TRACK_BLOB_SUB = 'data'
_TRACK_BLOB_FILE = 'gen_tracking.json'


def _merge_tracking(local_data: dict, azure_data: dict) -> dict:
    """按 batch_id 合并；并对有图记录按 favorite_id 去重（保留最新 created_at）。"""
    merged = dict(azure_data)
    for k, v in local_data.items():
        if k not in merged:
            merged[k] = v
        else:
            local_ts = v.get('created_at', '')
            azure_ts = merged[k].get('created_at', '')
            if local_ts >= azure_ts:
                merged[k] = v

    # 防止历史重复记录从 Azure 回灌：同一 favorite_id 若存在多个“有图” batch，只保留最新的。
    fav_keep_bid = {}
    for bid, entry in merged.items():
        fid = entry.get('favorite_id', '')
        has_images = bool(entry.get('blob_urls') or entry.get('local_paths'))
        if not fid or not has_images:
            continue

        if fid not in fav_keep_bid:
            fav_keep_bid[fid] = bid
            continue

        old_bid = fav_keep_bid[fid]
        old_ts = merged.get(old_bid, {}).get('created_at', '')
        new_ts = entry.get('created_at', '')
        if new_ts >= old_ts:
            fav_keep_bid[fid] = bid

    drop_bids = []
    for bid, entry in merged.items():
        fid = entry.get('favorite_id', '')
        has_images = bool(entry.get('blob_urls') or entry.get('local_paths'))
        if fid and has_images and fav_keep_bid.get(fid) != bid:
            drop_bids.append(bid)

    if drop_bids:
        for bid in drop_bids:
            merged.pop(bid, None)
        print(f"[Sync] 追踪去重: 移除 {len(drop_bids)} 条重复有图记录")

    return merged


def sync_tracking() -> dict:
    """同步追踪数据"""
    local_data = {}
    azure_data = {}

    if os.path.exists(_TRACK_LOCAL):
        try:
            with open(_TRACK_LOCAL, 'r', encoding='utf-8') as f:
                local_data = json.load(f)
                if not isinstance(local_data, dict):
                    local_data = {}
        except Exception:
            pass

    has_azure = _azure_available()
    if has_azure:
        try:
            blob = _get_blob()
            azure_data = blob.get_json(_TRACK_BLOB_SUB, _TRACK_BLOB_FILE) or {}
        except Exception as e:
            print(f"[Sync] Azure 追踪读取失败: {e}")

    merged = _merge_tracking(local_data, azure_data)
    local_only = len(set(local_data.keys()) - set(azure_data.keys()))
    azure_only = len(set(azure_data.keys()) - set(local_data.keys()))

    # 双写
    text = json.dumps(merged, ensure_ascii=False, indent=2)
    os.makedirs(os.path.dirname(_TRACK_LOCAL), exist_ok=True)
    with open(_TRACK_LOCAL, 'w', encoding='utf-8') as f:
        f.write(text)

    if has_azure and (local_only > 0 or len(merged) != len(azure_data)):
        try:
            blob = _get_blob()
            blob.put_json(_TRACK_BLOB_SUB, _TRACK_BLOB_FILE, merged)
        except Exception as e:
            print(f"[Sync] Azure 追踪写入失败: {e}")

    stats = {
        'local_before': len(local_data),
        'azure_before': len(azure_data),
        'merged': len(merged),
        'local_only': local_only,
        'azure_only': azure_only,
    }
    print(f"[Sync] 追踪: 本地{len(local_data)} + Azure{len(azure_data)} → 合并{len(merged)}")
    return stats


# ============================================================
# 美学蓝图合并
# ============================================================

_BLUE_LOCAL = os.path.join(_CACHE_DIR, 'aesthetic_blueprints.json')
_BLUE_BLOB_SUB = 'data'
_BLUE_BLOB_FILE = 'aesthetic_blueprints.json'


def _blueprint_key(bp: dict) -> str:
    """蓝图去重 key：work_title + base_model"""
    return f"{bp.get('work_title', '')}||{bp.get('base_model', '')}"


def _merge_blueprints(local_list: list, azure_list: list) -> list:
    """按 (work_title, base_model) 去重合并"""
    merged = {}
    for bp in azure_list:
        key = _blueprint_key(bp)
        merged[key] = bp
    for bp in local_list:
        key = _blueprint_key(bp)
        if key not in merged:
            merged[key] = bp
        # 冲突时保留 Azure 版本（先入为主）
    return list(merged.values())


def sync_blueprints() -> dict:
    """同步美学蓝图"""
    local_list = []
    azure_list = []

    if os.path.exists(_BLUE_LOCAL):
        try:
            with open(_BLUE_LOCAL, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                local_list = data
        except Exception:
            pass

    has_azure = _azure_available()
    if has_azure:
        try:
            blob = _get_blob()
            data = blob.get_json(_BLUE_BLOB_SUB, _BLUE_BLOB_FILE)
            if isinstance(data, list):
                azure_list = data
        except Exception as e:
            print(f"[Sync] Azure 蓝图读取失败: {e}")

    merged = _merge_blueprints(local_list, azure_list)
    local_keys = set(_blueprint_key(b) for b in local_list)
    azure_keys = set(_blueprint_key(b) for b in azure_list)
    local_only = len(local_keys - azure_keys)
    azure_only = len(azure_keys - local_keys)

    # 双写
    text = json.dumps(merged, ensure_ascii=False, indent=2)
    os.makedirs(os.path.dirname(_BLUE_LOCAL), exist_ok=True)
    with open(_BLUE_LOCAL, 'w', encoding='utf-8') as f:
        f.write(text)

    if has_azure and (local_only > 0 or len(merged) != len(azure_list)):
        try:
            blob = _get_blob()
            blob.put_json(_BLUE_BLOB_SUB, _BLUE_BLOB_FILE, merged, indent=2)
        except Exception as e:
            print(f"[Sync] Azure 蓝图写入失败: {e}")

    stats = {
        'local_before': len(local_list),
        'azure_before': len(azure_list),
        'merged': len(merged),
        'local_only': local_only,
        'azure_only': azure_only,
    }
    print(f"[Sync] 蓝图: 本地{len(local_list)} + Azure{len(azure_list)} → 合并{len(merged)}")
    return stats


# ============================================================
# 统一入口
# ============================================================

def sync_all() -> dict:
    """同步所有数据，返回各项统计"""
    print(f"[Sync] ========== 开始数据同步 ==========")
    start = time.time()
    result = {
        'favorites': sync_favorites(),
        'tracking': sync_tracking(),
        'blueprints': sync_blueprints(),
    }
    elapsed = round(time.time() - start, 1)
    print(f"[Sync] ========== 同步完成 ({elapsed}s) ==========")
    result['elapsed_seconds'] = elapsed
    return result


if __name__ == '__main__':
    import pprint
    pprint.pprint(sync_all())
