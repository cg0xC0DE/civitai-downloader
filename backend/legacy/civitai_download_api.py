#!/usr/bin/env python3
"""
Civitai Downloader with Multi-Task Support & HTTP API
多任务下载 + Web API 服务器

安装依赖: pip install requests

使用方法:
    python civitai_download_api.py --port 8080
"""

import os
import sys
import json
import threading
import time
import uuid
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import hashlib

# ============ 配置 ============
HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DOWNLOAD_DIR = "C:/workplace/downloads"
MAX_CONCURRENT = 3  # 最大并发下载数

# ============ 全局状态 ============
tasks = {}  # {task_id: {...}}
task_queue = []  # 等待中的任务 ID
task_lock = threading.Lock()

executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT + 2)

# ============ 任务管理器 ============
class TaskManager:
    @staticmethod
    def create_task(model_id, task_id=None):
        """创建新任务"""
        if task_id is None:
            task_id = str(uuid.uuid4())[:8]
        
        task = {
            "task_id": task_id,
            "model_id": model_id,
            "status": "pending",  # pending, downloading, completed, failed, stopped
            "progress": 0,
            "speed": 0,
            "downloaded": 0,
            "total": 0,
            "filename": None,
            "error": None,
            "start_time": None,
            "end_time": None
        }
        
        with task_lock:
            tasks[task_id] = task
            task_queue.append(task_id)
        
        return task_id
    
    @staticmethod
    def get_next_task():
        """获取下一个待执行的任务"""
        with task_lock:
            while task_queue and tasks.get(task_queue[0], {}).get("status") != "pending":
                task_queue.pop(0)
            
            if task_queue:
                return task_queue[0]
            return None
    
    @staticmethod
    def get_task(task_id):
        return tasks.get(task_id)
    
    @staticmethod
    def get_all_tasks():
        """获取所有任务状态"""
        with task_lock:
            return list(tasks.values())
    
    @staticmethod
    def update_task(task_id, **kwargs):
        """更新任务状态"""
        with task_lock:
            if task_id in tasks:
                tasks[task_id].update(kwargs)
    
    @staticmethod
    def stop_task(task_id):
        """停止任务"""
        with task_lock:
            if task_id in tasks:
                tasks[task_id]["status"] = "stopped"
                tasks[task_id]["end_time"] = time.time()


# ============ 下载器 ============
class CivitaiDownloader:
    def __init__(self, task_id, model_id):
        self.task_id = task_id
        self.model_id = model_id
        self.session = requests.Session()
        self.chunk_size = 8192
        self._stop_flag = threading.Event()
    
    def stop(self):
        self._stop_flag.set()
    
    def download(self):
        global tasks
        
        try:
            TaskManager.update_task(
                self.task_id,
                status="downloading",
                start_time=time.time()
            )
            
            # 获取下载链接
            url = f"https://civitai.com/api/download/models/{self.model_id}?type=Model&format=SafeTensor&size=pruned&fp=fp16"
            
            print(f"📥 [{self.task_id}] 解析下载地址...")
            head_resp = self.session.head(url, allow_redirects=True, timeout=30)
            final_url = head_resp.url
            
            filename = self.extract_filename(head_resp.headers)
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            total_size = int(head_resp.headers.get('Content-Length', 0))
            
            TaskManager.update_task(
                self.task_id,
                filename=filename,
                total=total_size
            )
            
            print(f"📦 [{self.task_id}] 开始下载: {filename} ({self.format_size(total_size)})")
            
            # 执行下载
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            
            response = self.session.get(final_url, stream=True, timeout=300)
            response.raise_for_status()
            
            downloaded = 0
            last_time = time.time()
            last_downloaded = 0
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if self._stop_flag.is_set():
                        print(f"⏹️ [{self.task_id}] 下载已停止")
                        TaskManager.update_task(self.task_id, status="stopped", end_time=time.time())
                        return
                    
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # 每秒更新一次状态
                        current_time = time.time()
                        if current_time - last_time >= 1:
                            speed = (downloaded - last_downloaded) / (current_time - last_time)
                            
                            TaskManager.update_task(
                                self.task_id,
                                progress=downloaded / total_size if total_size > 0 else 0,
                                downloaded=downloaded,
                                speed=speed
                            )
                            
                            last_time = current_time
                            last_downloaded = downloaded
            
            # 下载完成
            TaskManager.update_task(
                self.task_id,
                progress=1,
                downloaded=downloaded,
                speed=0,
                status="completed",
                end_time=time.time()
            )
            
            print(f"✅ [{self.task_id}] 下载完成: {filename} ({self.format_size(downloaded)})")
            
            # 标记队列中任务完成
            with task_lock:
                if self.task_id in task_queue:
                    task_queue.remove(self.task_id)
            
            # 检查队列，启动下一个任务
            TaskManager.scheduler_check()
            
        except Exception as e:
            print(f"❌ [{self.task_id}] 下载失败: {e}")
            TaskManager.update_task(
                self.task_id,
                status="failed",
                error=str(e),
                end_time=time.time()
            )
            
            with task_lock:
                if self.task_id in task_queue:
                    task_queue.remove(self.task_id)
            
            TaskManager.scheduler_check()
    
    @staticmethod
    def scheduler_check():
        """调度器：检查队列，启动等待中的任务"""
        while True:
            next_task_id = TaskManager.get_next_task()
            
            if not next_task_id:
                break
            
            task = TaskManager.get_task(next_task_id)
            if task and task["status"] == "pending":
                # 标记为准备中
                with task_lock:
                    if tasks.get(next_task_id, {}).get("status") == "pending":
                        # 检查当前正在下载的任务数
                        downloading = sum(1 for t in tasks.values() if t["status"] == "downloading")
                        if downloading >= MAX_CONCURRENT:
                            break
                        
                        tasks[next_task_id]["status"] = "preparing"
                
                # 启动下载
                task_info = tasks.get(next_task_id)
                if task_info and task_info["status"] == "preparing":
                    print(f"🚀 [{next_task_id}] 启动下载任务 (并发达: {downloading + 1}/{MAX_CONCURRENT})")
                    executor.submit(CivitaiDownloader(next_task_id, task_info["model_id"]).download)
            
            break
    
    @staticmethod
    def format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"
    
    def extract_filename(self, headers):
        content_disposition = headers.get('Content-Disposition', '')
        if 'filename=' in content_disposition:
            import re
            match = re.search(r'filename="?([^";\n]+)', content_disposition)
            if match:
                return match.group(1).strip()
        return f"{self.model_id}.safetensors"


