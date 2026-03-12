#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性临时脚本：清理本地 OUTPUT_DIR 中已从 Azure 删除的孤立文件。

逻辑：
  1. 列出 Azure generated/ 前缀下所有 blob 的文件名（不含路径）
  2. 列出本地 OUTPUT_DIR 中所有 .png 文件
  3. 本地有、Azure 没有的 → 孤立文件 → 删除

运行方式：
  cd backend
  python cleanup_orphan_outputs.py [--dry-run]
"""

import os
import sys

# 确保能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import OUTPUT_DIR

DRY_RUN = '--dry-run' in sys.argv


def main():
    print(f"[Cleanup] OUTPUT_DIR = {OUTPUT_DIR}")
    print(f"[Cleanup] 模式 = {'DRY RUN（不实际删除）' if DRY_RUN else '实际删除'}")
    print()

    # ---- 1. 获取 Azure 上现存的文件名集合 ----
    try:
        from azure_blob.blob_storage import BlobStorage
        blob = BlobStorage(container='civitaidl')
        print("[Cleanup] 正在列出 Azure blobs（generated/）...")
        all_blobs = list(blob._container_client.list_blobs(name_starts_with='generated/'))
        azure_filenames = set()
        for b in all_blobs:
            fname = b.name.rsplit('/', 1)[-1]
            azure_filenames.add(fname)
        print(f"[Cleanup] Azure 上共 {len(azure_filenames)} 个文件")
    except Exception as e:
        print(f"[Cleanup] ❌ 无法连接 Azure: {e}")
        sys.exit(1)

    # ---- 2. 列出本地 OUTPUT_DIR 中的 .png 文件 ----
    if not os.path.isdir(OUTPUT_DIR):
        print(f"[Cleanup] OUTPUT_DIR 不存在: {OUTPUT_DIR}")
        sys.exit(0)

    local_files = [f for f in os.listdir(OUTPUT_DIR) if f.lower().endswith('.png')]
    print(f"[Cleanup] 本地共 {len(local_files)} 个 .png 文件")
    print()

    # ---- 3. 找出孤立文件并删除 ----
    orphans = [f for f in local_files if f not in azure_filenames]
    print(f"[Cleanup] 孤立文件（本地有、Azure 无）：{len(orphans)} 个")

    if not orphans:
        print("[Cleanup] ✅ 无需清理，本地文件与 Azure 完全一致。")
        return

    deleted = 0
    errors = 0
    for fname in sorted(orphans):
        fpath = os.path.join(OUTPUT_DIR, fname)
        size_kb = os.path.getsize(fpath) / 1024
        if DRY_RUN:
            print(f"  [DRY] 将删除: {fname}  ({size_kb:.1f} KB)")
        else:
            try:
                os.remove(fpath)
                print(f"  ✅ 已删除: {fname}  ({size_kb:.1f} KB)")
                deleted += 1
            except Exception as e:
                print(f"  ❌ 删除失败: {fname}  ({e})")
                errors += 1

    print()
    if DRY_RUN:
        print(f"[Cleanup] DRY RUN 完成，共 {len(orphans)} 个文件待删除。去掉 --dry-run 参数后重新运行以实际删除。")
    else:
        print(f"[Cleanup] 完成：删除 {deleted} 个，失败 {errors} 个。")


if __name__ == '__main__':
    main()
