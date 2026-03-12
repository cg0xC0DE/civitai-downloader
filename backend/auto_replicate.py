# -*- coding: utf-8 -*-
"""
Auto-Replicate Pipeline — 自动复刻收藏图片

流程：
1. 从收藏队列取一条 pending 记录
2. 调用 parse_civitai_image 解析生成参数
3. 检查模型是否齐全；缺失则自动下载（LLM 分类子目录）
4. 调用 run_comfyui_workflow 生成图片
5. 标记为 done / fail
6. 异常事件推送到 cmd-patrol MQ

不并发：下载逐个、生成逐张（ComfyUI 自身有 queue）。
"""

import os
import re
import json
import time
import uuid
import threading
import traceback
import glob
import queue as _queue_mod

from patrol_mq import publish_event
from subtype_classifier import get_type_subtype

# ============ 工作流检测 ============

_WORKFLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")


def _detect_workflow() -> str:
    """自动检测可用的工作流名称（去掉 .json 后缀）"""
    if not os.path.isdir(_WORKFLOW_DIR):
        return "xl.text2img.basic"
    files = sorted(glob.glob(os.path.join(_WORKFLOW_DIR, "*.json")))
    if files:
        return os.path.splitext(os.path.basename(files[0]))[0]
    return "xl.text2img.basic"

# ============ 状态管理 ============

_state_lock = threading.Lock()
_state = {
    "running": False,
    "phase": "idle",        # idle / parsing / downloading / generating / waiting
    "current_url": "",
    "current_fav_id": "",
    "processed": 0,
    "skipped": 0,
    "failed": 0,
    "total_pending": 0,
    "total_retry": 0,       # pending 但有 retry_reason（待调整，不自动处理）
    "shuffle": False,       # True = 随机挑选，False = 顺序处理
    "log": [],              # 最近 N 条日志
}
_LOG_MAX = 50

# 已提交到 ComfyUI 但图片尚未产出的 fav_id（防止重复提交）
_inflight_fav_ids = set()
_inflight_lock = threading.Lock()
_worker_thread = None
_stop_event = threading.Event()

# ============ 后台下载队列 ============
# 每个任务: { mm, fav_ids: set, url_hint }
# fav_ids: 等待该模型的所有收藏 ID（下载完后批量解锁）
_dl_queue: _queue_mod.Queue = _queue_mod.Queue()
_dl_seen: set = set()          # 已入队的 version_id（去重）
_dl_seen_lock = threading.Lock()
_dl_seen_meta: dict = {}       # key -> {kind, name} 用于前端展示
_dl_current_key: str = ""      # 当前正在下载的 key
_dl_worker_thread = None
_dl_worker_started = False


def _enqueue_download(mm: dict, fav_id: str, url_hint: str) -> bool:
    """
    将缺失模型加入后台下载队列。
    返回 True=新任务入队, False=已在队列中（去重）。
    同一 version_id 只入队一次，但会把 fav_id 追加到等待列表。
    """
    global _dl_worker_thread, _dl_worker_started
    key = str(mm.get("version_id") or mm.get("model_id") or mm.get("name", ""))
    with _dl_seen_lock:
        if key in _dl_seen:
            # 已在队列，只追加 fav_id（通过共享 dict 传递）
            _dl_pending_fav_ids.setdefault(key, set()).add(fav_id)
            return False
        _dl_seen.add(key)
        _dl_seen_meta[key] = {"kind": mm.get("kind", ""), "name": mm.get("name", "")}
        _dl_pending_fav_ids[key] = {fav_id}

    _dl_queue.put({"mm": mm, "key": key, "url_hint": url_hint})

    # 确保下载 worker 在运行
    if not _dl_worker_started:
        _dl_worker_thread = threading.Thread(target=_dl_worker_loop, daemon=True, name="dl-worker")
        _dl_worker_thread.start()
        _dl_worker_started = True
    return True


# fav_id 等待集合：key -> set of fav_id
_dl_pending_fav_ids: dict = {}