# ============ HTTP Handler ============
class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, ensure_ascii=False).encode())
    
    def do_GET(self):
        path = urlparse(self.path).path
        
        if path == '/status':
            self.send_json(TaskManager.get_all_tasks())
        
        elif path == '/queue':
            with task_lock:
                queue = [tid for tid in task_queue if tasks.get(tid, {}).get("status") == "pending"]
            self.send_json({"pending": queue, "max_concurrent": MAX_CONCURRENT})
        
        elif path == '/health':
            self.send_json({"status": "ok"})
        
        elif path.startswith('/task/'):
            task_id = path.split('/')[-1]
            task = TaskManager.get_task(task_id)
            if task:
                self.send_json(task)
            else:
                self.send_json({"error": "Task not found"}, 404)
        
        else:
            self.send_json({
                "service": "Civitai Multi-Task Downloader",
                "version": "2.0",
                "endpoints": {
                    "GET /status": "所有任务状态",
                    "GET /queue": "任务队列信息",
                    "GET /task/{id}": "单个任务详情",
                    "POST /download": "添加下载任务",
                    "POST /stop/{id}": "停止任务"
                }
            })
    
    def do_POST(self):
        path = urlparse(self.path).path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        if path == '/download':
            model_id = data.get('model_id')
            if not model_id:
                self.send_json({"error": "model_id required"}, 400)
                return
            
            task_id = TaskManager.create_task(model_id, data.get('task_id'))
            
            # 尝试启动
            CivitaiDownloader.scheduler_check()
            
            self.send_json({
                "status": "queued",
                "task_id": task_id,
                "model_id": model_id
            })
        
        elif path.startswith('/stop/'):
            task_id = path.split('/')[-1]
            TaskManager.stop_task(task_id)
            
            # 触发调度
            CivitaiDownloader.scheduler_check()
            
            self.send_json({"status": "stopped", "task_id": task_id})
        
        else:
            self.send_json({"error": "Not found"}, 404)


# ============ 主程序 ============
def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Civitai Multi-Task Downloader")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("-d", "--dir", default=DOWNLOAD_DIR, help="下载目录")
    parser.add_argument("-c", "--concurrent", type=int, default=MAX_CONCURRENT, help="最大并发数")
    
    args = parser.parse_args()
    
    global DOWNLOAD_DIR, MAX_CONCURRENT
    DOWNLOAD_DIR = args.dir
    MAX_CONCURRENT = args.concurrent
    
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    server = HTTPServer((HOST, args.port), DownloadHandler)
    
    print("=" * 55)
    print(f"   Civitai Multi-Task Downloader v2.0")
    print(f"   监听: http://{HOST}:{args.port}")
    print(f"   下载目录: {DOWNLOAD_DIR}")
    print(f"   最大并发: {MAX_CONCURRENT}")
    print("=" * 55)
    print("\nAPI 使用:")
    print("  GET  /status           - 所有任务状态")
    print("  GET  /queue            - 任务队列")
    print("  GET  /task/{id}        - 单个任务详情")
    print("  POST /download         - 添加任务 {model_id: xxx, task_id: xxx}")
    print("  POST /stop/{id}        - 停止任务")
    print()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
