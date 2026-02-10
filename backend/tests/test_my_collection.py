#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取用户收藏图片 (My Collection)

API: GET /api/v1/images?isFavorited=true

返回收藏的图片列表，每张图片包含:
- id, url, width, height
- username (作者)
- baseModel (底模)
- modelVersionIds (使用的模型版本)
- createdAt (收藏时间)
"""

import requests
import json

API_BASE = 'https://civitai.com/api/v1'
HEADERS = {'User-Agent': 'Mozilla/5.0'}


def get_favorite_images(limit=20):
    """获取收藏的图片"""
    url = f'{API_BASE}/images?isFavorited=true&limit={limit}'
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_images_with_models(limit=20):
    """获取收藏图片及其模型信息"""
    url = f'{API_BASE}/images?isFavorited=true&limit={limit}'
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    
    results = []
    for item in data.get('items', []):
        # 获取模型信息
        model_ids = item.get('modelVersionIds', [])
        models = []
        for mvid in model_ids[:3]:  # 最多取3个
            try:
                mv_resp = requests.get(
                    f'{API_BASE}/model-versions/{mvid}',
                    headers=HEADERS, timeout=5
                )
                if mv_resp.status_code == 200:
                    mv = mv_resp.json()
                    models.append({
                        'id': mvid,
                        'name': mv.get('model', {}).get('name', 'Unknown'),
                        'version': mv.get('name', 'Unknown'),
                    })
            except:
                pass
        
        results.append({
            'id': item['id'],
            'url': item.get('url', ''),
            'width': item.get('width'),
            'height': item.get('height'),
            'username': item.get('username'),
            'base_model': item.get('baseModel'),
            'created_at': item.get('createdAt'),
            'models': models,
        })
    
    return results


if __name__ == '__main__':
    print("=" * 60)
    print("Civitai 我的收藏图片 (My Collection)")
    print("=" * 60)
    
    # 获取收藏图片
    data = get_favorite_images(limit=10)
    items = data.get('items', [])
    
    print(f"\n收藏数量: {len(items)} 张")
    print()
    
    for i, item in enumerate(items, 1):
        print(f"[{i}] ID: {item['id']}")
        print(f"    URL: {item.get('url', '')[:70]}...")
        print(f"    作者: {item.get('username', 'N/A')}")
        print(f"    底模: {item.get('baseModel', 'N/A')}")
        print(f"    尺寸: {item.get('width')}x{item.get('height')}")
        print(f"    时间: {item.get('createdAt', '')[:10]}")
        print()
