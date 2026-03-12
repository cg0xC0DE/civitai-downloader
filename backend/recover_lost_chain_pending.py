#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恢复“已画过但被丢弃”的待处理收藏：
- 条件：gen_tracking 记录为 pending_review，且无 blob_urls/local_paths
- 操作：将对应 favorite_id 状态重置为 pending

默认仅处理当天 created_at 记录。

用法：
  python recover_lost_chain_pending.py
  python recover_lost_chain_pending.py --day 2026-02-22
  python recover_lost_chain_pending.py --all-days
  python recover_lost_chain_pending.py --dry
"""

import os
import sys
import json
import time
import argparse
from collections import Counter
from datetime import datetime


BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKING_PATH = os.path.join(BACKEND_DIR, 'cache', 'gen_tracking.json')
REPORT_DIR = os.path.join(BACKEND_DIR, 'cache', 'recovery_reports')

sys.path.insert(0, BACKEND_DIR)
from favorite_images import list_all, update_status  # noqa: E402


def _pick_day(args):
    if args.all_days:
        return None
    if args.day:
        return args.day.strip()
    return datetime.now().strftime('%Y-%m-%d')


def _load_tracking():
    if not os.path.exists(TRACKING_PATH):
        raise FileNotFoundError(f'gen_tracking 不存在: {TRACKING_PATH}')
    with open(TRACKING_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _has_images(rec):
    return bool(rec.get('blob_urls')) or bool(rec.get('local_paths'))


def _has_reviewed_status(rec):
    image_statuses = rec.get('image_statuses') or {}
    if not isinstance(image_statuses, dict):
        return False
    return any(v in ('perfect', 'done') for v in image_statuses.values())


def _build_favorite_summary(tracking):
    summary = {}
    for _, rec in tracking.items():
        if not isinstance(rec, dict):
            continue
        fav_id = (rec.get('favorite_id') or '').strip()
        if not fav_id:
            continue
        item = summary.setdefault(fav_id, {
            'has_images': False,
            'has_reviewed': False,
            'records': 0,
            'created_at_list': [],
        })
        item['records'] += 1
        created_at = (rec.get('created_at') or '').strip()
        if created_at:
            item['created_at_list'].append(created_at)
        if _has_images(rec):
            item['has_images'] = True
        if _has_reviewed_status(rec):
            item['has_reviewed'] = True
    return summary


def _collect_candidates(tracking, day):
    raw_items = []
    raw_fav_ids = []
    fav_summary = _build_favorite_summary(tracking)

    for batch_id, rec in tracking.items():
        if not isinstance(rec, dict):
            continue

        status = (rec.get('status') or '').strip()
        if status != 'pending_review':
            continue

        created_at = (rec.get('created_at') or '').strip()
        if day and not created_at.startswith(day):
            continue

        if _has_images(rec):
            continue

        fav_id = (rec.get('favorite_id') or '').strip()
        if not fav_id:
            continue

        raw_items.append({
            'created_at': created_at,
            'batch_id': batch_id,
            'favorite_id': fav_id,
            'source_url': rec.get('source_url') or '',
        })
        raw_fav_ids.append(fav_id)

    recover_items = []
    recover_fav_ids = []
    skipped_has_images = set()
    skipped_reviewed = set()
    seen_recover = set()

    for it in raw_items:
        fid = it['favorite_id']
        s = fav_summary.get(fid, {})
        if s.get('has_reviewed'):
            skipped_reviewed.add(fid)
            continue
        if s.get('has_images'):
            skipped_has_images.add(fid)
            continue
        recover_items.append(it)
        if fid not in seen_recover:
            recover_fav_ids.append(fid)
            seen_recover.add(fid)

    return {
        'raw_items': raw_items,
        'raw_fav_ids': sorted(set(raw_fav_ids)),
        'recover_items': recover_items,
        'recover_fav_ids': sorted(set(recover_fav_ids)),
        'skipped_has_images_fav_ids': sorted(skipped_has_images),
        'skipped_reviewed_fav_ids': sorted(skipped_reviewed),
        'fav_summary': fav_summary,
    }


def _status_snapshot(ids):
    entries = list_all()
    by_id = {e.get('id'): e for e in entries if e.get('id')}
    return Counter((by_id.get(fid) or {}).get('status', 'missing') for fid in ids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--day', default='', help='仅处理指定日期，格式 YYYY-MM-DD')
    parser.add_argument('--all-days', action='store_true', help='处理所有日期')
    parser.add_argument('--dry', action='store_true', help='仅预览，不写入')
    parser.add_argument('--repair-overreset', action='store_true', help='修复历史误重置：将错误 pending 改回 processing')
    args = parser.parse_args()

    day = _pick_day(args)
    tracking = _load_tracking()
    picked = _collect_candidates(tracking, day)
    items = picked['recover_items']
    fav_ids = picked['recover_fav_ids']
    raw_items = picked['raw_items']
    raw_fav_ids = picked['raw_fav_ids']
    skipped_has_images_fav_ids = picked['skipped_has_images_fav_ids']
    skipped_reviewed_fav_ids = picked['skipped_reviewed_fav_ids']
    fav_summary = picked['fav_summary']

    before = _status_snapshot(fav_ids)
    ok = 0
    errors = []

    if not args.dry:
        for fid in fav_ids:
            try:
                r = update_status(fid, 'pending')
                if r.get('status') == 'ok':
                    ok += 1
                else:
                    errors.append({'id': fid, 'error': r.get('message', 'unknown')})
            except Exception as e:
                errors.append({'id': fid, 'error': str(e)})
    else:
        ok = len(fav_ids)

    after = _status_snapshot(fav_ids) if not args.dry else before

    repaired_overreset = 0
    overreset_errors = []
    overreset_candidates = []
    if args.repair_overreset:
        entries = list_all()
        overreset_target_ids = set()
        for fid in raw_fav_ids:
            s = fav_summary.get(fid, {})
            if s.get('has_images') or s.get('has_reviewed'):
                overreset_target_ids.add(fid)

        for e in entries:
            fid = (e.get('id') or '').strip()
            if not fid or fid not in overreset_target_ids:
                continue
            if e.get('status') != 'pending':
                continue
            if e.get('retry_reason'):
                continue
            overreset_candidates.append(fid)

        if not args.dry:
            for fid in sorted(set(overreset_candidates)):
                try:
                    r = update_status(fid, 'processing')
                    if r.get('status') == 'ok':
                        repaired_overreset += 1
                    else:
                        overreset_errors.append({'id': fid, 'error': r.get('message', 'unknown')})
                except Exception as e:
                    overreset_errors.append({'id': fid, 'error': str(e)})
        else:
            repaired_overreset = len(set(overreset_candidates))

    report = {
        'day': day or 'ALL',
        'dry_run': bool(args.dry),
        'raw_candidate_batches': len(raw_items),
        'raw_candidate_favorites': len(raw_fav_ids),
        'skipped_has_images_favorites': len(skipped_has_images_fav_ids),
        'skipped_reviewed_favorites': len(skipped_reviewed_fav_ids),
        'candidate_batches': len(items),
        'candidate_favorites': len(fav_ids),
        'updated_ok': ok,
        'update_errors': len(errors),
        'status_before': dict(before),
        'status_after': dict(after),
        'repair_overreset': bool(args.repair_overreset),
        'repaired_overreset': repaired_overreset,
        'repair_overreset_errors': len(overreset_errors),
        'repair_overreset_error_items': overreset_errors,
        'errors': errors,
        'items': items,
        'skipped_has_images_fav_ids': skipped_has_images_fav_ids,
        'skipped_reviewed_fav_ids': skipped_reviewed_fav_ids,
    }

    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    out_name = f"lost_chain_reset_local_{'dry_' if args.dry else ''}{ts}.json"
    out_path = os.path.join(REPORT_DIR, out_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"day={report['day']}")
    print(f"candidate_batches={report['candidate_batches']}")
    print(f"candidate_favorites={report['candidate_favorites']}")
    print(f"skipped_has_images_favorites={report['skipped_has_images_favorites']}")
    print(f"skipped_reviewed_favorites={report['skipped_reviewed_favorites']}")
    print(f"updated_ok={report['updated_ok']}")
    print(f"update_errors={report['update_errors']}")
    if args.repair_overreset:
        print(f"repaired_overreset={report['repaired_overreset']}")
        print(f"repair_overreset_errors={report['repair_overreset_errors']}")
    print(f"report={out_path}")


if __name__ == '__main__':
    main()