def _dl_worker_loop():
    """后台下载 worker，串行处理下载队列"""
    global _dl_worker_started
    _log("[DL] 下载 worker 已启动")
    try:
        while True:
            try:
                task = _dl_queue.get(timeout=60)
            except _queue_mod.Empty:
                # 60 秒无任务，退出（下次有任务时重新启动）
                break

            key = task["key"]
            mm = task["mm"]
            url_hint = task["url_hint"]

            with _dl_seen_lock:
                global _dl_current_key
                _dl_current_key = key
                fav_ids = set(_dl_pending_fav_ids.get(key, set()))

            _log(f"[DL] 开始下载: {mm.get('kind')} '{mm.get('name')}' (等待收藏: {len(fav_ids)} 张)")

            ok = _download_missing_model(mm, next(iter(fav_ids), ""), url_hint)

            with _dl_seen_lock:
                _dl_current_key = ""
                _dl_seen.discard(key)
                _dl_seen_meta.pop(key, None)
                _dl_pending_fav_ids.pop(key, None)

            if ok:
                # 下载成功：检查该收藏是否还有其他模型仍在下载队列中
                # 只有全部模型都下完，才清除 retry_reason 让收藏重新进入队列
                try:
                    from favorite_images import list_all, update_status
                    entries = list_all()
                    with _dl_seen_lock:
                        still_pending_keys = set(_dl_seen)  # 当前还在队列中的 key
                    for fid in fav_ids:
                        # 检查该 fav_id 是否还在其他下载任务的等待列表里
                        still_waiting = any(fid in _dl_pending_fav_ids.get(k, set()) for k in still_pending_keys)
                        if still_waiting:
                            _log(f"[DL] 收藏 {fid[:16]} 还有其他模型待下载，继续等待")
                            continue
                        for e in entries:
                            if e.get("id") == fid and e.get("status") == "pending":
                                if "等待模型下载" in e.get("retry_reason", ""):
                                    update_status(fid, "pending")  # 清除 retry_reason
                                    _log(f"[DL] 解锁收藏 {fid[:16]}，重新进入队列")
                                break
                except Exception as ex:
                    _log(f"[DL] 解锁收藏失败: {ex}")
            else:
                # 下载失败：把 retry_reason 改成失败原因
                try:
                    from favorite_images import update_status
                    for fid in fav_ids:
                        update_status(fid, "pending", retry_reason=f"模型下载失败: {mm.get('kind')} '{mm.get('name')}'")
                except Exception:
                    pass

            _dl_queue.task_done()
    finally:
        _dl_worker_started = False
        _log("[DL] 下载 worker 已退出")


def _log(msg: str):
    """追加一条日志到 state"""
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(f"[AutoReplicate] {msg}")
    with _state_lock:
        _state["log"].append(line)
        if len(_state["log"]) > _LOG_MAX:
            _state["log"] = _state["log"][-_LOG_MAX:]


def get_status() -> dict:
    """返回当前状态（线程安全）"""
    with _state_lock:
        s = dict(_state)
    with _dl_seen_lock:
        s["dl_queue_size"] = _dl_queue.qsize()
        s["dl_downloading"] = list(_dl_seen)
        s["dl_current_key"] = _dl_current_key
        s["dl_list"] = [{"key": k, "active": k == _dl_current_key, **_dl_seen_meta.get(k, {})} for k in _dl_seen]
    return s


def is_running() -> bool:
    with _state_lock:
        return _state["running"]


def start(shuffle: bool = False):
    """启动自动复刻后台线程"""
    global _worker_thread
    if is_running():
        return {"status": "error", "message": "已在运行中"}

    _stop_event.clear()
    with _state_lock:
        _state.update(running=True, phase="starting", processed=0, skipped=0, failed=0, shuffle=shuffle, log=[])

    _worker_thread = threading.Thread(target=_run_loop, daemon=True, name="auto-replicate")
    _worker_thread.start()
    mode = "随机" if shuffle else "顺序"
    _log(f"自动复刻已启动（{mode}模式）")
    return {"status": "ok", "message": f"已启动（{mode}模式）"}


def stop():
    """请求停止（当前任务完成后停止）"""
    if not is_running():
        return {"status": "error", "message": "未在运行"}
    _stop_event.set()
    _log("收到停止请求，将在当前任务完成后停止")
    return {"status": "ok", "message": "正在停止..."}


# ============ 主循环 ============

