#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收藏图片 URL 接收接口

功能：
- 接收 Civitai 图片 URL
- 验证 URL 格式
- 保存到缓存文件（支持多生产者）

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

# 配置
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
MAX_QUEUE_SIZE = 10000  # 最大队列长度
QUEUE_FILE = os.path.join(CACHE_DIR, 'favorite_images.jsonl')

# 线程安全队列
_queue_lock = threading.Lock()
_url_queue = []

# URL 正则验证
IMAGE_URL_PATTERN = re.compile(r'^https://civitai\.com/images/\d+$')


def init_cache_dir():
    """初始化缓存目录"""
    os.makedirs(CACHE_DIR, exist_ok=True)


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
    保存 URL 到队列文件
    返回: {"status": "ok"/"error", "id": "...", "message": "..."}
    """
    try:
        is_valid, error = validate_url(url)
        if not is_valid:
            return {"status": "error", "message": error}
        
        url = url.strip()
        entry_id = str(uuid.uuid4())[:8]
        timestamp = __import__('datetime').datetime.now().isoformat()
        
        # 写入队列文件 (JSONL 格式)
        with _queue_lock:
            with open(QUEUE_FILE, 'a', encoding='utf-8') as f:
                entry = {
                    "id": entry_id,
                    "url": url,
                    "status": "pending",
                    "created_at": timestamp,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        
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
            # 读取所有行
            if not os.path.exists(QUEUE_FILE):
                return {"status": "empty", "message": "队列为空"}
            
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if not lines:
                return {"status": "empty", "message": "队列为空"}
            
            # 找到第一条 pending 状态
            for i, line in enumerate(lines):
                entry = json.loads(line.strip())
                if entry.get('status') == 'pending':
                    # 更新状态为 processing
                    entry['status'] = 'processing'
                    entry['processing_at'] = __import__('datetime').datetime.now().isoformat()
                    lines[i] = json.dumps(entry, ensure_ascii=False) + '\n'
                    
                    # 写回文件
                    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
                    
                    return {
                        "status": "ok",
                        "consumed": entry
                    }
            
            return {"status": "empty", "message": "没有待处理的 URL"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def mark_done(entry_id: str) -> dict:
    """
    标记某条记录为已完成（供消费进程调用）
    """
    try:
        with _queue_lock:
            if not os.path.exists(QUEUE_FILE):
                return {"status": "error", "message": "队列文件不存在"}
            
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            updated = False
            for i, line in enumerate(lines):
                entry = json.loads(line.strip())
                if entry.get('id') == entry_id:
                    entry['status'] = 'done'
                    entry['done_at'] = __import__('datetime').datetime.now().isoformat()
                    lines[i] = json.dumps(entry, ensure_ascii=False) + '\n'
                    updated = True
                    break
            
            if not updated:
                return {"status": "error", "message": "未找到对应记录"}
            
            with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            return {"status": "ok", "message": "已标记完成"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def cleanup_done():
    """清理已完成的记录"""
    try:
        with _queue_lock:
            if not os.path.exists(QUEUE_FILE):
                return {"status": "error", "message": "队列文件不存在"}
            
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            remaining = []
            removed = 0
            for line in lines:
                entry = json.loads(line.strip())
                if entry.get('status') != 'done':
                    remaining.append(line)
                else:
                    removed += 1
            
            if remaining:
                with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                    f.writelines(remaining)
            else:
                # 清空文件
                with open(QUEUE_FILE, 'w') as f:
                    pass
            
            return {"status": "ok", "removed": removed, "remaining": len(remaining)}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_queue_status() -> dict:
    """获取队列状态"""
    try:
        if not os.path.exists(QUEUE_FILE):
            return {"total": 0, "pending": 0, "processing": 0, "done": 0}
        
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        stats = {"total": len(lines), "pending": 0, "processing": 0, "done": 0}
        for line in lines:
            entry = json.loads(line.strip())
            status = entry.get('status', 'pending')
            stats[status] = stats.get(status, 0) + 1
        
        return stats
    
    except Exception:
        return {"error": str(Exception)}


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
    init_cache_dir()
    
    port = port or 53133
    server = HTTPServer(('0.0.0.0', port), APIHandler)
    print(f"[API] 收藏图片服务已启动: http://localhost:{port}")
    print(f"[API] 队列文件: {QUEUE_FILE}")
    server.serve_forever()


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 53133
    run_server(port)
