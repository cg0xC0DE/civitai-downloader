#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Downloader - 环境初始化 & 自检脚本
检测 Python、GPU、CUDA、venv、PyTorch、项目路径等，确保项目可以无痛启动。

Usage:
    python init.py          # 完整检测 + 自动修复
    python init.py --check  # 仅检测不修复
"""

import os
import sys
import subprocess
import shutil
import platform
import json
import re
import argparse

# ============ 常量 ============
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(PROJECT_ROOT, 'backend')
VENV_DIR = os.path.join(BACKEND_DIR, 'venv')
REQUIREMENTS_FILE = os.path.join(BACKEND_DIR, 'requirements.txt')
CONFIG_FILE = os.path.join(BACKEND_DIR, 'config.py')

MIN_PYTHON = (3, 10)
# PyTorch CUDA 版本与 pip index URL 对照
TORCH_CUDA_INDEX = {
    '11.8': 'https://download.pytorch.org/whl/cu118',
    '12.1': 'https://download.pytorch.org/whl/cu121',
    '12.4': 'https://download.pytorch.org/whl/cu124',
    '12.6': 'https://download.pytorch.org/whl/cu126',
}

# ============ 工具函数 ============
class Colors:
    """ANSI 颜色（Windows 10+ 支持）"""
    RESET = '\033[0m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

def ok(msg):    print(f"  {Colors.GREEN}✔{Colors.RESET} {msg}")
def warn(msg):  print(f"  {Colors.YELLOW}⚠{Colors.RESET} {msg}")
def fail(msg):  print(f"  {Colors.RED}✘{Colors.RESET} {msg}")
def info(msg):  print(f"  {Colors.CYAN}ℹ{Colors.RESET} {msg}")
def heading(msg): print(f"\n{Colors.BOLD}{Colors.CYAN}{'─'*50}{Colors.RESET}\n{Colors.BOLD}  {msg}{Colors.RESET}\n{Colors.CYAN}{'─'*50}{Colors.RESET}")

def run_cmd(cmd, timeout=30, **kwargs):
    """执行命令，返回 (returncode, stdout, stderr)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, '', f'Command not found: {cmd[0]}'
    except subprocess.TimeoutExpired:
        return -2, '', 'Timeout'
    except Exception as e:
        return -3, '', str(e)


# ============ 检测项 ============

