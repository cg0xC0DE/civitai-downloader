#!/usr/bin/env python3
"""
Civitai Model Download URL Parser
用法: python civitai_download.py 模型ID
"""

import requests
import sys

def parse_civitai_download(model_id, model_type="Model", format="SafeTensor", size="pruned", fp="fp16"):
    url = f"https://civitai.com/api/download/models/{model_id}"
    params = {
        "type": model_type,
        "format": format,
        "size": size,
        "fp": fp
    }
    
    print(f"\n=== Civitai Download Parser ===")
    print(f"API URL: {url}")
    print(f"参数: {params}")
    
    try:
        # 跟随重定向
        response = requests.get(url, params=params, allow_redirects=True, timeout=30)
        
        print(f"\n最终 URL: {response.url}")
        print(f"状态码: {response.status_code}")
        
        # 解析存储提供商
        from urllib.parse import urlparse
        domain = urlparse(response.url).netloc
        
        if "cloudflarestorage" in domain:
            print(f"\n📦 存储提供商: Cloudflare R2")
        else:
            print(f"\n📦 存储提供商: {domain}")
        
        # 如果是要下载，可以检查 Content-Disposition
        content_disposition = response.headers.get('Content-Disposition', '')
        if content_disposition:
            print(f"文件名: {content_disposition}")
        
        print(f"\n文件大小: {len(response.content) / (1024*1024):.2f} MB")
        
        # 保存文件
        filename = f"{model_id}.safetensors"
        with open(filename, 'wb') as f:
            f.write(response.content)
        print(f"\n✅ 已保存为: {filename}")
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")

if __name__ == "__main__":
    model_id = sys.argv[1] if len(sys.argv) > 1 else "2579173"
    parse_civitai_download(model_id)
