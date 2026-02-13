#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Downloader - 最终版
功能：
- 解析 Civitai 页面
- 提取 Suggested Settings（从 HTML）
- 下载

使用:
    python civitai_downloader.py --serve 8080
"""

import os
import sys
import io
import json
import threading
import time
import uuid
import re
import requests
from bs4 import BeautifulSoup
from html import unescape
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============ 配置 ============
HOST = "0.0.0.0"
DEFAULT_PORT = 8080
MODEL_BASE_DIR = "D:/"  # AI 模型存放根目录
MAX_CONCURRENT = 3  # 最大并发下载数

# 默认为 None，使用时从 PATH_MAPPING 获取
DOWNLOAD_DIR = None  # 动态确定

# 路径映射：Type.SubType -> 子目录路径
PATH_MAPPING = {
    # ckpt (基模)
    "ckpt.1.5": "ckpt/1.5",
    "ckpt.xl": "ckpt/xl",
    "ckpt.flux": "ckpt/flux",
    "ckpt.ide": "ckpt/ide",
    
    # lora
    "lora.1.5": "lora/1.5",
    "lora.xl-style": "lora/xl-style",
    "lora.xl-nsfw": "lora/xl-nsfw",
    "lora.xl-enhance": "lora/xl-enhance",
    "lora.xl-character": "lora/xl-character",
    "lora.xl-background": "lora/xl-background",
    "lora.xl-pose": "lora/xl-pose",
    "lora.xl-face": "lora/xl-face",
    "lora.xl-suit": "lora/xl-suit",
    "lora.xl-slider": "lora/xl-slider",
    "lora.my-lora": "lora/my-lora",
    "lora.clothes": "lora",  # 默认放到 lora 根目录
    "lora.other": "lora",    # 默认放到 lora 根目录
}

# 获取下载路径
def get_download_path(download_type):
    """根据下载类型获取目标路径"""
    if download_type in PATH_MAPPING:
        subdir = PATH_MAPPING[download_type]
    else:
        # 默认路径
        type_name = download_type.split('.')[0] if '.' in download_type else download_type
        subdir = type_name
    
    return os.path.join(MODEL_BASE_DIR, subdir)

# ============ 清理 ============
def clean_html(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text()
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ============ Civitai 解析 ============
class CivitaiParser:
    BASE_URL = "https://civitai.com"
    API_URL = "https://civitai.com/api"
    
    @classmethod
    def parse_model(cls, url):
        model_id, version_id = cls._extract_ids(url)
        if not model_id:
            raise ValueError(f"Cannot parse URL: {url}")
        
        print(f"[INFO] Fetching model (ID: {model_id}, Version: {version_id})")
        
        # API 数据
        api_info = cls._fetch_api(model_id, version_id)
        
        # 网页数据（提取 Suggested Settings）
        web_info = cls._fetch_webpage(url)
        
        model_info = {
            "model_id": model_id,
            "title": api_info.get("title", "Unknown"),
            "base_model": api_info.get("base_model", "Unknown"),
            "version": api_info.get("version_name", "v1"),
            "description": web_info.get("description", ""),
            "description_clean": web_info.get("description_clean", ""),
            "suggested_settings": web_info.get("suggested_settings", ""),
            "suggested_settings_parsed": web_info.get("suggested_settings_parsed"),
            "download_url": api_info.get("download_url", ""),
            "filename": api_info.get("filename", f"{model_id}.safetensors")
        }
        
        return model_info
    
    @classmethod
    def _fetch_api(cls, model_id, version_id):
        api_url = f"{cls.API_URL}/v1/models/{model_id}"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        result = {
            "title": data.get("name", "Unknown"),
            "base_model": "Unknown",
            "version_name": "v1",
            "download_url": "",
            "filename": f"{model_id}.safetensors"
        }
        
        # 找版本
        for v in data.get("modelVersions", []):
            if version_id and v.get("id") == version_id:
                result["base_model"] = v.get("baseModel", "Unknown")
                result["version_name"] = v.get("name", "v1")
                
                files = v.get("files", [])
                if files:
                    f = files[0]
                    result["download_url"] = f"{cls.API_URL}/download/models/{model_id}?type=Model&format={f.get('format','SafeTensor')}&size={f.get('size','pruned')}&fp={f.get('fp','fp16')}"
                    safe_title = re.sub(r'[<>:"/\\|?*]', '_', data.get("name", "Unknown"))
                    result["filename"] = f"{safe_title}_{v.get('name','v1')}.safetensors"
                break
            elif version_id is None:
                result["base_model"] = v.get("baseModel", "Unknown")
                result["version_name"] = v.get("name", "v1")
                break
        
        return result
    
    @classmethod
    def _fetch_webpage(cls, url):
        print(f"[INFO] Fetching webpage...")
        
        headers = {"User-Agent": "Mozilla/5.0"}
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            print(f"[WARN] Webpage fetch failed: {e}")
            return {"description": "", "description_clean": "", "suggested_settings": "", "suggested_settings_parsed": None}
        
        # 提取 Suggested Settings HTML
        suggested_html = cls._extract_suggested_settings(html)
        
        # 解析为结构化数据
        suggested_parsed = cls._parse_suggested_settings(suggested_html)
        
        return {
            "description": suggested_html,
            "description_clean": clean_html(suggested_html),
            "suggested_settings": suggested_html,
            "suggested_settings_parsed": suggested_parsed
        }
    
    @classmethod
    def _extract_suggested_settings(cls, html):
        """从 HTML 中提取 Suggested Settings <ul>"""
        
        if not html:
            return ""
        
        # 找 <ul> 标签，前面包含 Settings 关键词
        ul_matches = re.findall(r'(?i)(settings|recommended|usage|how to).{0,100}?<ul>(.+?)</ul>', html, re.DOTALL)
        
        for prefix, ul_content in ul_matches:
            clean = clean_html(ul_content)
            if len(clean) > 50 and any(kw in clean for kw in ['CLIP', 'Sampler', 'CFG', 'ENSD', 'Steps']):
                return f"<ul>{ul_content}</ul>"
        
        # 直接找包含 Settings 关键词的 <ul>
        ul_matches = re.findall(r'(?i)<ul>(.+?)</ul>', html, re.DOTALL)
        for ul in ul_matches[:30]:
            if any(kw in ul for kw in ['CLIP', 'Sampler', 'CFG', 'ENSD']):
                return f"<ul>{ul}</ul>"
        
        return ""
    
    @classmethod
    def _parse_suggested_settings(cls, html):
        """
        解析 Suggested Settings HTML，返回结构化数据
        输入: <ul><li>CLIP skip 1</li><li>Samplers : Eular A</li>...</ul>
        输出: {clip_skip: 1, sampler: "Eular A", ...}
        """
        if not html:
            return None
        
        result = {
            "positive_prompts": None,
            "negative_prompts": None,
            "trigger_words": None,
            "sampler": None,
            "cfg_scale": None,
            "clip_skip": None,
            "ensd": None,
            "steps": None,
            "seed": None,
            "resolution": None,
            "hires_fix": None,
            "restore_faces": None,
            "adetailer": None,
            "upscaler": None,
            "other_settings": []
        }
        
        # 提取所有 <li> 项目
        li_items = re.findall(r'<li[^>]*>(.+?)</li>', html, re.DOTALL)
        
        for item in li_items:
            # 清理 HTML 标签，保留内容
            item_clean = clean_html(item).strip()
            if not item_clean:
                continue
            
            item_lower = item_clean.lower()
            
            # CLIP Skip
            if 'clip skip' in item_lower:
                match = re.search(r'(\d+)', item_clean)
                result['clip_skip'] = int(match.group(1)) if match else None
            
            # Sampler
            elif any(kw in item_lower for kw in ['sampler', 'eular', 'dpm++', 'ddim', 'plms', 'lms']):
                # 提取冒号后面的内容
                match = re.search(r'[:\s]+(.+)', item_clean)
                result['sampler'] = match.group(1).strip() if match else item_clean
            
            # CFG Scale
            elif 'cfg' in item_lower:
                match = re.search(r'([\d\.]+(?:-\d+)?)', item_clean)
                result['cfg_scale'] = match.group(1) if match else None
            
            # ENSD
            elif 'ensd' in item_lower:
                match = re.search(r'(\d+)', item_clean)
                result['ensd'] = int(match.group(1)) if match else None
            
            # Steps
            elif 'step' in item_lower:
                match = re.search(r'(\d+)', item_clean)
                result['steps'] = int(match.group(1)) if match else None
            
            # Seed
            elif 'seed' in item_lower and 'negative' not in item_lower:
                match = re.search(r'(-?\d+)', item_clean)
                result['seed'] = match.group(1) if match else None
            
            # Resolution
            elif any(kw in item_lower for kw in ['resolution', 'size', 'width', 'height']):
                match = re.search(r'(\d+x\d+)', item_clean)
                result['resolution'] = match.group(1) if match else None
            
            # Highres Fix
            elif 'highres' in item_lower or 'img2img' in item_lower:
                result['hires_fix'] = True
            
            # Restore Faces
            elif 'restore face' in item_lower:
                result['restore_faces'] = 'don\'t use' not in item_lower
            
            # ADetailer
            elif 'adetailer' in item_lower:
                result['adetailer'] = True
            
            # Upscaler
            elif 'upscaler' in item_lower:
                match = re.search(r'4x[-\s]?(\w+)', item_clean, re.I)
                result['upscaler'] = match.group(1) if match else True
            
            # Positive Prompts
            elif item_lower.startswith('positive'):
                result['positive_prompts'] = re.sub(r'^positive[:\s]*', '', item_clean, flags=re.I).strip()
            
            # Negative Prompts
            elif item_lower.startswith('negative'):
                result['negative_prompts'] = re.sub(r'^negative[:\s]*', '', item_clean, flags=re.I).strip()
            
            # Trigger Words
            elif 'trigger' in item_lower:
                result['trigger_words'] = re.sub(r'^trigger[:\s]*', '', item_clean, flags=re.I).strip()
            
            # 其他设置
            else:
                result['other_settings'].append(item_clean)
        
        # 清理空值
        result = {k: v for k, v in result.items() if v is not None and v != []}
        
        return result if result else None
    
    @classmethod
    def _extract_ids(cls, url):
        model_match = re.search(r'models?/(\d+)', url)
        version_match = re.search(r'[?&]modelVersionId=(\d+)', url)
        model_id = model_match.group(1) if model_match else None
        version_id = int(version_match.group(1)) if version_match else None
        return model_id, version_id


# ============ 任务管理 ============
class TaskManager:
    tasks = {}
    task_lock = threading.Lock()
    
    @classmethod
    def create_task(cls, model_info, task_id=None, download_type=None):
        if task_id is None:
            task_id = str(uuid.uuid4())[:8]
        
        # 计算下载路径
        if download_type:
            download_path = get_download_path(download_type)
        else:
            download_path = DOWNLOAD_DIR
        
        task = {
            "task_id": task_id,
            "model_id": model_info["model_id"],
            "title": model_info["title"],
            "base_model": model_info["base_model"],
            "version": model_info["version"],
            "description_clean": model_info.get("description_clean", ""),
            "suggested_settings": model_info.get("suggested_settings", ""),
            "suggested_settings_parsed": model_info.get("suggested_settings_parsed", {}),
            "download_url": model_info["download_url"],
            "filename": model_info["filename"],
            "download_type": download_type,
            "download_path": download_path,
            "status": "pending",
            "progress": 0,
            "speed": 0,
            "downloaded": 0,
            "total": 0,
            "error": None,
            "created_at": datetime.now().isoformat()
        }
        
        with cls.task_lock:
            cls.tasks[task_id] = task
        
        return task_id
    
    @classmethod
    def get_all_tasks(cls):
        with cls.task_lock:
            return list(cls.tasks.values())
    
    @classmethod
    def update_task(cls, task_id, **kwargs):
        with cls.task_lock:
            if task_id in cls.tasks:
                cls.tasks[task_id].update(kwargs)


# ============ 下载器 ============
class Downloader:
    def __init__(self, task_id, url, filename, download_path=None, model_info=None):
        self.task_id = task_id
        self.url = url
        self.filename = filename
        self.download_path = download_path or DOWNLOAD_DIR
        self.model_info = model_info or {}
        self.chunk_size = 8192
        
        # 创建不使用代理的会话
        self.session = requests.Session()
        self.session.trust_env = False
    
    def _save_metadata_file(self, filepath):
        """保存元数据 txt 文件"""
        try:
            txt_path = filepath.replace('.safetensors', '.txt').replace('.ckpt', '.txt')
            
            with open(txt_path, 'w', encoding='utf-8') as f:
                # 第1行：下载地址
                f.write(f"URL: {self.url}\n")
                
                # 第2行：类型参数
                download_type = self.model_info.get('download_type', '')
                f.write(f"Type: {download_type}\n")
                
                # 第3行：标题
                f.write(f"Title: {self.model_info.get('title', '')}\n")
                
                # 第4行：基模
                f.write(f"Base Model: {self.model_info.get('base_model', '')}\n")
                
                # 第5行：版本
                f.write(f"Version: {self.model_info.get('version', '')}\n")
                
                # 第6行及以后：Suggested Settings
                f.write(f"\n[SUGGESTED_SETTINGS]\n")
                
                # 解析后的结构化数据
                parsed = self.model_info.get('suggested_settings_parsed', {})
                if parsed:
                    for key, value in parsed.items():
                        if value is not None:
                            f.write(f"{key}: {value}\n")
                
                # 原始 HTML
                raw_settings = self.model_info.get('suggested_settings', '')
                if raw_settings:
                    f.write(f"\n[RAW_HTML]\n")
                    f.write(raw_settings)
            
            print(f"[METADATA] {txt_path}")
            return True
        except Exception as e:
            print(f"[WARN] Failed to save metadata: {e}")
            return False
    
    def download(self):
        try:
            TaskManager.update_task(self.task_id, status="downloading")
            print(f"[START] {self.task_id}: {self.filename}")
            
            # 对于 S3 预签名 URL，需要用 GET 而不是 HEAD 获取文件大小
            session = requests.Session()
            session.trust_env = False
            
            response = session.get(self.url, stream=True, timeout=600)
            response.raise_for_status()
            
            # 从 Content-Length 获取大小（如果可用）
            total_size = int(response.headers.get('Content-Length', 0))
            TaskManager.update_task(self.task_id, total=total_size)
            
            filepath = os.path.join(self.download_path, self.filename)
            os.makedirs(self.download_path, exist_ok=True)
            
            downloaded = 0
            last_time = time.time()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if time.time() - last_time >= 1:
                            speed = (downloaded - int(response.headers.get('Content-Length', 0) or 0)) / (time.time() - last_time)
                            TaskManager.update_task(
                                self.task_id,
                                progress=downloaded / total_size if total_size > 0 else 0,
                                downloaded=downloaded,
                                speed=speed
                            )
                            last_time = time.time()
            
            # 下载完成
            TaskManager.update_task(self.task_id, progress=1, downloaded=downloaded, status="completed")
            print(f"[DONE] {self.task_id}: {self.filename}")
            
            # 保存元数据文件
            self._save_metadata_file(filepath)
            
        except Exception as e:
            print(f"[FAIL] {self.task_id}: {e}")
            TaskManager.update_task(self.task_id, status="failed", error=str(e))


# ============ 调度器 ============
class Scheduler:
    MAX_CONCURRENT = MAX_CONCURRENT
    
    @classmethod
    def start_next(cls):
        with TaskManager.task_lock:
            downloading = sum(1 for t in TaskManager.tasks.values() if t["status"] == "downloading")
            
            if downloading >= cls.MAX_CONCURRENT:
                return
            
            for task_id, task in TaskManager.tasks.items():
                if task["status"] == "pending":
                    task["status"] = "downloading"
                    download_path = task.get("download_path", DOWNLOAD_DIR)
                    print(f"[LAUNCH] {task_id} -> {download_path}")
                    
                    # 构建 model_info 传给 Downloader
                    model_info = {
                        "title": task.get("title", ""),
                        "base_model": task.get("base_model", ""),
                        "version": task.get("version", ""),
                        "download_type": task.get("download_type", ""),
                        "suggested_settings": task.get("suggested_settings", ""),
                        "suggested_settings_parsed": task.get("suggested_settings_parsed", {}),
                        "download_url": task["download_url"]
                    }
                    
                    threading.Thread(target=Downloader(
                        task_id, 
                        task["download_url"], 
                        task["filename"],
                        download_path,
                        model_info
                    ).download).start()
                    return
    
    @classmethod
    def add_task(cls, model_info, task_id=None, download_type=None):
        task_id = TaskManager.create_task(model_info, task_id, download_type)
        Scheduler.start_next()
        return task_id


# ============ HTTP Handler ============
class Handler(BaseHTTPRequestHandler):
    def log_message(self, f, *args):
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
        elif path == '/health':
            self.send_json({"status": "ok"})
        elif path.startswith('/parse/'):
            url = path[7:]
            try:
                info = CivitaiParser.parse_model(url)
                self.send_json({"status": "success", "model": info})
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 400)
        else:
            self.send_json({"service": "Civitai Downloader v3.0"})
    
    def do_POST(self):
        path = urlparse(self.path).path
        body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
        data = json.loads(body) if body else {}
        
        if path == '/download':
            url = data.get('url')
            if not url:
                self.send_json({"error": "url required"}, 400)
                return
            
            try:
                info = CivitaiParser.parse_model(url)
                download_type = data.get('type')  # e.g., "ckpt.xl" or "lora.face"
                download_path = get_download_path(download_type) if download_type else DOWNLOAD_DIR
                
                task_id = Scheduler.add_task(info, data.get('task_id'), download_type)
                
                self.send_json({
                    "status": "queued",
                    "task_id": task_id,
                    "title": info["title"],
                    "base_model": info["base_model"],
                    "download_type": download_type,
                    "download_path": download_path,
                    "suggested_settings": info.get("suggested_settings", "")[:300],
                    "filename": info["filename"]
                })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 400)


# ============ 测试 ============
def test():
    test_url = "https://civitai.com/models/1224788?modelVersionId=2578958"
    
    print("=" * 60)
    print("Civitai Test")
    print(f"URL: {test_url}")
    print("=" * 60)
    
    try:
        info = CivitaiParser.parse_model(test_url)
        
        print(f"\n[TITLE] {info['title']}")
        print(f"[BASE MODEL] {info['base_model']}")
        print(f"[VERSION] {info['version']}")
        
        print(f"\n[SUGGESTED SETTINGS - PARSED]")
        parsed = info.get('suggested_settings_parsed')
        if parsed:
            for k, v in parsed.items():
                print(f"  {k}: {v}")
        else:
            print("  (None)")
        
        print(f"\n[DOWNLOAD] {info['download_url']}")
        
        # 测试路径映射
        print(f"\n[PATH MAPPING TEST]")
        for test_type in ["ckpt.xl", "lora.face", "lora.xl-enhance"]:
            path = get_download_path(test_type)
            print(f"  {test_type} -> {path}")
        
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test()
        return
    
    # 解析参数
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python civitai_downloader.py --test                    # 测试解析")
        print("  python civitai_downloader.py --serve [port]           # 启动 API 服务")
        print("  python civitai_downloader.py <url> <type>            # 直接下载")
        print()
        print("Type examples:")
        print("  ckpt.xl       -> D:/ckpt/xl/")
        print("  lora.face     -> D:/lora/xl-face/")
        print("  lora.xl-enhance -> D:/lora/xl-enhance/")
        sys.exit(1)
    
    if sys.argv[1] == "--serve":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        server = HTTPServer((HOST, port), Handler)
        print(f"[INIT] Civitai Downloader v3.0")
        print(f"[LISTEN] http://{HOST}:{port}")
        server.serve_forever()
    
    else:
        # 直接下载模式: python civitai_downloader.py <url> <type>
        url = sys.argv[1]
        download_type = sys.argv[2] if len(sys.argv) > 2 else None
        
        print("=" * 60)
        print("Civitai Direct Download")
        print(f"URL: {url}")
        print(f"Type: {download_type or 'default'}")
        print("=" * 60)
        
        try:
            info = CivitaiParser.parse_model(url)
            
            print(f"\n[TITLE] {info['title']}")
            print(f"[BASE MODEL] {info['base_model']}")
            
            # 计算路径
            if download_type:
                download_path = get_download_path(download_type)
                print(f"[TYPE] {download_type}")
                print(f"[PATH] {download_path}")
            else:
                download_path = DOWNLOAD_DIR
                print(f"[PATH] {download_path}")
            
            # 构建 model_info
            model_info = {
                "title": info["title"],
                "base_model": info["base_model"],
                "version": info.get("version", ""),
                "download_type": download_type or "",
                "suggested_settings": info.get("suggested_settings", ""),
                "suggested_settings_parsed": info.get("suggested_settings_parsed", {}),
                "download_url": info["download_url"]
            }
            
            print(f"\n[START DOWNLOAD]")
            task_id = str(uuid.uuid4())[:8]
            downloader = Downloader(task_id, info["download_url"], info["filename"], download_path, model_info)
            downloader.download()
            
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
