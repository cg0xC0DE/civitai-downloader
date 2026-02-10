# Civitai Downloader

Civitai 模型下载 + ComfyUI 绘图 + AI 美学分析，一站式 AI 绘画工作流工具。

## 功能概览

| 功能 | 说明 |
|------|------|
| 模型下载 | 从 Civitai 下载 Checkpoint / LoRA / Embedding 模型，自动重命名并建立索引 |
| 参数解析 | 自动解析 Civitai 图片的生成参数（模型、LoRA、提示词、尺寸等） |
| ComfyUI 绘图 | 集成 ComfyUI，支持自动/手动两种绘图模式，自动匹配 LoRA |
| 画廊 | 浏览生成的图片，支持 Azure Blob 云端存储 |
| 收藏 | 收藏 Civitai 图片，一键跳转复刻，状态追踪（待处理 → 处理中 → 已完成） |
| 美学分析 | GPT-4o 驱动的图片美学分析，生成可复用的创作蓝图 |
| Chrome 扩展 | 在 Civitai 网站上一键收藏图片到本工具 |

## 目录结构

```
civitai-downloader/
├── backend/
│   ├── server.py              # HTTP API 服务器（端口 53133）
│   ├── config.py              # 路径、端口、API 配置
│   ├── civitaidl.py           # Civitai 下载核心模块
│   ├── comfyui.py             # ComfyUI API 封装
│   ├── workflow_processor.py  # 工作流动态修改
│   ├── scan_models.py         # 本地模型扫描
│   ├── cache_manager.py       # 模型缓存管理
│   ├── favorite_images.py     # 收藏队列管理
│   ├── azure_blob/            # Azure Blob 存储集成
│   │   ├── blob_storage.py
│   │   ├── credentials.py         # ⚠️ 不提交（gitignore）
│   │   └── example_credentials.py # 凭证模板
│   ├── llm/                   # LLM / 美学分析模块
│   │   ├── aesthetic.py           # 美学分析核心逻辑
│   │   ├── llm_client.py         # OpenAI API 封装
│   │   ├── credential.py         # ⚠️ 不提交（gitignore）
│   │   └── example_credential.py # 凭证模板
│   ├── util/
│   │   ├── model_renamer.py      # 模型文件重命名（Civitai API 标准化）
│   │   ├── model_index.py        # 模型索引管理
│   │   └── chrome-extension/     # Civitai 一键收藏 Chrome 扩展
│   ├── workflows/             # ComfyUI 工作流 JSON 文件
│   ├── cache/                 # 运行时缓存（aesthetic、收藏等）
│   ├── output/                # ComfyUI 生成图片输出
│   └── requirements.txt
├── frontend/
│   ├── index.html             # 主界面（4 个 Tab）
│   ├── css/style.css          # 样式
│   └── js/
│       ├── app.js             # 主逻辑（下载、绘图、收藏）
│       └── gallery.js         # 画廊 & 美学分析
├── init.py                    # 环境初始化 & 自检脚本
├── start_backend.cmd          # 启动后端
└── start_comfyui_watchdog.cmd # 启动 ComfyUI（含自动重启）
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- ComfyUI（端口 8188）
- （可选）OpenAI API Key — 用于美学分析
- （可选）Azure Storage — 用于画廊云端存储

### 2. 安装依赖

```bash
python init.py          # 自动检测环境并安装依赖
# 或手动：
cd backend
pip install -r requirements.txt
```

### 3. 配置凭证

复制凭证模板并填入你的密钥：

```bash
# Azure Blob 存储（可选）
cp backend/azure_blob/example_credentials.py backend/azure_blob/credentials.py

# OpenAI API（美学分析需要）
cp backend/llm/example_credential.py backend/llm/credential.py
```

编辑对应的 `credentials.py` / `credential.py`，填入实际的密钥值。

### 4. 修改配置

编辑 `backend/config.py`：

```python
# 模型目录（改为你的实际路径）
CKPT_BASE_DIR = 'D:/ckpt'
LORA_BASE_DIR = 'D:/lora'
EMBEDDING_BASE_DIR = 'D:/embeddings'

# ComfyUI
COMFYUI_URL = '127.0.0.1:8188'
COMFYUI_PATH = 'C:/ComfyUI_windows_portable/ComfyUI'

# 服务端口
SERVER_PORT = 53133

