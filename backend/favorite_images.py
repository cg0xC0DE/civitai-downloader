#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收藏图片 URL 管理（local-first + Azure 双写）

存储策略：
- 本地：cache/favorite_images/queue.jsonl（始终读写）
- Azure：data/favorite_images.jsonl（有配置时同步写入）
- 首次启动若本地为空但 Azure 有数据，会自动迁移到本地

Usage:
    POST /api/favorite-images
    Body: {"url": "https://civitai.com/images/81080164"}
"""

import os
import re
import json
import uuid
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ============== 存储配置 ==============
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_PATH = os.path.join(_BACKEND_DIR, 'cache', 'favorite_images', 'queue.jsonl')

_BLOB_CONTAINER = 'civitaidl'
_BLOB_SUBFOLDER = 'data'
_BLOB_FILENAME = 'favorite_images.jsonl'

# 线程安全锁
_queue_lock = threading.Lock()


def _azure_available() -> bool:
    """检查 Azure Blob 是否可用"""
    try:
        from azure_blob.credentials import CONNECTION_STRING
        return bool(CONNECTION_STRING)
    except Exception:
        return False


def _get_blob():
    """获取 BlobStorage 实例（仅 Azure 可用时调用）"""
    from azure_blob import BlobStorage
    return BlobStorage(container=_BLOB_CONTAINER)


def _parse_jsonl(text: str) -> list:
    """解析 JSONL 文本为列表"""
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
    """列表转 JSONL 文本"""
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    return '\n'.join(lines) + '\n' if lines else ''


def _read_all_entries() -> list:
    """读取所有条目（本地优先，Azure 回退迁移）"""
    # 1. 尝试读本地
    if os.path.exists(_LOCAL_PATH):
        with open(_LOCAL_PATH, 'r', encoding='utf-8') as f:
            entries = _parse_jsonl(f.read())
        if entries:
            return entries

    # 2. 本地为空，尝试从 Azure 迁移
    if _azure_available():
        try:
            blob = _get_blob()
            text = blob.get_text(_BLOB_SUBFOLDER, _BLOB_FILENAME)
            entries = _parse_jsonl(text)
            if entries:
                # 写入本地缓存
                os.makedirs(os.path.dirname(_LOCAL_PATH), exist_ok=True)
                with open(_LOCAL_PATH, 'w', encoding='utf-8') as f:
                    f.write(_entries_to_jsonl(entries))
                print(f"[Favorites] 从 Azure 迁移 {len(entries)} 条到本地")
                return entries
        except Exception as e:
            print(f"[Favorites] Azure 读取失败: {e}")

    return []


def _write_all_entries(entries: list):
    """写入所有条目（本地 + Azure 双写）"""
    text = _entries_to_jsonl(entries)

    # 1. 始终写本地
    os.makedirs(os.path.dirname(_LOCAL_PATH), exist_ok=True)
    with open(_LOCAL_PATH, 'w', encoding='utf-8') as f:
        f.write(text)

    # 2. 有 Azure 配置时同步写入
    if _azure_available():
        try:
            blob = _get_blob()
            blob.put_text(_BLOB_SUBFOLDER, _BLOB_FILENAME, text)
        except Exception as e:
            print(f"[Favorites] Azure 写入失败（本地已保存）: {e}")

# URL 正则验证
IMAGE_URL_PATTERN = re.compile(r'^https://civitai\.com/images/\d+$')



def validate_url(url: str) -> tuple:
    """
    验证 URL 格式
    返回: (is_valid, error_message)
    """
    if not url:
        return False, "URL 不能为空"
    
    # 去除空白字符
    url = url.strip()
    
    # 检查是否是 URL
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "无效的 URL 格式"
    except Exception:
        return False, "URL 解析失败"
    
    # 验证域名
    if parsed.netloc != 'civitai.com':
        return False, "仅支持 civitai.com 域名"
    
    # 验证路径格式
    path_match = re.match(r'^/images/(\d+)$', parsed.path)
    if not path_match:
        return False, "URL 路径格式无效，应为 /images/{id}"
    
    # 验证图片 ID
    image_id = path_match.group(1)
    if not image_id.isdigit() or len(image_id) < 1:
        return False, "无效的图片 ID"
    
    return True, None


def save_url_to_queue(url: str) -> dict:
    """
    保存 URL 到队列（Azure Blob）
    返回: {"status": "ok"/"error", "id": "...", "message": "..."}
    """
    try:
        is_valid, error = validate_url(url)
        if not is_valid:
            return {"status": "error", "message": error}
        
        url = url.strip()
        entry_id = str(uuid.uuid4())[:8]
        timestamp = __import__('datetime').datetime.now().isoformat()
        
        entry = {
            "id": entry_id,
            "url": url,
            "status": "pending",
            "created_at": timestamp,
        }
        
        with _queue_lock:
            entries = _read_all_entries()
            entries.append(entry)
            _write_all_entries(entries)
        
        return {
            "status": "ok",
            "id": entry_id,
            "url": url,
            "message": "已加入处理队列"
        }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def consume_one() -> dict:
    """
    消费一条 URL（返回并标记为 processing）
    供其他进程调用
    """
    try:
        with _queue_lock:
            entries = _read_all_entries()
            if not entries:
                return {"status": "empty", "message": "队列为空"}
            
            for entry in entries:
                if entry.get('status') == 'pending':
                    entry['status'] = 'processing'
                    entry['processing_at'] = __import__('datetime').datetime.now().isoformat()
                    _write_all_entries(entries)
                    return {"status": "ok", "consumed": entry}
            
            return {"status": "empty", "message": "没有待处理的 URL"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def mark_done(entry_id: str) -> dict:
    """
    标记某条记录为已完成（供消费进程调用）
    """
    try:
        with _queue_lock:
            entries = _read_all_entries()
            updated = False
            for entry in entries:
                if entry.get('id') == entry_id:
                    entry['status'] = 'done'
                    entry['done_at'] = __import__('datetime').datetime.now().isoformat()
                    updated = True
                    break
            
            if not updated:
                return {"status": "error", "message": "未找到对应记录"}
            
            _write_all_entries(entries)
            return {"status": "ok", "message": "已标记完成"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def cleanup_done():
    """清理已完成的记录"""
    try:
        with _queue_lock:
            entries = _read_all_entries()
            remaining = [e for e in entries if e.get('status') != 'done']
            removed = len(entries) - len(remaining)
            _write_all_entries(remaining)
            return {"status": "ok", "removed": removed, "remaining": len(remaining)}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_queue_status() -> dict:
    """获取队列状态"""
    try:
        entries = _read_all_entries()
        stats = {"total": len(entries), "pending": 0, "processing": 0, "done": 0, "fail": 0}
        for entry in entries:
            status = entry.get('status', 'pending')
            stats[status] = stats.get(status, 0) + 1
        return stats
    
    except Exception as e:
        return {"error": str(e)}


# ============== 新增方法（供 server.py 统一调用） ==============

def list_all() -> list:
    """返回所有收藏条目"""
    return _read_all_entries()


def update_status(entry_id: str, new_status: str) -> dict:
    """
    按 id 更新收藏条目状态
    返回: {"status": "ok"/"error", "message": "..."}
    """
    try:
        with _queue_lock:
            entries = _read_all_entries()
            updated = False
            for entry in entries:
                if entry.get('id') == entry_id:
                    entry['status'] = new_status
                    if new_status == 'processing':
                        entry['processing_at'] = __import__('datetime').datetime.now().isoformat()
                    elif new_status == 'done':
                        entry['done_at'] = __import__('datetime').datetime.now().isoformat()
                    elif new_status == 'fail':
                        entry['fail_at'] = __import__('datetime').datetime.now().isoformat()
                    updated = True
                    break
            
            if not updated:
                return {"status": "error", "message": "未找到对应记录"}
            
            _write_all_entries(entries)
            return {"status": "ok", "message": f"已更新为 {new_status}"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def delete_by_id(entry_id: str) -> dict:
    """
    按 id 删除收藏条目
    返回: {"status": "ok"/"error", "message": "..."}
    """
    try:
        with _queue_lock:
            entries = _read_all_entries()
            before = len(entries)
            entries = [e for e in entries if e.get('id') != entry_id]
            
            if len(entries) == before:
                return {"status": "error", "message": "未找到对应记录"}
            
            _write_all_entries(entries)
            return {"status": "ok", "message": "已删除"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============== HTTP Handler ==============

class APIHandler(BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        print(f"[API] {self.address_string()} - {format % args}")
    
    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == '/api/favorite-images':
            # 接收 URL
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            
            try:
                data = json.loads(body)
                url = data.get('url', '')
            except json.JSONDecodeError:
                self.send_json({"status": "error", "message": "无效的 JSON 格式"}, 400)
                return
            
            result = save_url_to_queue(url)
            status = 200 if result['status'] == 'ok' else 400
            self.send_json(result, status)
        
        else:
            self.send_json({"status": "error", "message": "未知接口"}, 404)
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == '/api/favorite-images/status':
            # 队列状态
            stats = get_queue_status()
            self.send_json({"status": "ok", "stats": stats})
        
        elif path == '/api/favorite-images/consume':
            # 消费一条（供其他进程调用）
            result = consume_one()
            self.send_json(result)
        
        elif path == '/api/favorite-images/cleanup':
            # 清理已完成的
            result = cleanup_done()
            self.send_json(result)
        
        else:
            self.send_json({"status": "error", "message": "未知接口"}, 404)


def run_server(port: int = None):
    """启动服务"""
    port = port or 53133
    server = HTTPServer(('0.0.0.0', port), APIHandler)
    print(f"[API] 收藏图片服务已启动: http://localhost:{port}")
    print(f"[API] 数据源: Azure Blob {_BLOB_CONTAINER}/{_BLOB_SUBFOLDER}/{_BLOB_FILENAME}")
    server.serve_forever()


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 53133
    run_server(port)
