# -*- coding: utf-8 -*-
"""Shared Azure Blob utilities."""

def _azure_available() -> bool:
    """检查 Azure Blob 存储是否已配置（连接字符串存在）"""
    try:
        from azure_blob.credentials import CONNECTION_STRING
        return bool(CONNECTION_STRING)
    except Exception:
        return False
