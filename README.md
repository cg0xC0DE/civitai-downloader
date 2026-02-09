# Civitai Downloader

Civitai 模型下载管理工具 + ComfyUI 绘图集成

## 功能概览

| 功能 | 说明 |
|------|------|
| 模型下载 | 从 Civitai 下载 Checkpoint / LoRA 模型 |
| 参数解析 | 自动解析 Civitai 图片的生成参数 |
| ComfyUI 绘图 | 集成 ComfyUI，支持自动/手动两种绘图模式 |

## 目录结构

```
civitai-downloader/
├── backend/
│   ├── server.py          # HTTP API 服务器（端口 53133）
│   ├── civitaidl.py       # 核心下载模块
│   ├── config.py          # 配置文件
│   ├── cache_manager.py   # 模型缓存管理
│   ├── scan_models.py     # 模型扫描
│   ├── workflow_processor.py  # 工作流处理
│   └── models/            # 存放下载的模型文件
├── frontend/
│   ├── index.html         # 主界面
│   ├── css/style.css      # 样式
│   └── js/app.js          # 前端逻辑
├── init.py                # 环境初始化 & 自检
├── wrapper.py             # 命令行包装器
└── start_backend.cmd      # 启动后端
```

## 快速开始

### 1. 环境初始化（首次运行）

```bash
cd backend
pip install -r requirements.txt
python init.py          # 检测环境并自动修复
```

### 2. 启动服务

```bash
# 启动后端 API
start_backend.cmd

# 启动 ComfyUI（独立进程）
start_comfyui_watchdog.cmd
```

服务运行地址：`http://localhost:53133`

## 前端使用

访问 `http://localhost:53133`

### Tab 1：下载模型

1. 粘贴 Civitai 模型 URL（如 `https://civitai.com/models/257749`）
2. 选择模型类型（Checkpoint / LoRA）
3. 点击「开始下载」

### Tab 2：ComfyUI 绘图

**自动模式：**
1. 粘贴 Civitai 图片 URL
2. 选择工作流
3. 点击「解析参数」→ 「开始生成」

**手动模式：**
1. 选择工作流
2. 选择 Checkpoint / LoRA 模型
3. 输入提示词
4. 选择尺寸，点击「开始生成」

## API 接口

### 基础地址

```
http://localhost:53133/api/
```

### 接口列表

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/parse-url` | 解析 Civitai URL |
| POST | `/api/download` | 下载模型 |
| GET | `/api/download-status` | 下载状态 |
| POST | `/api/draw` | ComfyUI 生成 |
| GET | `/api/workflows` | 获取工作流列表 |
| GET | `/api/models` | 获取本地模型列表 |
| POST | `/api/comfyui/restart` | 重启 ComfyUI |

### 请求示例

**下载模型：**
```json
POST /api/download
{
    "url": "https://civitai.com/models/257749",
    "type": "ckpt.xl"
}
```

**绘图请求：**
```json
POST /api/draw
{
    "workflow": "nolora",
    "prompt": "1girl, masterpiece, best quality",
    "width": 1024,
    "height": 1024,
    "checkpoint": "hassakuXL_v13.safetensors"
}
```

## 配置项（backend/config.py）

```python
SERVER_PORT = 53133          # 服务端口
COMFYUI_URL = "http://localhost:8188"  # ComfyUI 地址
CIVITAI_API_TOKEN = "..."    # Civitai API Token（可选，部分模型需要）
CKPT_BASE_DIR = "..."        # Checkpoint 模型目录
LORA_BASE_DIR = "..."        # LoRA 模型目录
WORKFLOW_DIR = "backend/workflows"  # 工作流目录
```

## 常见问题

**Q: 下载失败？**
- 检查 CIVITAI_API_TOKEN 是否配置
- 确认模型类型选择正确

**Q: ComfyUI 无法连接？**
- 确认 ComfyUI 已启动（端口 8188）
- 检查 COMFYUI_URL 配置

**Q: 找不到模型？**
- 检查 CKPT_BASE_DIR / LORA_BASE_DIR 路径
- 点击 ComfyUI Tab 的「重启」刷新模型列表

## 相关项目

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- [Civitai](https://civitai.com)

---

维护者：cg0xC0DE