def _run_loop():
    """后台主循环"""
    import random as _random
    try:
        from favorite_images import list_all, update_status
        from server import parse_civitai_image, run_comfyui_workflow

        while not _stop_event.is_set():
            # 取一条 pending（排除 retry_reason 条目，这类需要用户手动调参后才能重新处理）
            entries = list_all()
            with _inflight_lock:
                _inflight_snapshot = set(_inflight_fav_ids)
            pending = [e for e in entries if e.get("status") == "pending" and not e.get("retry_reason") and e.get("id") not in _inflight_snapshot]
            retry_count = sum(1 for e in entries if e.get("status") == "pending" and e.get("retry_reason"))

            with _state_lock:
                _state["total_pending"] = len(pending)
                _state["total_retry"] = retry_count
                shuffle = _state["shuffle"]

            if not pending:
                _log("队列为空，等待 30 秒...")
                with _state_lock:
                    _state["phase"] = "waiting"
                if _stop_event.wait(30):
                    break
                continue

            entry = _random.choice(pending) if shuffle else pending[0]
            fav_id = entry["id"]
            url = entry["url"]

            with _state_lock:
                _state["current_url"] = url
                _state["current_fav_id"] = fav_id

            _log(f"处理: {url}")

            # 注意：不在此处预设 processing，由 _process_one 内部根据情况设置

            try:
                result, reason = _process_one(fav_id, url)
                if result == "submitted":
                    # 生成已提交，状态已设为 processing，继续下一张
                    with _state_lock:
                        _state["processed"] += 1
                    _log(f"✅ 已提交生成，继续下一张: {url}")
                elif result == "done":
                    # 所有参数组合均已废弃 → 回退 pending，由用户手动做美学分析后才能标记 done
                    update_status(fav_id, "pending", retry_reason="所有参数组合均已废弃，请手动确认是否已完成")
                    with _state_lock:
                        _state["skipped"] += 1
                    _log(f"⏭️ 所有组合已废弃，回退 pending 等待人工确认: {url}")
                elif result == "pending":
                    if reason == "dl_queued":
                        # 模型已加入后台下载队列，收藏回退等待，继续下一张
                        with _state_lock:
                            _state["skipped"] += 1
                        _log(f"⏳ 模型下载中，跳过继续下一张: {url}")
                    else:
                        # 用户主动停止，回滚为 pending 等待下次
                        _log(f"⏸️ 停止中，回滚为 pending: {url}")
                elif result == "skip":
                    # 解析/下载失败 → 带 retry_reason 进入待调整，供用户查看原因
                    update_status(fav_id, "pending", retry_reason=reason or "跳过（解析/下载失败）")
                    with _state_lock:
                        _state["skipped"] += 1
                    _log(f"⏭️ 跳过（待调整）: {url} — {reason}")
                else:  # "fail"
                    # 生成提交失败 → 带 retry_reason 进入待调整
                    update_status(fav_id, "pending", retry_reason=reason or "生成提交失败")
                    with _state_lock:
                        _state["failed"] += 1
                    _log(f"❌ 失败（待调整）: {url} — {reason}")
            except Exception as ex:
                traceback.print_exc()
                # 未预期异常 → 带 retry_reason 进入待调整
                update_status(fav_id, "pending", retry_reason=f"自动复刻异常: {str(ex)[:200]}")
                with _state_lock:
                    _state["failed"] += 1
                _log(f"❌ 异常（回退 pending）: {ex}")
                publish_event(
                    title=f"自动复刻异常: {url}",
                    type="replicate_error",
                    detail=(
                        f"原图 URL: {url}"
                        f"\nfav_id: {fav_id}"
                        f"\n异常堆栈:\n{traceback.format_exc()[:700]}"
                    ),
                    meta={"url": url, "fav_id": fav_id, "error": str(ex)},
                )

            # 任务间短暂间隔
            if _stop_event.wait(2):
                break

    except Exception as ex:
        _log(f"主循环异常退出: {ex}")
        traceback.print_exc()
    finally:
        with _state_lock:
            _state["running"] = False
            _state["phase"] = "idle"
            _state["current_url"] = ""
            _state["current_fav_id"] = ""
        _log("自动复刻已停止")


# ============ 单条处理 ============

