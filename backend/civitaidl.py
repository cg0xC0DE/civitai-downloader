#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Downloader Backend
功能：
- 解析 Civitai URL
- 检查模型是否已存在于 D: 盘
- 下载模型到指定目录
- 生成元数据文件

Usage:
    from civitaidl import CivitaiDownloader
    downloader = CivitaiDownloader()
    result = downloader.download(url, "ckpt.xl")
"""

import os
import sys
import io
import json
import re
import requests
import time
from datetime import datetime

# UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============ 配置（从 config.py 统一引用） ============
sys.path.insert(0, os.path.dirname(__file__))
from config import CIVITAI_API_URL as API_URL, CIVITAI_API_TOKEN, resolve_type_subtype


class CivitaiDownloader:
    """Civitai 下载器"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False  # 禁用代理
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def parse_url(self, url: str) -> dict:
        """
        解析 Civitai URL
        返回: {model_id, version_id, title}
        """
        try:
            # URL 格式: https://civitai.com/models/{model_id} 或带 slug
            # 可能带 ?modelVersionId={version_id}
            model_id_match = re.search(r'civitai\.com/models/(\d+)', url)
            if not model_id_match:
                return {"status": "error", "message": "无法解析 model_id"}
            
            model_id = model_id_match.group(1)
            
            # version_id
            version_id_match = re.search(r'[?&]modelVersionId=(\d+)', url)
            version_id = int(version_id_match.group(1)) if version_id_match else None
            
            return {
                "status": "ok",
                "model_id": model_id,
                "version_id": version_id
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_model_info(self, model_id: str, version_id: int = None) -> dict:
        """
        获取模型信息
        """
        try:
            url = f"{API_URL}/{model_id}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # 找指定版本
            target_version = None
            for v in data.get("modelVersions", []):
                if version_id and v.get("id") == version_id:
                    target_version = v
                    break
                elif version_id is None:
                    target_version = v
                    break
            
            if not target_version:
                return {"status": "error", "message": "未找到版本"}
            
            # 获取文件信息
            files = target_version.get("files", [])
            if not files:
                return {"status": "error", "message": "没有文件"}
            
            file_info = files[0]
            
            return {
                "status": "ok",
                "title": data.get("name", "Unknown"),
                "version_name": target_version.get("name", "v1"),
                "file_name": file_info.get("name", ""),
                "file_size": file_info.get("sizeKB", 0),
                "base_model": target_version.get("baseModel", ""),
                "download_url": file_info.get("downloadUrl", ""),
                "model_id": model_id,
                "version_id": target_version.get("id")
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def check_exists(self, title: str, file_name: str) -> dict:
        """
        D 盘全局判重：遍历 ckpt + lora 所有子目录
        双重 contains 匹配：title 和 file_name
        返回匹配到的完整路径
        """
        from config import CKPT_BASE_DIR, LORA_BASE_DIR, MODEL_EXTENSIONS

        # 准备匹配关键词
        clean_title = re.sub(r'[<>:"/\\|?*\s]', '', title).strip().lower()
        clean_file = os.path.splitext(file_name)[0].lower() if file_name else ''
        # file_name 里常见的分隔符也去掉，做纯字母数字比较
        clean_file_alpha = re.sub(r'[^a-z0-9]', '', clean_file)

        # 遍历所有仓库目录
        for base_dir in [CKPT_BASE_DIR, LORA_BASE_DIR]:
            if not os.path.exists(base_dir):
                continue
            for sub in os.listdir(base_dir):
                subdir = os.path.join(base_dir, sub)
                if not os.path.isdir(subdir):
                    continue
                for f in os.listdir(subdir):
                    if not f.lower().endswith(MODEL_EXTENSIONS):
                        continue

                    f_lower = f.lower()
                    f_stem = os.path.splitext(f_lower)[0]
                    f_alpha = re.sub(r'[^a-z0-9]', '', f_stem)
                    full_path = os.path.join(subdir, f)

                    # 精确文件名匹配
                    if f_lower == file_name.lower():
                        return {
                            "exists": True, "filename": f, "path": full_path,
                            "match_type": "exact",
                            "message": f"文件已存在: {full_path}"
                        }

                    # file_name contains 匹配
                    if clean_file_alpha and len(clean_file_alpha) >= 6 and clean_file_alpha in f_alpha:
                        return {
                            "exists": True, "filename": f, "path": full_path,
                            "match_type": "file_contains",
                            "message": f"疑似重复(文件名匹配): {full_path}"
                        }

                    # title contains 匹配
                    if clean_title and len(clean_title) >= 4 and clean_title in f_alpha:
                        return {
                            "exists": True, "filename": f, "path": full_path,
                            "match_type": "title_contains",
                            "message": f"疑似重复(标题匹配): {full_path}"
                        }

        return {"exists": False, "message": "模型不存在"}
    
    def download_file(self, download_url: str, save_path: str, file_name: str, progress_callback=None) -> dict:
        """
        下载文件
        progress_callback(downloaded, total_size) 可选回调
        """
        try:
            os.makedirs(save_path, exist_ok=True)
            
            # 带上 Civitai API Token（部分模型需要认证）
            dl_url = download_url
            if CIVITAI_API_TOKEN:
                sep = '&' if '?' in dl_url else '?'
                dl_url = f"{dl_url}{sep}token={CIVITAI_API_TOKEN}"
            
            response = self.session.get(dl_url, stream=True, timeout=300, allow_redirects=True)
            response.raise_for_status()
            
            # 检查是否被重定向到登录页
            if 'login' in response.url or 'returnUrl' in response.url:
                return {"status": "error", "message": "需要 Civitai API Token 才能下载此模型，请在 config.py 中配置 CIVITAI_API_TOKEN"}
            
            # 检查 content-type，防止下载到 HTML 页面
            content_type = response.headers.get('content-type', '')
            if 'text/html' in content_type:
                return {"status": "error", "message": f"下载失败：服务器返回了 HTML 页面而非模型文件（可能需要 API Token 或链接已失效）"}
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            file_path = os.path.join(save_path, file_name)
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
            
            # 下载后校验：文件太小可能不对
            if total_size > 0 and downloaded < total_size * 0.9:
                return {"status": "error", "message": f"下载不完整：期望 {total_size} 字节，实际 {downloaded} 字节"}
            
            return {
                "status": "ok",
                "file_path": file_path,
                "file_size": downloaded,
                "message": f"下载完成: {file_name}"
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def save_metadata(self, model_info: dict, target_dir: str) -> str:
        """
        保存元数据文件 (.txt)
        """
        try:
            base_name = os.path.splitext(model_info["file_name"])[0]
            txt_path = os.path.join(target_dir, f"{base_name}.txt")
            
            content = f"""URL: https://civitai.com/models/{model_info['model_id']}?versionId={model_info['version_id']}

Title: {model_info['title']}

Base Model: {model_info['base_model']}

Version: {model_info['version_name']}

File: {model_info['file_name']}

Downloaded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return txt_path
            
        except Exception as e:
            return None
    
    def prepare(self, url: str, type_subtype: str = "ckpt.xl") -> dict:
        """
        预检阶段：解析URL + 调用Civitai API + 判重
        在入队前同步调用，快速返回结果
        """
        result = {"status": "ok", "url": url, "type": type_subtype}

        # 1. 解析 URL
        parse_result = self.parse_url(url)
        if parse_result["status"] != "ok":
            return {**result, "status": "error", "message": parse_result["message"]}

        result["model_id"] = parse_result["model_id"]
        result["version_id"] = parse_result.get("version_id")

        # 2. 获取模型信息
        model_info = self.get_model_info(
            parse_result["model_id"],
            parse_result.get("version_id")
        )
        if model_info["status"] != "ok":
            return {**result, "status": "error", "message": model_info["message"]}

        result["title"] = model_info["title"]
        result["version_name"] = model_info["version_name"]
        result["file_name"] = model_info["file_name"]
        result["model_info"] = model_info

        # 3. 确定保存目录
        resolved = resolve_type_subtype(type_subtype)
        if not resolved:
            return {**result, "status": "error", "message": f"未知类型: {type_subtype}"}
        main_type, subtype, target_dir = resolved
        result["target_dir"] = target_dir

        # 4. D 盘全局判重
        check_result = self.check_exists(model_info["title"], model_info["file_name"])
        if check_result["exists"]:
            return {
                **result,
                "status": "exists",
                "message": check_result["message"],
                "filename": check_result.get("filename"),
                "path": check_result.get("path"),
                "match_type": check_result.get("match_type")
            }

        return result

    def execute_download(self, model_info: dict, target_dir: str, progress_callback=None) -> dict:
        """
        执行阶段：纯下载 + 保存元数据（在 worker 线程中调用）
        """
        result = {
            "title": model_info["title"],
            "version_name": model_info["version_name"],
            "file_name": model_info["file_name"],
        }

        download_result = self.download_file(
            model_info["download_url"],
            target_dir,
            model_info["file_name"],
            progress_callback=progress_callback
        )

        if download_result["status"] != "ok":
            return {**result, "status": "error", "message": download_result["message"]}

        result["saved_path"] = download_result["file_path"]

        txt_path = self.save_metadata(model_info, target_dir)
        if txt_path:
            result["metadata_path"] = txt_path

        return {**result, "status": "ok", "message": f"下载完成: {model_info['file_name']}"}

    def download(self, url: str, type_subtype: str = "ckpt.xl", auto_proxy: bool = True, progress_callback=None) -> dict:
        """
        主下载函数（兼容旧调用）
        """
        prep = self.prepare(url, type_subtype)
        if prep["status"] != "ok":
            return prep

        return self.execute_download(prep["model_info"], prep["target_dir"], progress_callback)


if __name__ == "__main__":
    # 测试
    downloader = CivitaiDownloader()
    
    # 测试解析
    test_url = "https://civitai.com/models/257749"
    print(json.dumps(downloader.parse_url(test_url), ensure_ascii=False, indent=2))
