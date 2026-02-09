# ============================================================
# Azure Blob Storage 便捷工具
# 复制粘贴即用，container = project（一个项目一个容器）
# ============================================================

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
from typing import Any, List, Optional, Tuple

import requests
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings

try:
    from .credentials import CONNECTION_STRING
except ModuleNotFoundError:  # pragma: no cover
    CONNECTION_STRING = ''

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BlobStorage:
    """
    Azure Blob Storage 便捷封装工具
    
    路径结构: {container=project}/{subfolder}/{filename}
    - container: 初始化时指定，即为项目名（一个项目对应一个容器）
    - subfolder: 可选，支持多级目录如 "data/images"
    - filename: 文件名
    
    也支持简化调用：直接传入完整 blob_path
    """
    
    # 默认 Blob URL 前缀（用于拼接完整访问地址）
    DEFAULT_BLOB_URL_PREFIX = "https://chatarchive.blob.core.windows.net"
    
    def __init__(self, project: str = None, container: str = None, connection_string: str = None):
        """
        初始化 BlobStorage
        
        Args:
            project: 项目名称（即 container 名称）
            container: container 名称（project 的别名，二选一）
            connection_string: Azure连接字符串，默认使用 credentials.py 中的配置
        """
        # 支持 project 或 container 参数
        self._project = project or container
        if not self._project:
            raise ValueError("project 或 container 参数不能为空")
        
        self._conn_str = connection_string or CONNECTION_STRING
        if not self._conn_str:
            raise ValueError(
                "Azure Blob connection string is empty. Please create azure_blob/credentials.py "
                "and set CONNECTION_STRING"
            )
        
        logger.info(f"[BlobStorage] 初始化: project={self._project}")
        self._client = BlobServiceClient.from_connection_string(self._conn_str)
        self._container_client = self._client.get_container_client(self._project)
        logger.info(f"[BlobStorage] 连接成功")
    
    def _build_blob_path(self, subfolder: Optional[str], filename: str) -> str:
        """
        构建 blob 路径
        
        Args:
            subfolder: 子文件夹路径（可选），支持多级如 "data/images"
            filename: 文件名
        
        Returns:
            blob 路径
        """
        if not filename:
            raise ValueError("filename 参数不能为空")
        
        parts = []
        if subfolder:
            # 规范化路径分隔符，去除首尾斜杠
            subfolder = subfolder.replace("\\", "/").strip("/")
            if subfolder:
                parts.append(subfolder)
        parts.append(filename)
        
        blob_path = "/".join(parts)
        logger.debug(f"[BlobStorage] 构建路径: {blob_path}")
        return blob_path
    
    def _split_blob_path(self, blob_path: str) -> Tuple[Optional[str], str]:
        """
        拆分 blob_path 为 subfolder 和 filename
        
        Args:
            blob_path: 完整的 blob 路径，如 "generated/2026-02-09/abc.png"
        
        Returns:
            (subfolder, filename) 元组
        """
        blob_path = blob_path.replace("\\", "/").strip("/")
        if "/" in blob_path:
            parts = blob_path.rsplit("/", 1)
            return parts[0], parts[1]
        return None, blob_path
    
    def get_blob_url(self, blob_path: str) -> str:
        """
        获取 blob 的完整访问 URL
        
        Args:
            blob_path: blob 路径
        
        Returns:
            完整的 blob URL
        """
        return f"{self.DEFAULT_BLOB_URL_PREFIX}/{self._project}/{blob_path}"
    
    # ==================== 查询操作 ====================
    
    def list_blobs(self, subfolder: Optional[str] = None, 
                   prefix: Optional[str] = None) -> List[str]:
        """
        列出指定路径下的所有 blob 名称
        
        Args:
            subfolder: 子文件夹路径（可选）
            prefix: 额外的文件名前缀过滤（可选）
        
        Returns:
            blob 名称列表
        """
        search_prefix = ""
        if subfolder:
            subfolder = subfolder.replace("\\", "/").strip("/")
            if subfolder:
                search_prefix = subfolder + "/"
        if prefix:
            search_prefix = search_prefix + prefix
        
        logger.info(f"[BlobStorage] list_blobs: project={self._project}, prefix={search_prefix or '(root)'}")
        blob_list = self._container_client.list_blob_names(name_starts_with=search_prefix if search_prefix else None)
        result = list(blob_list)
        logger.info(f"[BlobStorage] list_blobs: 找到 {len(result)} 个文件")
        return result
    
    def list_recent_blobs(self, prefix: str = None, max_results: int = 100, 
                          return_urls: bool = True) -> List[str]:
        """
        按时间倒序列出最近的 blob
        
        Args:
            prefix: 路径前缀过滤（如 "generated/"）
            max_results: 最大返回数量，默认 100
            return_urls: 是否返回完整 URL，默认 True
        
        Returns:
            blob 路径或 URL 列表（按 LastModified 降序）
        """
        logger.info(f"[BlobStorage] list_recent_blobs: project={self._project}, prefix={prefix}, max={max_results}")
        
        # 获取 blob 列表（包含属性）
        blobs = self._container_client.list_blobs(name_starts_with=prefix)
        
        # 转换为列表并按 last_modified 降序排序
        blob_list = []
        for blob in blobs:
            blob_list.append({
                'name': blob.name,
                'last_modified': blob.last_modified
            })
        
        # 按时间降序排序
        blob_list.sort(key=lambda x: x['last_modified'], reverse=True)
        
        # 限制数量
        blob_list = blob_list[:max_results]
        
        # 返回结果
        if return_urls:
            result = [self.get_blob_url(b['name']) for b in blob_list]
        else:
            result = [b['name'] for b in blob_list]
        
        logger.info(f"[BlobStorage] list_recent_blobs: 返回 {len(result)} 个文件")
        return result
    
    def list_blobs_by_time(self, subfolder: Optional[str] = None, 
                           prefix: Optional[str] = None,
                           max_results: int = 100) -> List[dict]:
        """
        按时间列出 blob（最新在前）
        
        Args:
            subfolder: 子文件夹路径（可选）
            prefix: 额外的文件名前缀过滤（可选）
            max_results: 最大返回数量，默认 100
        
        Returns:
            [{"path": "generated/2026-02-09/xxx.png", "time": "2026-02-09T12:00:00Z", "url": "..."}, ...]
        """
        # 构建搜索前缀
        search_prefix = ""
        if subfolder:
            subfolder = subfolder.replace("\\", "/").strip("/")
            if subfolder:
                search_prefix = subfolder + "/"
        if prefix:
            search_prefix = search_prefix + prefix
        
        logger.info(f"[BlobStorage] list_blobs_by_time: project={self._project}, prefix={search_prefix or '(root)'}, max={max_results}")
        
        # 获取 blob 列表（包含属性）
        blobs = self._container_client.list_blobs(name_starts_with=search_prefix if search_prefix else None)
        
        # 转换为列表
        blob_list = []
        for blob in blobs:
            blob_list.append({
                'path': blob.name,
                'time': blob.last_modified.isoformat() if blob.last_modified else None,
                'url': self.get_blob_url(blob.name),
                '_sort_key': blob.last_modified
            })
        
        # 按时间降序排序
        blob_list.sort(key=lambda x: x['_sort_key'] or '', reverse=True)
        
        # 限制数量
        blob_list = blob_list[:max_results]
        
        # 移除内部排序字段
        for item in blob_list:
            del item['_sort_key']
        
        logger.info(f"[BlobStorage] list_blobs_by_time: 返回 {len(blob_list)} 个文件")
        return blob_list
    
    def exists(self, subfolder: Optional[str], filename: str) -> bool:
        """
        检查 blob 是否存在
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名
        
        Returns:
            True 如果存在，否则 False
        """
        blob_path = self._build_blob_path(subfolder, filename)
        blob_client = self._client.get_blob_client(container=self._project, blob=blob_path)
        exists = blob_client.exists()
        logger.info(f"[BlobStorage] exists: {self._project}/{blob_path} -> {exists}")
        return exists
    
    # ==================== 读取操作 ====================
    
    def get_bytes(self, subfolder: Optional[str], filename: str) -> Optional[bytes]:
        """
        获取 blob 的原始字节数据
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名
        
        Returns:
            字节数据，如果不存在返回 None
        """
        blob_path = self._build_blob_path(subfolder, filename)
        blob_client = self._client.get_blob_client(container=self._project, blob=blob_path)
        
        logger.info(f"[BlobStorage] get_bytes: {self._project}/{blob_path}")
        try:
            data = blob_client.download_blob().readall()
            logger.info(f"[BlobStorage] get_bytes: 成功, 大小={len(data)} bytes")
            return data
        except ResourceNotFoundError:
            logger.warning(f"[BlobStorage] get_bytes: 文件不存在 {self._project}/{blob_path}")
            return None
    
    def get_text(self, subfolder: Optional[str], filename: str, 
                 encoding: str = "utf-8") -> Optional[str]:
        """
        获取 blob 的文本内容
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名
            encoding: 文本编码，默认 utf-8
        
        Returns:
            文本内容，如果不存在返回 None
        """
        blob_path = self._build_blob_path(subfolder, filename)
        logger.info(f"[BlobStorage] get_text: {self._project}/{blob_path}")
        
        raw = self.get_bytes(subfolder, filename)
        if raw is None:
            return None
        
        text = raw.decode(encoding)
        logger.info(f"[BlobStorage] get_text: 成功, 长度={len(text)} chars")
        return text
    
    def get_json(self, subfolder: Optional[str], filename: str) -> Optional[Any]:
        """
        获取 blob 并解析为 JSON 对象
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名（建议以 .json 结尾）
        
        Returns:
            解析后的 JSON 对象，如果不存在或解析失败返回 None
        """
        blob_path = self._build_blob_path(subfolder, filename)
        logger.info(f"[BlobStorage] get_json: {self._project}/{blob_path}")
        
        text = self.get_text(subfolder, filename)
        if text is None:
            return None
        
        try:
            obj = json.loads(text)
            logger.info(f"[BlobStorage] get_json: 解析成功")
            return obj
        except json.JSONDecodeError as e:
            logger.error(f"[BlobStorage] get_json: JSON解析失败 - {e}")
            return None
    
    def get_image_base64(self, subfolder: Optional[str], filename: str) -> Optional[str]:
        """
        获取图片并转换为 base64 编码
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 图片文件名
        
        Returns:
            base64 编码的字符串，如果不存在返回 None
        """
        blob_path = self._build_blob_path(subfolder, filename)
        logger.info(f"[BlobStorage] get_image_base64: {self._project}/{blob_path}")
        
        raw = self.get_bytes(subfolder, filename)
        if raw is None:
            return None
        
        b64 = base64.b64encode(raw).decode("utf-8")
        logger.info(f"[BlobStorage] get_image_base64: 成功, base64长度={len(b64)}")
        return b64
    
    # ==================== 写入操作 ====================
    
    def put_bytes(self, data_or_subfolder, filename_or_path: str = None,
                  data: bytes = None, content_type: str = None,
                  overwrite: bool = True) -> str:
        """
        写入字节数据到 blob（支持两种调用方式）
        
        方式1（简化）: put_bytes(data, blob_path)
        方式2（完整）: put_bytes(subfolder, filename, data=data, content_type=...)
        
        Args:
            data_or_subfolder: 字节数据（方式1）或子文件夹路径（方式2）
            filename_or_path: blob路径（方式1）或文件名（方式2）
            data: 字节数据（方式2）
            content_type: MIME类型，默认根据扩展名推断
            overwrite: 是否覆盖已存在的文件
        
        Returns:
            上传成功返回 blob URL，失败抛出异常
        """
        # 判断调用方式
        if isinstance(data_or_subfolder, bytes):
            # 方式1: put_bytes(data, blob_path)
            actual_data = data_or_subfolder
            blob_path = filename_or_path.replace("\\", "/").strip("/")
        else:
            # 方式2: put_bytes(subfolder, filename, data=data)
            if data is None:
                raise ValueError("方式2调用需要提供 data 参数")
            actual_data = data
            blob_path = self._build_blob_path(data_or_subfolder, filename_or_path)
        
        # 推断 content_type
        if not content_type:
            ext = os.path.splitext(blob_path)[1].lower().lstrip(".")
            ext_map = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "webp": "image/webp",
                "svg": "image/svg+xml",
                "pdf": "application/pdf",
                "json": "application/json",
                "txt": "text/plain",
            }
            content_type = ext_map.get(ext, "application/octet-stream")
        
        blob_client = self._client.get_blob_client(container=self._project, blob=blob_path)
        
        logger.info(f"[BlobStorage] put_bytes: {self._project}/{blob_path}, size={len(actual_data)}, overwrite={overwrite}")
        
        if not overwrite and blob_client.exists():
            logger.warning(f"[BlobStorage] put_bytes: 文件已存在且不覆盖 {self._project}/{blob_path}")
            # 返回现有文件的 URL
            return self.get_blob_url(blob_path)
        
        settings = ContentSettings(content_type=content_type)
        blob_client.upload_blob(actual_data, overwrite=overwrite, content_settings=settings)
        
        url = self.get_blob_url(blob_path)
        logger.info(f"[BlobStorage] put_bytes: 写入成功 -> {url}")
        return url
    
    def put_text(self, subfolder: Optional[str], filename: str,
                 text: str, content_type: str = "text/plain; charset=utf-8",
                 overwrite: bool = True) -> str:
        """
        写入文本到 blob
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名
            text: 文本内容
            content_type: MIME类型
            overwrite: 是否覆盖已存在的文件
        
        Returns:
            上传成功返回 blob URL
        """
        blob_path = self._build_blob_path(subfolder, filename)
        logger.info(f"[BlobStorage] put_text: {self._project}/{blob_path}, length={len(text)}")
        
        return self.put_bytes(subfolder, filename, data=text.encode("utf-8"), 
                              content_type=content_type, overwrite=overwrite)
    
    def put_json(self, subfolder: Optional[str], filename: str,
                 obj: Any, overwrite: bool = True, indent: Optional[int] = None) -> str:
        """
        将对象序列化为 JSON 并写入 blob
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名（建议以 .json 结尾）
            obj: 可序列化为 JSON 的对象
            overwrite: 是否覆盖已存在的文件
            indent: JSON缩进（None表示紧凑格式）
        
        Returns:
            上传成功返回 blob URL
        """
        blob_path = self._build_blob_path(subfolder, filename)
        logger.info(f"[BlobStorage] put_json: {self._project}/{blob_path}")
        
        text = json.dumps(obj, ensure_ascii=False, indent=indent)
        return self.put_text(subfolder, filename, text,
                             content_type="application/json; charset=utf-8",
                             overwrite=overwrite)
    
    def upload_from_url(self, url: str, subfolder: Optional[str],
                        filename: str, overwrite: bool = False,
                        content_type: Optional[str] = None) -> str:
        """
        从 URL 下载文件并上传到 blob（常用于图片）
        
        Args:
            url: 源文件 URL
            subfolder: 子文件夹路径（可选）
            filename: 目标文件名
            overwrite: 是否覆盖已存在的文件
            content_type: MIME类型（不指定则根据扩展名推断）
        
        Returns:
            上传成功返回 blob URL
        """
        blob_path = self._build_blob_path(subfolder, filename)
        blob_client = self._client.get_blob_client(container=self._project, blob=blob_path)
        
        logger.info(f"[BlobStorage] upload_from_url: {url} -> {self._project}/{blob_path}")
        
        if not overwrite and blob_client.exists():
            logger.warning(f"[BlobStorage] upload_from_url: 文件已存在且不覆盖 {self._project}/{blob_path}")
            return self.get_blob_url(blob_path)
        
        try:
            resp = requests.get(url, stream=True, timeout=30)
            resp.raise_for_status()
            
            # 临时文件处理
            suffix = os.path.splitext(filename)[1].lstrip(".") or "bin"
            tmpdir = tempfile.mkdtemp()
            tmpfile = os.path.join(tmpdir, f"tmp.{suffix}")
            
            with open(tmpfile, "wb") as f:
                shutil.copyfileobj(resp.raw, f)
            del resp
            
            with open(tmpfile, "rb") as f:
                data = f.read()
            
            result = self.put_bytes(subfolder, filename, data=data, 
                                    content_type=content_type, overwrite=True)
            
            # 清理临时文件
            os.remove(tmpfile)
            os.rmdir(tmpdir)
            
            logger.info(f"[BlobStorage] upload_from_url: 成功")
            return result
        except Exception as e:
            logger.error(f"[BlobStorage] upload_from_url: 失败 - {e}")
            raise
    
    # ==================== 删除操作 ====================
    
    def delete(self, subfolder: Optional[str], filename: str) -> bool:
        """
        删除指定的 blob
        
        Args:
            subfolder: 子文件夹路径（可选）
            filename: 文件名
        
        Returns:
            True 删除成功，False 文件不存在或删除失败
        """
        blob_path = self._build_blob_path(subfolder, filename)
        blob_client = self._client.get_blob_client(container=self._project, blob=blob_path)
        
        logger.info(f"[BlobStorage] delete: {self._project}/{blob_path}")
        
        try:
            blob_client.delete_blob()
            logger.info(f"[BlobStorage] delete: 成功")
            return True
        except ResourceNotFoundError:
            logger.warning(f"[BlobStorage] delete: 文件不存在 {self._project}/{blob_path}")
            return False
