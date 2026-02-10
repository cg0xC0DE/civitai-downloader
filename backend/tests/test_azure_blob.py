#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Azure Blob 存储测试脚本
验证：绘图生成 → 本地保存 → Azure 上传 → API 列表查询
"""

import os
import sys
import time
import requests

# 配置
API_BASE = "http://localhost:53133"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TIMEOUT_SECONDS = 120  # 最长等待时间


def print_step(step: int, msg: str):
    """打印步骤信息"""
    print(f"\n{'='*50}")
    print(f"[Step {step}] {msg}")
    print('='*50)


def print_result(success: bool, msg: str):
    """打印结果"""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status}: {msg}")


def test_azure_blob():
    """主测试流程"""
    print("\n" + "="*60)
    print("🧪 Azure Blob 存储测试")
    print("="*60)
    
    # ========== Step 1: 触发绘图 ==========
    print_step(1, "触发 ComfyUI 绘图")
    
    try:
        resp = requests.post(
            f"{API_BASE}/api/workflow/run",
            json={
                "workflow": "nolora",  # 使用无 LoRA 的基础工作流
                "prompt": "a cute cat, simple background, masterpiece",
                "negative_prompt": "low quality, worst quality",
                "width": 512,
                "height": 512,
                "steps": 10,  # 减少步数加快测试
                "batch_size": 1,  # 只生成1张
                "vary_sizes": False
            },
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('status') == 'error':
            print_result(False, f"绘图请求失败: {data.get('message')}")
            return False
        
        batch_id = data.get('prompt_id')
        print(f"  绘图任务已提交: batch_id={batch_id}")
        print(f"  状态: {data.get('status')}")
        print(f"  消息: {data.get('message')}")
        print_result(True, "绘图任务提交成功")
        
    except requests.exceptions.ConnectionError:
        print_result(False, "无法连接到 API 服务器，请确保 server.py 已启动")
        return False
    except Exception as e:
        print_result(False, f"请求异常: {e}")
        return False
    
    # ========== Step 2: 等待生成完成 ==========
    print_step(2, "等待生成完成")
    
    start_time = time.time()
    task_result = None
    
    while time.time() - start_time < TIMEOUT_SECONDS:
        try:
            resp = requests.get(
                f"{API_BASE}/api/workflow/status",
                params={"prompt_id": batch_id},
                timeout=10
            )
            data = resp.json()
            
            status = data.get('status')
            print(f"  [{int(time.time() - start_time)}s] 状态: {status} - {data.get('message', '')}")
            
            if status == 'success':
                task_result = data
                print_result(True, f"生成完成，共 {data.get('images_count', 0)} 张图片")
                break
            elif status == 'error':
                print_result(False, f"生成失败: {data.get('message')}")
                return False
            
            time.sleep(3)  # 每3秒轮询一次
            
        except Exception as e:
            print(f"  轮询异常: {e}")
            time.sleep(3)
    
    if not task_result:
        print_result(False, f"生成超时（{TIMEOUT_SECONDS}秒）")
        return False
    
    # ========== Step 3: 验证本地保存 ==========
    print_step(3, "验证本地保存")
    
    saved_paths = task_result.get('saved_paths', [])
    local_success = True
    
    if not saved_paths:
        print_result(False, "没有保存的图片路径")
        local_success = False
    else:
        for path in saved_paths:
            if os.path.exists(path):
                size = os.path.getsize(path)
                print(f"  ✓ {path} ({size:,} bytes)")
            else:
                print(f"  ✗ {path} (文件不存在)")
                local_success = False
    
    print_result(local_success, f"本地保存 {len(saved_paths)} 张图片")
    
    # ========== Step 4: 验证 Azure 上传 ==========
    print_step(4, "验证 Azure 上传")
    
    azure_urls = task_result.get('azure_urls', [])
    azure_success = False
    
    if not azure_urls:
        print("  ⚠️  任务返回中没有 azure_urls（可能上传失败或未配置）")
    else:
        print(f"  任务返回的 Azure URLs ({len(azure_urls)}):")
        for url in azure_urls:
            print(f"    🔗 {url}")
        azure_success = True
    
    # 调用 /api/azure/list 验证
    print("\n  调用 /api/azure/list 接口验证...")
    try:
        resp = requests.get(
            f"{API_BASE}/api/azure/list",
            params={"prefix": "generated/", "limit": 20},
            timeout=30
        )
        data = resp.json()
        
        if data.get('success'):
            blobs = data.get('blobs', [])
            print(f"  Azure Blob 列表返回 {len(blobs)} 个文件")
            
            # 检查刚上传的文件是否在列表中
            if azure_urls:
                found_count = 0
                for url in azure_urls:
                    if url in blobs:
                        found_count += 1
                        print(f"    ✓ 已验证: {url.split('/')[-1]}")
                    else:
                        print(f"    ✗ 未找到: {url.split('/')[-1]}")
                
                if found_count == len(azure_urls):
                    azure_success = True
                    print_result(True, f"Azure 上传验证成功，{found_count}/{len(azure_urls)} 文件已确认")
                else:
                    print_result(False, f"部分文件未在列表中找到: {found_count}/{len(azure_urls)}")
            else:
                # 显示最近的几个文件
                print("  最近上传的文件:")
                for url in blobs[:5]:
                    print(f"    📁 {url.split('/')[-1]}")
                print_result(False, "任务未返回 azure_urls，无法验证具体文件")
        else:
            print_result(False, f"API 调用失败: {data.get('error')}")
            
    except Exception as e:
        print_result(False, f"API 调用异常: {e}")
    
    # ========== Step 5: 总结 ==========
    print_step(5, "测试总结")
    
    results = {
        "绘图触发": True,
        "生成完成": task_result is not None,
        "本地保存": local_success,
        "Azure上传": azure_success,
    }
    
    all_pass = all(results.values())
    
    print("\n  测试结果:")
    for name, passed in results.items():
        status = "✅" if passed else "❌"
        print(f"    {status} {name}")
    
    print("\n" + "="*60)
    if all_pass:
        print("🎉 全部测试通过！")
    else:
        print("⚠️  部分测试未通过，请检查配置")
    print("="*60 + "\n")
    
    # 显示生成的图片信息
    if azure_urls:
        print("📸 生成的图片 Azure URL:")
        for url in azure_urls:
            print(f"  {url}")
    
    return all_pass


def test_azure_list_only():
    """仅测试 Azure 列表接口"""
    print("\n" + "="*60)
    print("🔍 测试 Azure Blob 列表接口")
    print("="*60)
    
    try:
        resp = requests.get(
            f"{API_BASE}/api/azure/list",
            params={"prefix": "generated/", "limit": 10},
            timeout=30
        )
        data = resp.json()
        
        if data.get('success'):
            blobs = data.get('blobs', [])
            print(f"\n✅ 成功获取 {data.get('total', len(blobs))} 个文件")
            print("\n最近上传的文件:")
            for i, url in enumerate(blobs[:10], 1):
                filename = url.split('/')[-1]
                print(f"  {i}. {filename}")
                print(f"     {url}")
        else:
            print(f"\n❌ 失败: {data.get('error')}")
            
    except requests.exceptions.ConnectionError:
        print("\n❌ 无法连接到 API 服务器")
    except Exception as e:
        print(f"\n❌ 异常: {e}")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--list':
        # 仅测试列表接口
        test_azure_list_only()
    else:
        # 完整测试
        success = test_azure_blob()
        sys.exit(0 if success else 1)