def _process_one(fav_id: str, url: str) -> tuple:
    """
    处理一条收藏。
    返回 (result, reason) 元组：
      ("submitted", "")      — 生成已提交（不等待完成），状态已设为 processing
      ("done",      "")      — 所有参数组合均已废弃，标记为 done
      ("pending",   "")      — 用户主动停止，回滚为 pending
      ("skip",      reason)  — 跳过（解析失败 / 分类不确定 / 下载失败）
      ("fail",      reason)  — 生成提交失败
    """
    from server import parse_civitai_image, run_comfyui_workflow, _gen_tasks, _gen_lock, _load_gen_tracking
    from favorite_images import update_status

    # ---- 0. 防重：检查是否已有成功的生成结果 ----
    try:
        existing = _load_gen_tracking()
        has_images = any(
            (v.get('blob_urls') or v.get('local_paths'))
            for v in existing.values()
            if v.get('favorite_id') == fav_id
        )
        if has_images:
            update_status(fav_id, "processing")
            _log(f"⏭️ 已有生成结果，跳过重复生成: {url}")
            return "done", ""
    except Exception as _chk_err:
        _log(f"⚠️ 防重检查失败（继续处理）: {_chk_err}")

    # ---- 1. 解析 ----
    with _state_lock:
        _state["phase"] = "parsing"

    parse_result = parse_civitai_image(url)

    if parse_result.get("status") == "error":
        err_msg = parse_result.get('message', '未知')
        publish_event(
            title=f"解析失败: {url}",
            type="parse_error",
            detail=(
                f"错误信息: {err_msg}"
                f"\n原图 URL: {url}"
                f"\nfav_id: {fav_id}"
            ),
            meta={"url": url, "fav_id": fav_id, "error": err_msg},
        )
        return "skip", f"解析失败: {err_msg[:150]}"

    # ---- 2. 检查并下载缺失模型（下载期间不改变收藏状态） ----
    checks = parse_result.get("checks", {})
    missing_models = []

    # Checkpoint
    ckpt_check = checks.get("checkpoint")
    if ckpt_check and not ckpt_check.get("found"):
        missing_models.append({
            "kind": "checkpoint",
            "name": parse_result.get("checkpoint", ""),
            "version_id": ckpt_check.get("modelVersionId"),
            "model_id": ckpt_check.get("modelId"),
            "base_model": parse_result.get("base_model", ""),
        })

    # LoRAs
    for lc in checks.get("loras", []):
        if not lc.get("found"):
            missing_models.append({
                "kind": "lora",
                "name": lc.get("requested_name", ""),
                "version_id": lc.get("modelVersionId"),
                "model_id": lc.get("modelId"),
                "base_model": parse_result.get("base_model", ""),
            })

    # Embeddings
    for ec in checks.get("embeddings", []):
        if not ec.get("found"):
            missing_models.append({
                "kind": "embedding",
                "name": ec.get("requested_name", ""),
                "version_id": ec.get("modelVersionId"),
                "model_id": ec.get("modelId"),
                "base_model": "",
            })

    if missing_models:
        # 异步下载：把缺失模型加入后台队列，当前收藏标记为 pending 等待，继续处理下一张
        names = [f"{m.get('kind')} '{m.get('name')}'".strip("'") for m in missing_models]
        retry_msg = f"等待模型下载: {', '.join(names)}"
        for mm in missing_models:
            _enqueue_download(mm, fav_id, url)
        from favorite_images import update_status
        update_status(fav_id, "pending", retry_reason=retry_msg)
        _log(f"⏳ 模型已加入下载队列，收藏回退等待: {retry_msg}")
        return "pending", "dl_queued"

    # ---- 3. 提交生成（fire-and-forget，不等待完成） ----
    with _state_lock:
        _state["phase"] = "generating"

    gen_result, gen_reason = _do_generate(parse_result, fav_id, url)
    if gen_result == "submitted":
        # 生成已提交到 ComfyUI 队列，但图片尚未产出
        # 不在此处改状态，保持 pending；图片真正生成完成后由回调改为 processing
        with _inflight_lock:
            _inflight_fav_ids.add(fav_id)
        return "submitted", ""
    elif gen_result == "done":
        return "done", ""
    elif gen_result == "skip":
        return "skip", gen_reason
    else:
        return "fail", gen_reason


def _fallback_lora_subtype(name: str, base_model: str, tags: list, description: str) -> str | None:
    """LLM 不可用/不确定时的 LoRA 子分类兜底（仅 XL 系）。"""
    bm = (base_model or "").lower()
    text = " ".join([name or "", description or "", " ".join(tags or [])]).lower()

    # 强关键词优先：即便 base_model / API 元数据缺失，也尽量给出可下载子类
    if any(k in text for k in ("slider", "sliders", "scale")):
        return "xl-slider"
    if any(k in text for k in ("character", "azur lane", "genshin", "honkai", "prinz eugen")):
        return "xl-character"

    is_xl_family = any(k in bm for k in ("xl", "pony", "illustrious", "noobai", "animagine")) or any(
        k in text for k in ("xl", "pony", "illustrious", "noobai", "animagine")
    )
    if not is_xl_family:
        return None

    if any(k in text for k in ("outfit", "costume", "suit", "clothing", "uniform", "dress")):
        return "xl-suit"
    if any(k in text for k in ("style", "aesthetic", "render", "painting")):
        return "xl-style"
    if any(k in text for k in ("nsfw", "sex", "nude", "erotic", "hentai", "porn")):
        return "xl-nsfw"
    return None