# Civitai API Token（部分模型需要登录才能下载）
# 获取方式：https://civitai.com/user/account -> API Keys
CIVITAI_API_TOKEN = ''
```

### 5. 启动服务

```bash
# 启动后端 API
start_backend.cmd

# 启动 ComfyUI（独立进程，含 watchdog 自动重启）
start_comfyui_watchdog.cmd
```

访问 `http://localhost:53133`

## 前端功能

### ⬇️ 下载 Tab

1. 粘贴 Civitai 模型 URL（如 `https://civitai.com/models/257749`）
2. 选择模型类型（Checkpoint / LoRA / Embedding）和子类型
3. 点击「开始下载」，自动重命名并建立索引

### 🎨 绘图 Tab

**自动模式（推荐）：**
1. 粘贴 Civitai 图片 URL
2. 点击「解析参数」— 自动提取模型、LoRA、提示词、尺寸
3. 选择工作流，点击「开始生成」

**手动模式：**
1. 选择工作流、Checkpoint、LoRA
2. 输入提示词和尺寸
3. 点击「开始生成」

### 🖼️ 画廊 Tab

- 浏览 ComfyUI 生成的图片（本地 / Azure Blob）
- 点击「🔍 美学分析」对图片进行 GPT-4o 美学解读，生成创作蓝图
- 蓝图结果缓存在 `backend/cache/aesthetic/`

### ⭐ 收藏 Tab

- 浏览通过 Chrome 扩展或手动添加的 Civitai 图片
- 每张图片显示状态标签：`待处理` → `处理中` → `已完成`
- 点击「分析与复刻」→ 自动跳转绘图 Tab 并填入 URL，状态变为「处理中」
- 在画廊完成美学分析后，状态自动变为「已完成」

### Chrome 扩展

位于 `backend/util/chrome-extension/`，可在 Civitai 网站上一键收藏图片。

安装方式：Chrome → `chrome://extensions` → 开发者模式 → 加载已解压的扩展程序 → 选择该目录。

## API 接口

基础地址：`http://localhost:53133/api/`

### 核心接口

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/parse-url` | 解析 Civitai URL |
| POST | `/api/download` | 下载模型 |
| GET | `/api/download-status` | 下载进度 |
| GET | `/api/workflows` | 工作流列表 |
| GET | `/api/models` | 本地模型列表 |
| POST | `/api/comfyui/restart` | 重启 ComfyUI |

### 绘图接口

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/parse-civitai-image` | 解析 Civitai 图片参数 |
| POST | `/api/workflow/run` | 提交 ComfyUI 工作流 |

### 画廊接口

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/gallery/list` | 获取画廊图片列表 |
| POST | `/api/azure/delete` | 删除 Azure Blob 图片 |

### 收藏接口

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/favorite/add` | 添加收藏 |
| GET | `/api/favorite/list` | 获取收藏列表 |
| POST | `/api/favorite/update-status` | 更新收藏状态 |
| POST | `/api/favorite/delete` | 删除收藏 |
| GET | `/api/image/thumb` | 获取 Civitai 图片缩略图 |

### 美学分析接口

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/aesthetic/analyze` | 提交美学分析（异步） |
| GET | `/api/aesthetic/status` | 查询分析任务状态 |
| GET | `/api/aesthetic/result` | 查询已缓存的分析结果 |

## 常见问题

**Q: 下载失败？**
- 检查 `config.py` 中 `CIVITAI_API_TOKEN` 是否已配置
- 确认模型类型选择正确

**Q: ComfyUI 无法连接？**
- 确认 ComfyUI 已启动（端口 8188）
- 检查 `config.py` 中 `COMFYUI_URL` 配置

**Q: 找不到模型？**
- 检查 `CKPT_BASE_DIR` / `LORA_BASE_DIR` 路径是否正确
- 重启 ComfyUI 刷新模型列表

**Q: 美学分析失败？**
- 确认 `backend/llm/credential.py` 中 `OPENAI_API_KEY` 已填入
- 检查网络是否能访问 OpenAI API

**Q: 画廊无图片？**
- 本地模式：检查 `backend/output/` 目录
- Azure 模式：确认 `backend/azure_blob/credentials.py` 中连接字符串正确

## 相关项目

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- [Civitai](https://civitai.com)

---

维护者：cg0xC0DE
