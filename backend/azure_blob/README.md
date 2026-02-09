
# Azure Blob Storage 便捷工具

一个随复制粘贴随用的 Azure Blob 存储读写工具，专为快速实现小型项目设计。

## 快速使用

### 1. 安装依赖

```bash
pip install azure-storage-blob requests
```

### 2. 配置凭据

编辑 `azure_blob/credentials.py`：

```python
CONNECTION_STRING = "DefaultEndpointsProtocol=https;AccountName=xxx;AccountKey=xxx;EndpointSuffix=core.windows.net"
```

### 3. 使用示例

```python
from azure_blob import BlobStorage

# 初始化（project 即 container 名称）
storage = BlobStorage(project="civitai-downloader")

# 写入 JSON
storage.put_json("data", "config.json", {"key": "value"})

# 读取 JSON
config = storage.get_json("data", "config.json")

# 列出文件
files = storage.list_blobs("data")
```

## 路径结构

```
{container}/{subfolder}/{filename}
```

- `container`: 初始化时指定的项目名
- `subfolder`: 子文件夹（可选）
- `filename`: 文件名

## API 概览

### 读取

| 方法 | 功能 |
|------|------|
| `get_bytes(subfolder, filename)` | 获取字节数据 |
| `get_text(subfolder, filename)` | 获取文本 |
| `get_json(subfolder, filename)` | 获取 JSON |
| `get_image_base64(subfolder, filename)` | 获取图片 base64 |
| `list_blobs(subfolder)` | 列出文件 |
| `exists(subfolder, filename)` | 检查存在 |

### 写入

| 方法 | 功能 |
|------|------|
| `put_bytes(subfolder, filename, data)` | 写入字节 |
| `put_text(subfolder, filename, text)` | 写入文本 |
| `put_json(subfolder, filename, obj)` | 写入 JSON |
| `upload_from_url(url, subfolder, filename)` | 从 URL 上传 |
| `delete(subfolder, filename)` | 删除文件 |

## 依赖

```
azure-storage-blob>=12.0.0
requests>=2.25.0
```