def _download_missing_model(mm: dict, fav_id: str, url: str) -> bool:
    """
    下载单个缺失模型。返回 True=成功, False=跳过。
    """
    from civitaidl import CivitaiDownloader

    kind = mm["kind"]
    version_id = mm.get("version_id")
    model_id = mm.get("model_id")
    name = mm["name"]
    base_model = mm.get("base_model", "")

    if not version_id and not model_id:
        _log(f"⚠️ 无法下载 {kind} '{name}': 缺少 version_id 和 model_id")
        publish_event(
            title=f"无法下载 {kind}: {name}",
            type="download_no_id",
            detail=(
                f"模型类型: {kind}"
                f"\n模型名称: {name}"
                f"\n原因: 缺少 version_id 和 model_id，无法构建下载链接"
                f"\n原图 URL: {url}"
                f"\nfav_id: {fav_id}"
            ),
            meta={"kind": kind, "name": name, "url": url, "fav_id": fav_id,
                  "version_id": None, "model_id": None},
        )
        return False

    # 构建 Civitai 下载 URL
    if version_id:
        civitai_url = f"https://civitai.com/models/{model_id}?modelVersionId={version_id}" if model_id else f"https://civitai.com/api/v1/model-versions/{version_id}"
    else:
        civitai_url = f"https://civitai.com/models/{model_id}"

    # 确定 type_subtype
    if kind == "checkpoint":
        from subtype_classifier import classify_checkpoint
        sub = classify_checkpoint(base_model)
        type_subtype = f"ckpt.{sub}"
    elif kind == "lora":
        # 需要从 Civitai API 获取 tags 和 description 用于 LLM 分类
        tags, description = _fetch_model_meta(model_id, version_id)
        sub = None
        try:
            from subtype_classifier import classify_lora
            sub = classify_lora(name, base_model, tags, description)
        except Exception as e:
            _log(f"LLM 分类失败: {e}")

        if not sub:
            sub = _fallback_lora_subtype(name, base_model, tags, description)
            if sub:
                _log(f"⚠️ LoRA LLM 分类不确定，使用规则兜底: {name} → {sub}")

        if not sub:
            _log(f"⚠️ LoRA '{name}' 子分类不确定，跳过")
            civitai_model_url = f"https://civitai.com/models/{model_id}" if model_id else "(无 model_id)"
            publish_event(
                title=f"LoRA 分类不确定: {name}",
                type="classify_uncertain",
                detail=(
                    f"模型名称: {name}"
                    f"\nbaseModel: {base_model}"
                    f"\nmodel_id: {model_id}  version_id: {version_id}"
                    f"\nCivitai 链接: {civitai_model_url}"
                    f"\n原因: LLM 无法确定 LoRA 子分类（xl-style/xl-character/...）"
                    f"\n原图 URL: {url}"
                    f"\nfav_id: {fav_id}"
                ),
                meta={"kind": "lora", "name": name, "base_model": base_model,
                      "model_id": model_id, "version_id": version_id,
                      "civitai_url": civitai_model_url,
                      "url": url, "fav_id": fav_id},
            )
            return False

        type_subtype = f"lora.{sub}"
    elif kind == "embedding":
        type_subtype = "embedding._root"
    else:
        return False

    _log(f"下载 {kind}: {name} → {type_subtype}")

    try:
        downloader = CivitaiDownloader()
        result = downloader.download(civitai_url, type_subtype)

        if result.get("status") == "ok":
            _log(f"✅ 下载完成: {name}")
            # 刷新模型缓存，touch 目录使 ComfyUI 重新扫描
            try:
                from server import model_cache, _restart_comfyui, _invalidate_comfyui_model_cache
                model_cache.refresh_all()
                _invalidate_comfyui_model_cache()
                remaining = _dl_queue.qsize()
                if remaining > 0:
                    _log(f"⏳ 下载队列还有 {remaining} 个任务，跳过重启，等全部下完再重启")
                else:
                    _log("重启 ComfyUI 加载新模型...")
                    _restart_comfyui()
            except Exception:
                pass
            return True
        elif result.get("status") == "exists":
            _log(f"ℹ️ 已存在: {name}")
            # 旧文件可能已存在于磁盘但未写入 model_index，补一条索引避免重复卡在“缺模型”
            try:
                _path = result.get("path")
                _mid = result.get("model_id")
                _vid = result.get("version_id")
                if _path and _mid and _vid:
                    from util import model_index
                    _mi = result.get("model_info") or {}
                    model_index.upsert(
                        model_id=_mid,
                        model_name=result.get("title") or _mi.get("title") or name,
                        version_id=_vid,
                        version_name=result.get("version_name") or _mi.get("version_name") or "",
                        filename=result.get("file_name") or result.get("filename") or os.path.basename(_path),
                        path=os.path.abspath(_path),
                        trigger_words=_mi.get("trained_words", []),
                    )
                    _log(f"ℹ️ 已补建索引: model_id={_mid}, version_id={_vid}")
            except Exception as _idx_err:
                _log(f"⚠️ 已存在模型补建索引失败: {_idx_err}")
            return True
        else:
            msg = result.get("message", "未知错误")
            _log(f"❌ 下载失败: {name} — {msg}")
            publish_event(
                title=f"下载失败: {name}",
                type="download_fail",
                detail=(
                    f"模型类型: {kind}  子类: {type_subtype}"
                    f"\n模型名称: {name}"
                    f"\nmodel_id: {model_id}  version_id: {version_id}"
                    f"\nCivitai 下载链接: {civitai_url}"
                    f"\n错误信息: {msg}"
                    f"\n原图 URL: {url}"
                    f"\nfav_id: {fav_id}"
                ),
                meta={"kind": kind, "name": name, "type_subtype": type_subtype,
                      "model_id": model_id, "version_id": version_id,
                      "civitai_url": civitai_url, "error": msg,
                      "url": url, "fav_id": fav_id},
            )
            return False
    except Exception as e:
        _log(f"❌ 下载异常: {name} — {e}")
        publish_event(
            title=f"下载异常: {name}",
            type="download_error",
            detail=(
                f"模型类型: {kind}  子类: {type_subtype}"
                f"\n模型名称: {name}"
                f"\nmodel_id: {model_id}  version_id: {version_id}"
                f"\nCivitai 下载链接: {civitai_url}"
                f"\n原图 URL: {url}"
                f"\nfav_id: {fav_id}"
                f"\n异常堆栈:\n{traceback.format_exc()[:600]}"
            ),
            meta={"kind": kind, "name": name, "type_subtype": type_subtype,
                  "model_id": model_id, "version_id": version_id,
                  "civitai_url": civitai_url, "error": str(e),
                  "url": url, "fav_id": fav_id},
        )
        return False


