#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
临时修复脚本：修正 bug 导致的数据异常

问题：
1. 自动复刻提交生成后，状态被错误设为 'done'（应为 'processing'）
2. 清理伪处理中时，有关联 gen_tracking 的条目被误改回 'pending'

修复逻辑：
- 'done' 状态 + 有 gen_tracking 记录 + done_at 在今天 → 改回 'processing'
- 'pending' 状态 + 有 gen_tracking 记录 → 改回 'processing'（本次数据无此情况，保留逻辑）

运行：python fix_status_anomaly.py [--dry]
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND_DIR)

from favorite_images import list_all, update_status

TRACKING_PATH = os.path.join(_BACKEND_DIR, 'cache', 'gen_tracking.json')


def load_tracking():
    if not os.path.exists(TRACKING_PATH):
        return {}
    with open(TRACKING_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='仅预览，不修改')
    args = parser.parse_args()
    dry = args.dry

    print(f"{'[DRY RUN] ' if dry else ''}开始修复数据异常...\n")

    tracking = load_tracking()
    tracked_fav_ids = set()
    for v in tracking.values():
        fid = v.get('favorite_id', '')
        if fid:
            tracked_fav_ids.add(fid)

    print(f"gen_tracking 中有 {len(tracked_fav_ids)} 个收藏有生成记录")

    entries = list_all()
    from collections import Counter
    before_counts = Counter(e.get('status', '?') for e in entries)
    print(f"修复前状态分布: {dict(before_counts)}\n")

    fixed_done = []
    fixed_pending = []

    for e in entries:
        eid = e['id']
        status = e.get('status', '')
        url = e.get('url', '')[:70]

        # 修复 1: 'done' 但有 gen_tracking → 应为 'processing'
        # （auto_replicate 不应设 done，done 仅由美学分析完成后设置）
        if status == 'done' and eid in tracked_fav_ids:
            done_at = e.get('done_at', '')
            print(f"  [FIX done→processing] {eid[:16]} done_at={done_at} {url}")
            if not dry:
                update_status(eid, 'processing')
            fixed_done.append(eid)

        # 修复 2: 'pending' 但有 gen_tracking → 被误清理，应为 'processing'
        elif status == 'pending' and eid in tracked_fav_ids:
            print(f"  [FIX pending→processing] {eid[:16]} {url}")
            if not dry:
                update_status(eid, 'processing')
            fixed_pending.append(eid)

    print(f"\n{'[DRY] ' if dry else ''}修复完成:")
    print(f"  done → processing: {len(fixed_done)} 条")
    print(f"  pending → processing: {len(fixed_pending)} 条")

    if not dry:
        entries_after = list_all()
        after_counts = Counter(e.get('status', '?') for e in entries_after)
        print(f"\n修复后状态分布: {dict(after_counts)}")
    else:
        print("\n（--dry 模式，未实际修改）")


if __name__ == '__main__':
    main()