def check_python():
    """检测 Python 版本 >= 3.10"""
    heading("1. Python 版本")
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if (ver.major, ver.minor) >= MIN_PYTHON:
        ok(f"Python {ver_str}  (路径: {sys.executable})")
        return True
    else:
        fail(f"Python {ver_str} 低于要求的 {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
        info("请安装 Python 3.10+ : https://www.python.org/downloads/")
        return False


def check_gpu():
    """检测 NVIDIA GPU"""
    heading("2. NVIDIA 显卡")
    rc, out, err = run_cmd(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'])
    if rc == 0 and out:
        for line in out.strip().split('\n'):
            ok(line.strip())
        return True
    else:
        fail("未检测到 NVIDIA GPU 或 nvidia-smi 不可用")
        info("请确认已安装 NVIDIA 显卡驱动")
        return False


def check_cuda():
    """检测 CUDA Toolkit"""
    heading("3. CUDA Toolkit")
    
    # 方法1: nvcc
    rc, out, _ = run_cmd(['nvcc', '--version'])
    if rc == 0:
        match = re.search(r'release (\d+\.\d+)', out)
        if match:
            cuda_ver = match.group(1)
            ok(f"CUDA {cuda_ver}  (nvcc)")
            return cuda_ver

    # 方法2: nvidia-smi 显示的 CUDA 版本（驱动支持的最高版本）
    rc, out, _ = run_cmd(['nvidia-smi'])
    if rc == 0:
        match = re.search(r'CUDA Version:\s*(\d+\.\d+)', out)
        if match:
            cuda_ver = match.group(1)
            ok(f"CUDA {cuda_ver}  (驱动支持最高版本，nvidia-smi)")
            warn("未检测到 nvcc，建议安装 CUDA Toolkit 以获取完整开发支持")
            return cuda_ver

    fail("未检测到 CUDA")
    info("请安装 CUDA Toolkit: https://developer.nvidia.com/cuda-downloads")
    return None


def check_venv(fix=False):
    """检测 / 创建 venv"""
    heading("4. Python 虚拟环境 (venv)")
    venv_python = os.path.join(VENV_DIR, 'Scripts', 'python.exe') if os.name == 'nt' \
                  else os.path.join(VENV_DIR, 'bin', 'python')
    
    if os.path.isfile(venv_python):
        # 验证 venv 是否可用
        rc, out, _ = run_cmd([venv_python, '--version'])
        if rc == 0:
            ok(f"venv 已存在  ({out})")
            ok(f"路径: {VENV_DIR}")
            return venv_python
        else:
            warn("venv 目录存在但 Python 不可用，可能已损坏")
            if fix:
                info("删除并重建 venv...")
                shutil.rmtree(VENV_DIR, ignore_errors=True)
            else:
                info("使用 --fix 模式可自动重建")
                return None

    if not fix:
        fail("venv 不存在")
        info("运行 python init.py（不带 --check）可自动创建")
        return None

    # 创建 venv
    info(f"正在创建 venv: {VENV_DIR}")
    rc, out, err = run_cmd([sys.executable, '-m', 'venv', VENV_DIR], timeout=120)
    if rc != 0:
        fail(f"创建 venv 失败: {err}")
        return None
    ok("venv 创建成功")

    # 升级 pip
    info("升级 pip...")
    run_cmd([venv_python, '-m', 'pip', 'install', '--upgrade', 'pip', '-q'], timeout=120)
    ok("pip 已升级")

    return venv_python


def check_dependencies(venv_python, fix=False):
    """检测 / 安装 requirements.txt 依赖"""
    heading("5. Python 依赖")
    if not venv_python:
        fail("跳过（venv 不可用）")
        return False

    if not os.path.isfile(REQUIREMENTS_FILE):
        warn(f"requirements.txt 不存在: {REQUIREMENTS_FILE}")
        return True

    # 解析需求
    with open(REQUIREMENTS_FILE, 'r') as f:
        reqs = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not reqs:
        ok("requirements.txt 为空，无额外依赖")
        return True

    # 检查已安装
    rc, out, _ = run_cmd([venv_python, '-m', 'pip', 'list', '--format=json'], timeout=30)
    installed = {}
    if rc == 0:
        try:
            for pkg in json.loads(out):
                installed[pkg['name'].lower()] = pkg['version']
        except:
            pass

    missing = []
    for req in reqs:
        pkg_name = re.split(r'[>=<!\[]', req)[0].strip().lower()
        if pkg_name and pkg_name not in installed:
            missing.append(req)

    if not missing:
        ok(f"所有依赖已安装 ({len(reqs)} 个包)")
        for req in reqs:
            pkg_name = re.split(r'[>=<!\[]', req)[0].strip().lower()
            info(f"  {pkg_name} == {installed.get(pkg_name, '?')}")
        return True

    if not fix:
        for m in missing:
            fail(f"缺少: {m}")
        info("运行 python init.py（不带 --check）可自动安装")
        return False

    info(f"安装 {len(missing)} 个缺少的包...")
    rc, out, err = run_cmd(
        [venv_python, '-m', 'pip', 'install', '-r', REQUIREMENTS_FILE, '-q'],
        timeout=300
    )
    if rc == 0:
        ok("依赖安装完成")
        return True
    else:
        fail(f"安装失败: {err[:200]}")
        return False


def check_pytorch(venv_python, cuda_ver):
    """检测 PyTorch 是否安装且支持当前 CUDA"""
    heading("6. PyTorch & CUDA 兼容性")
    if not venv_python:
        fail("跳过（venv 不可用）")
        return False

    # 检查 torch 是否已安装
    rc, out, _ = run_cmd([
        venv_python, '-c',
        'import torch; print(torch.__version__); print(torch.cuda.is_available()); '
        'print(torch.version.cuda or "None"); '
        'print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A")'
    ], timeout=30)

    if rc == 0:
        lines = out.strip().split('\n')
        torch_ver = lines[0] if len(lines) > 0 else '?'
        cuda_available = lines[1].strip() == 'True' if len(lines) > 1 else False
        torch_cuda = lines[2] if len(lines) > 2 else '?'
        device_name = lines[3] if len(lines) > 3 else '?'

        ok(f"PyTorch {torch_ver}")
        if cuda_available:
            ok(f"CUDA 可用  (torch.cuda: {torch_cuda}, 设备: {device_name})")
            return True
        else:
            warn(f"PyTorch 已安装但 CUDA 不可用 (torch.version.cuda={torch_cuda})")
            if cuda_ver:
                _suggest_torch_install(cuda_ver)
            return False
    else:
        warn("PyTorch 未安装")
        if cuda_ver:
            _suggest_torch_install(cuda_ver)
        else:
            info("请先安装 CUDA，再安装对应版本的 PyTorch")
        return False


def _suggest_torch_install(cuda_ver):
    """根据 CUDA 版本给出 PyTorch 安装命令建议"""
    # 找到最接近的支持版本
    major_minor = cuda_ver  # e.g. "12.4"
    major = cuda_ver.split('.')[0]  # e.g. "12"

    best_match = None
    for supported in sorted(TORCH_CUDA_INDEX.keys(), reverse=True):
        if major_minor.startswith(supported[:4]) or supported.startswith(major):
            best_match = supported
            break
    if not best_match:
        best_match = sorted(TORCH_CUDA_INDEX.keys())[-1]  # 用最新的

    index_url = TORCH_CUDA_INDEX[best_match]
    info(f"建议安装命令 (CUDA {best_match}):")
    print(f"\n    {Colors.BOLD}pip install torch torchvision --index-url {index_url}{Colors.RESET}\n")


def check_project_paths():
    """检测项目关键路径和配置"""
    heading("7. 项目路径 & 配置")
    all_ok = True

    # backend 目录
    if os.path.isdir(BACKEND_DIR):
        ok(f"backend 目录: {BACKEND_DIR}")
    else:
        fail(f"backend 目录不存在: {BACKEND_DIR}")
        all_ok = False

    # config.py
    if os.path.isfile(CONFIG_FILE):
        ok(f"config.py: {CONFIG_FILE}")
    else:
        fail(f"config.py 不存在: {CONFIG_FILE}")
        all_ok = False
        return all_ok

    # 读取 config 中的关键路径
    sys.path.insert(0, BACKEND_DIR)
    try:
        import config
        
        # 模型目录
        for label, path in [('CKPT_BASE_DIR', config.CKPT_BASE_DIR),
                            ('LORA_BASE_DIR', config.LORA_BASE_DIR)]:
            if os.path.isdir(path):
                ok(f"{label}: {path}")
            else:
                warn(f"{label}: {path}  (目录不存在，首次下载时会自动创建)")

        # ComfyUI
        comfyui_path = config.COMFYUI_PATH
        if os.path.isdir(comfyui_path):
            ok(f"COMFYUI_PATH: {comfyui_path}")
        else:
            warn(f"COMFYUI_PATH: {comfyui_path}  (目录不存在)")

        # workflow 目录
        wf_dir = config.WORKFLOW_DIR
        if os.path.isdir(wf_dir):
            wf_count = len([f for f in os.listdir(wf_dir) if f.endswith('.json')])
            ok(f"WORKFLOW_DIR: {wf_dir}  ({wf_count} 个工作流)")
        else:
            warn(f"WORKFLOW_DIR: {wf_dir}  (目录不存在，将自动创建)")

        # Civitai API Token
        token = config.CIVITAI_API_TOKEN
        if token:
            ok(f"CIVITAI_API_TOKEN: {'*' * 8}...{token[-4:]}")
        else:
            warn("CIVITAI_API_TOKEN 未设置（部分模型下载需要）")

        # 端口
        ok(f"SERVER_PORT: {config.SERVER_PORT}")
        ok(f"COMFYUI_URL: {config.COMFYUI_URL}")

    except Exception as e:
        fail(f"读取 config.py 失败: {e}")
        all_ok = False

    return all_ok


def check_comfyui_watchdog():
    """检测 ComfyUI watchdog 配置"""
    heading("8. ComfyUI Watchdog")
    watchdog_path = os.path.join(PROJECT_ROOT, 'start_comfyui_watchdog.cmd')
    if not os.path.isfile(watchdog_path):
        warn("start_comfyui_watchdog.cmd 不存在")
        return

    ok(f"watchdog 脚本: {watchdog_path}")

    with open(watchdog_path, 'r') as f:
        content = f.read()

    # 提取 COMFYUI_BAT 路径
    match = re.search(r'set\s+COMFYUI_BAT=(.+)', content)
    if match:
        bat_path = match.group(1).strip()
        if os.path.isfile(bat_path):
            ok(f"COMFYUI_BAT: {bat_path}")
        else:
            warn(f"COMFYUI_BAT: {bat_path}  (文件不存在)")

    if '--disable-auto-launch' in content:
        ok("已配置 --disable-auto-launch")
    else:
        info("未配置 --disable-auto-launch（重启时会打开浏览器）")


# ============ 主流程 ============

def main():
    # 启用 Windows ANSI 颜色
    if os.name == 'nt':
        os.system('')

    parser = argparse.ArgumentParser(description='Civitai Downloader 环境初始化')
    parser.add_argument('--check', action='store_true', help='仅检测，不自动修复')
    args = parser.parse_args()

    fix = not args.check

    print(f"\n{Colors.BOLD}{'═'*50}")
    print(f"  Civitai Downloader - 环境初始化{'（仅检测模式）' if not fix else ''}")
    print(f"{'═'*50}{Colors.RESET}")
    print(f"  项目路径: {PROJECT_ROOT}")
    print(f"  系统: {platform.system()} {platform.release()} ({platform.machine()})")

    results = {}

    # 1. Python
    results['python'] = check_python()
    if not results['python']:
        print(f"\n{Colors.RED}{Colors.BOLD}  ❌ Python 版本不满足要求，无法继续{Colors.RESET}\n")
        sys.exit(1)

    # 2. GPU
    results['gpu'] = check_gpu()

    # 3. CUDA
    cuda_ver = check_cuda()
    results['cuda'] = cuda_ver is not None

    # 4. venv
    venv_python = check_venv(fix=fix)
    results['venv'] = venv_python is not None

    # 5. 依赖
    if venv_python:
        results['deps'] = check_dependencies(venv_python, fix=fix)
    else:
        results['deps'] = False

    # 6. PyTorch
    if venv_python:
        results['pytorch'] = check_pytorch(venv_python, cuda_ver)
    else:
        results['pytorch'] = False

    # 7. 项目路径
    results['paths'] = check_project_paths()

    # 8. ComfyUI watchdog
    check_comfyui_watchdog()

    # ============ 汇总 ============
    heading("汇总")
    items = [
        ('Python >= 3.10', 'python'),
        ('NVIDIA GPU', 'gpu'),
        ('CUDA', 'cuda'),
        ('venv', 'venv'),
        ('依赖包', 'deps'),
        ('PyTorch + CUDA', 'pytorch'),
        ('项目路径', 'paths'),
    ]
    all_pass = True
    critical_fail = False
    for label, key in items:
        passed = results.get(key, False)
        if passed:
            ok(label)
        else:
            fail(label)
            all_pass = False
            if key in ('python', 'gpu', 'cuda'):
                critical_fail = True

    print()
    if all_pass:
        print(f"  {Colors.GREEN}{Colors.BOLD}🎉 所有检测通过！可以启动项目了{Colors.RESET}")
        print(f"  {Colors.DIM}启动后端:  start_backend.cmd{Colors.RESET}")
        print(f"  {Colors.DIM}启动ComfyUI:  start_comfyui_watchdog.cmd{Colors.RESET}")
    elif critical_fail:
        print(f"  {Colors.RED}{Colors.BOLD}❌ 关键环境缺失，请先安装缺少的组件{Colors.RESET}")
    else:
        print(f"  {Colors.YELLOW}{Colors.BOLD}⚠ 部分检测未通过，但不影响基本功能{Colors.RESET}")
        if not fix:
            print(f"  {Colors.DIM}运行 python init.py（不带 --check）可自动修复部分问题{Colors.RESET}")

    print()
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