def _fetch_model_meta(model_id, version_id) -> tuple:
    """从 Civitai API 获取模型的 tags 和 description（用于 LLM 分类）"""
    import urllib.request
    tags = []
    description = ""
    try:
        from config import CIVITAI_API_URL
        api_url = f"{CIVITAI_API_URL}/{model_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tags = data.get("tags", [])
        description = (data.get("description") or "")[:300]
        # 简单清理 HTML
        description = re.sub(r"<[^>]+>", " ", description).strip()
    except Exception as e:
        _log(f"获取模型 meta 失败: {e}")
    return tags, description


def _do_generate(parse_result: dict, fav_id: str, url: str) -> str:
    """
    提交生成任务（fire-and-forget，不等待完成）。
    返回 "submitted" / "fail" / "skip"
    """
    from server import run_comfyui_workflow, compute_weight_sweep, _pick_random_xl_checkpoints
    import copy as _copy

    checks = parse_result.get("checks", {})
    ckpt = checks.get("checkpoint") or {}

    _parse_checkpoint_raw = str(parse_result.get("checkpoint", "") or "").strip()
    _fallback_ckpts = []
    if ckpt.get("found"):
        _ckpt_sub = ckpt.get('subtype', '')
        checkpoint_name = f"{_ckpt_sub}/{ckpt['filename']}" if _ckpt_sub else ckpt['filename']
    elif not _parse_checkpoint_raw:
        _fallback_ckpts = _pick_random_xl_checkpoints(count=6)
        if not _fallback_ckpts:
            _log("⚠️ 未解析到 checkpoint，且无可用 XL 基模可回退，跳过生成")
            return "skip", "Checkpoint 缺失且无可用 XL 基模"
        checkpoint_name = _fallback_ckpts[0]
        _log(f"⚠️ 未解析到 checkpoint，随机回退 XL 基模 {len(_fallback_ckpts)} 个")
    else:
        _log("⚠️ Checkpoint 未找到，跳过生成")
        return "skip", f"Checkpoint 未找到: {ckpt.get('requested_name', '')}"

    # 构建 LoRA 列表
    lora_list = []
    for lc in checks.get("loras", []):
        if lc.get("found"):
            lora_list.append({
                "name": f"{lc['subtype']}/{lc['filename']}",
                "weight": lc.get("weight", 1.0),
            })

    # ---- 权重扫描：将扫描组合注入 variations ----
    variations = parse_result.get("variations") or [{"label": "基准", "params": {}}]
    sweep = compute_weight_sweep(checks.get("loras", []))
    if sweep:
        merged = []
        for base_var in variations:
            for combo in sweep:
                new_var = _copy.deepcopy(base_var)
                wt_desc = ", ".join(f"L{k}={v}" for k, v in combo.items())
                new_var["label"] = f"{base_var.get('label', '')} | {wt_desc}"
                new_var.setdefault("params", {})["lora_weights"] = combo
                merged.append(new_var)
        variations = merged
        _log(f"权重扫描: {len(sweep)}组 × {len(parse_result.get('variations') or [{'_':1}])}变体 = {len(variations)}张")

    # ---- 尺寸未知：注入两种标准竖图尺寸变体 ----
    ps = parse_result.get("param_sources", {})
    _size_unknown = ps.get("width") in ("default", "missing") or ps.get("height") in ("default", "missing")
    if _size_unknown:
        size_pairs = [(832, 1216), (768, 1344)]
        expanded = []
        for var in variations:
            for sw, sh in size_pairs:
                new_var = _copy.deepcopy(var)
                new_var.setdefault("params", {}).update({"width": sw, "height": sh})
                new_var["label"] = f"{var.get('label', '')} | {sw}×{sh}"
                expanded.append(new_var)
        variations = expanded
        _log(f"尺寸未知，注入 {len(size_pairs)} 种标准尺寸，共 {len(variations)} 个变体")

    # ---- 种子未知：每个变体 ×3 随机种子 ----
    import random as _rand
    _seed_unknown = parse_result.get("seed", -1) < 0
    if _seed_unknown:
        _SEED_REPEATS = 3
        expanded = []
        for var in variations:
            for si in range(_SEED_REPEATS):
                new_var = _copy.deepcopy(var)
                new_var.setdefault("params", {})["seed"] = _rand.randint(0, 2**32 - 1)
                new_var["label"] = f"{var.get('label', '')} | seed#{si+1}"
                expanded.append(new_var)
        variations = expanded
        _log(f"种子未知，每变体 ×{_SEED_REPEATS} 随机种子，共 {len(variations)} 个变体")

    # ---- checkpoint 缺失：改为随机 6 个 XL 基模（每个基模一张） ----
    if _fallback_ckpts:
        _template = _copy.deepcopy((variations or [{"label": "基准", "params": {}}])[0])
        _tp = _template.get('params') if isinstance(_template.get('params'), dict) else {}
        _template['params'] = _tp
        _tp.pop('checkpoint', None)
        _tp.pop('seed', None)

        _ckpt_vars = []
        for i, ck_name in enumerate(_fallback_ckpts):
            nv = _copy.deepcopy(_template)
            nv.setdefault('params', {})['checkpoint'] = ck_name
            nv['label'] = f"随机XL#{i+1} | {ck_name.split('/')[-1]}"
            _ckpt_vars.append(nv)
        variations = _ckpt_vars
        _log(f"checkpoint 缺失，已注入随机 XL 基模变体: {len(variations)} 个")

    # ---- 废弃记录检查：过滤掉已废弃的参数组合 ----
    try:
        from discard_log import check_params, compute_fingerprint
        base_params = {
            'checkpoint': checkpoint_name,
            'loras': lora_list,
            'prompt': parse_result.get('prompt', ''),
            'negative_prompt': parse_result.get('negative_prompt', ''),
            'width': parse_result.get('width', 1024),
            'height': parse_result.get('height', 1024),
            'steps': parse_result.get('steps', 20),
            'cfg': parse_result.get('cfg', 7.0),
            'sampler': parse_result.get('sampler', 'dpmpp_2m'),
            'scheduler': parse_result.get('scheduler', ''),
            'seed': parse_result.get('seed') if parse_result.get('seed', -1) >= 0 else -1,
        }
        filtered_variations = []
        skipped_labels = []
        for var in variations:
            vp = var.get('params', {})
            # 合并 variation 的 lora_weights 覆盖到 base_params
            var_loras = [dict(l) for l in lora_list]
            lw = vp.get('lora_weights')
            if lw and isinstance(lw, dict):
                for li, lora in enumerate(var_loras):
                    if li in lw:
                        lora['weight'] = lw[li]
            var_params = dict(base_params, loras=var_loras)
            _var_ckpt = str(vp.get('checkpoint', '')).strip()
            if _var_ckpt:
                var_params['checkpoint'] = _var_ckpt
            chk = check_params(fav_id, var_params)
            if chk['found']:
                skipped_labels.append(var.get('label', f'变体{len(skipped_labels)+1}'))
                _log(f"⏭️ 跳过已废弃参数组合: {var.get('label')} (fp={chk['fingerprint'][:8]})")
            else:
                filtered_variations.append(var)

        if skipped_labels:
            publish_event(
                title=f"跳过已废弃参数: {url}",
                type="discard_skip",
                detail=(
                    f"原图 URL: {url}"
                    f"\nfav_id: {fav_id}"
                    f"\ncheckpoint: {checkpoint_name}"
                    f"\nLoRA: {', '.join(l['name'] for l in lora_list) or '无'}"
                    f"\n跳过 {len(skipped_labels)} 个已废弃的参数组合:"
                    f"\n" + "\n".join(f"  - {s}" for s in skipped_labels)
                ),
                meta={"url": url, "fav_id": fav_id, "checkpoint": checkpoint_name,
                      "loras": [l['name'] for l in lora_list], "skipped": skipped_labels},
            )

        if not filtered_variations:
            _log(f"⏭️ 所有参数组合均已废弃，跳过生成: {url}")
            return "done", ""

        variations = filtered_variations
        _log(f"废弃记录过滤后: {len(variations)} 个组合待生成（跳过 {len(skipped_labels)} 个）")
    except Exception as dl_err:
        _log(f"⚠️ 废弃记录检查失败（继续生成）: {dl_err}")

    _log(f"生成: ckpt={checkpoint_name}, loras={len(lora_list)}, "
         f"size={parse_result.get('width')}x{parse_result.get('height')}, "
         f"总提交={len(variations)}张")

    try:
        wf_name = _detect_workflow()
        result = run_comfyui_workflow(  # type: ignore[call-arg]
            workflow_name=wf_name,
            checkpoint=checkpoint_name,
            positive_prompt=parse_result.get("prompt", ""),
            negative_prompt=parse_result.get("negative_prompt", "low quality, worst quality"),
            width=parse_result.get("width", 1024),
            height=parse_result.get("height", 1024),
            steps=parse_result.get("steps", 20),
            cfg=parse_result.get("cfg", 7.0),
            sampler=parse_result.get("sampler", "dpmpp_2m"),
            scheduler=parse_result.get("scheduler", ""),
            seed=parse_result.get("seed") if parse_result.get("seed", -1) >= 0 else None,
            loras=lora_list if lora_list else None,
            batch_size=1,
            variations=variations,
            favorite_id=fav_id,
            source_url=url,
            clip_skip=parse_result.get("clip_skip"),
            queue_max_pending=2,
            queue_wait_timeout=1800,
        )

        if result.get("status") in ("submitted", "success"):
            batch_id = result.get("prompt_id", "")
            _log(f"已提交生成: batch={batch_id}")
            return "submitted", ""
        else:
            msg = result.get("message", "")
            _log(f"生成失败: {msg}")
            publish_event(
                title=f"生成失败: {url}",
                type="generate_fail",
                detail=(
                    f"原图 URL: {url}"
                    f"\nfav_id: {fav_id}"
                    f"\ncheckpoint: {checkpoint_name}"
                    f"\nLoRA: {', '.join(l['name'] for l in lora_list) or '无'}"
                    f"\n尺寸: {parse_result.get('width')}x{parse_result.get('height')}"
                    f"\nComfyUI 拒绝原因: {msg}"
                ),
                meta={"url": url, "fav_id": fav_id, "checkpoint": checkpoint_name,
                      "loras": [l['name'] for l in lora_list],
                      "width": parse_result.get('width'), "height": parse_result.get('height'),
                      "comfyui_error": msg},
            )
            return "fail", f"ComfyUI 拒绝: {msg[:200]}"

    except Exception as e:
        _log(f"生成异常: {e}")
        publish_event(
            title=f"生成异常: {url}",
            type="generate_error",
            detail=(
                f"原图 URL: {url}"
                f"\nfav_id: {fav_id}"
                f"\ncheckpoint: {checkpoint_name}"
                f"\nLoRA: {', '.join(l['name'] for l in lora_list) or '无'}"
                f"\n尺寸: {parse_result.get('width')}x{parse_result.get('height')}"
                f"\n异常堆栈:\n{traceback.format_exc()[:600]}"
            ),
            meta={"url": url, "fav_id": fav_id, "checkpoint": checkpoint_name,
                  "loras": [l['name'] for l in lora_list],
                  "width": parse_result.get('width'), "height": parse_result.get('height'),
                  "error": str(e)},
        )
        return "fail", f"生成异常: {str(e)[:200]}"
