#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Collections API 测试脚本
用于调研获取登录用户 collections 的接口

## Civitai API 参考

### 已知的 API 端点:
- GET /api/v1/models - 获取模型列表
- GET /api/v1/models/{id} - 获取单个模型详情
- GET /api/v1/model-versions/{id} - 获取版本详情
- GET /api/v1/tags - 获取标签
- GET /api/v1/collections - 公开的 collections 列表

### API Token 认证:
- 部分接口需要认证
- 认证方式: `Authorization: Bearer {token}`
- 或通过 query 参数: `?token={token}`

## 调研结果总结:

### Collections 端点测试结果:

1. 公开 Collections:
   - GET /api/v1/collections - 返回公开的 collections 列表
   - 返回格式: {"items": [...], "metadata": {...}}

2. 用户私有 Collections:
   - /api/v1/user/me - 返回 404 (API 可能已变更)
   - /api/v1/user/collections - 返回 404
   - /api/v1/collections/mine - 返回 404

### 可能的替代方案:

1. 通过 /api/v1/models?filterFavorites=true 获取收藏的模型
2. 使用 web scraping 方式获取用户的 collections 页面
3. 检查 Civitai 网站是否有 GraphQL API

## 使用方法:

    python test_collections.py

"""

import requests
import json
import sys

# ============ 配置 ============
API_BASE = 'https://civitai.com/api/v1'

# 从配置读取 token
try:
    from config import CIVITAI_API_TOKEN
    print(f"[INFO] 使用配置中的 API Token")
except ImportError:
    CIVITAI_API_TOKEN = None
    print("[WARN] 未找到 config.py，使用无认证模式")


def get_headers():
    """生成请求头"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Content-Type': 'application/json',
    }
    if CIVITAI_API_TOKEN:
        headers['Authorization'] = f'Bearer {CIVITAI_API_TOKEN}'
    return headers


def test_endpoint(name, url, indent=0):
    """测试 API 端点"""
    prefix = "  " * indent
    print(f"\n{prefix}{'='*50}")
    print(f"{prefix}[TEST] {name}")
    print(f"{prefix}[URL]  {url}")
    
    try:
        resp = requests.get(url, headers=get_headers(), timeout=10)
        print(f"{prefix}[Status] {resp.status_code}")
        
        if resp.status_code == 200:
            try:
                data = resp.json()
                print(f"{prefix}[OK] 返回 JSON 数据")
                return data
            except:
                print(f"{prefix}[OK] 返回文本: {resp.text[:200]}")
                return resp.text
        else:
            print(f"{prefix}[FAIL] {resp.text[:150]}")
            return None
            
    except Exception as e:
        print(f"{prefix}[ERROR] {e}")
        return None


def main():
    print("=" * 60)
    print("Civitai Collections API 调研测试")
    print("=" * 60)
    
    if CIVITAI_API_TOKEN:
        print(f"[Token] {CIVITAI_API_TOKEN[:8]}...")
    else:
        print("[Token] 未配置")
    
    # 1. 测试公开 Collections 列表
    print("\n" + "=" * 60)
    print("[1] 公开 Collections 列表")
    print("=" * 60)
    
    collections = test_endpoint(
        "公开 Collections",
        f"{API_BASE}/collections?limit=5"
    )
    
    if collections and isinstance(collections, dict) and 'items' in collections:
        print("\n[Collections 结构]")
        for item in collections['items'][:3]:
            print(f"  - {item.get('name', 'Unknown')} (id={item.get('id')})")
            print(f"    creator: {item.get('creator', {}).get('username', 'N/A')}")
            print(f"    models: {item.get('modelCount', 'N/A')}")
    
    # 2. 测试用户相关接口
    print("\n" + "=" * 60)
    print("[2] 用户相关接口 (需要认证)")
    print("=" * 60)
    
    user_endpoints = [
        ("当前用户信息", f"{API_BASE}/user/me"),
        ("用户详情", f"{API_BASE}/user"),
        ("用户 Collections", f"{API_BASE}/user/collections"),
        ("用户 Models", f"{API_BASE}/user/models"),
    ]
    
    for name, url in user_endpoints:
        test_endpoint(name, url)
    
    # 3. 测试其他可能的接口
    print("\n" + "=" * 60)
    print("[3] 其他相关接口")
    print("=" * 60)
    
    other_endpoints = [
        ("收藏的模型", f"{API_BASE}/models?filterFavorites=true"),
        ("用户的模型", f"{API_BASE}/models?username=me"),
    ]
    
    for name, url in other_endpoints:
        test_endpoint(name, url)
    
    # 4. 查看 Models API 结构
    print("\n" + "=" * 60)
    print("[4] Models API 结构参考")
    print("=" * 60)
    
    models = test_endpoint("模型列表", f"{API_BASE}/models?limit=1")
    
    if models and isinstance(models, dict):
        print("\n[Models API 返回结构]")
        print(f"  keys: {list(models.keys())}")
        if 'items' in models and models['items']:
            item = models['items'][0]
            print(f"\n  单个模型字段:")
            for key in list(item.keys())[:10]:
                print(f"    - {key}")
    
    # 5. 总结
    print("\n" + "=" * 60)
    print("[5] 调研总结")
    print("=" * 60)
    
    print("""
## Civitai API Collections 接口调研结果

### 当前状态:
- 公开 Collections: ✅ /api/v1/collections 可用
- 私有用户 Collections: ❌ /api/v1/user/me 等返回 404

### 可能原因:
1. API 版本已变更，旧端点不再可用
2. 需要特定的 API 权限
3. Collections 功能可能需要使用 GraphQL

### 建议:
1. 检查 Civitai 官方最新 API 文档
2. 尝试使用 GraphQL API
3. 或者通过网页抓取获取用户 Collections

### 参考链接:
- https://github.com/civitai/civitai/wiki/REST-API-Reference
- https://docs.civitai.com/
""")


if __name__ == '__main__':
    main()
