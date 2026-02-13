# -*- coding: utf-8 -*-
"""
美学分析管线 — 从 AI 绘画作品中提取结构化美学蓝图

流程：
  1. 读取图片 PNG 元数据（ComfyUI workflow）→ 提取生成参数
  2. 从 model_index 收集所有模型的触发词
  3. 组装 LLM prompt（图片 + 参数 + 触发词 + 用户主观描述）
  4. 调用 GPT-4o Vision → 返回结构化 JSON
  5. 保存到 aesthetic_blueprints.json

用法：
  from llm.aesthetic import analyze
  result = analyze(image_source="https://xxx/image.png", user_why_good="色彩对比很强")
"""

import os
import sys
import json
import struct
import zlib
import urllib.request

_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from llm.llm_client import chat_with_image_json
_LOCAL_PATH = os.path.join(_BACKEND_DIR, 'cache', 'aesthetic_blueprints.json')
_BLOB_CONTAINER = 'civitaidl'
_BLOB_SUBFOLDER = 'data'
_BLOB_FILENAME = 'aesthetic_blueprints.json'


def _azure_available() -> bool:
    """检查 Azure Blob 是否可用"""
    try:
        from azure_blob.credentials import CONNECTION_STRING
        return bool(CONNECTION_STRING)
    except Exception:
        return False


def _get_blob():
    from azure_blob import BlobStorage
    return BlobStorage(container=_BLOB_CONTAINER)


# ============================================================
# 第 1 步：读取 PNG 元数据
# ============================================================

def _read_png_text_chunks(data: bytes) -> dict:
    """
    从 PNG 二进制数据中提取所有 tEXt / iTXt 文本块。
    ComfyUI 在 tEXt 块中存储 "prompt"（API 格式）和 "workflow"（UI 格式）。
    返回 { key: value } dict。
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return {}

    result = {}
    offset = 8

    while offset < len(data):
        if offset + 8 > len(data):
            break
        length = struct.unpack('>I', data[offset:offset+4])[0]
        chunk_type = data[offset+4:offset+8]

        chunk_data = data[offset+8:offset+8+length]

        if chunk_type == b'tEXt':
            # tEXt: keyword\0text
            sep = chunk_data.find(b'\x00')
            if sep >= 0:
                key = chunk_data[:sep].decode('latin-1')
                value = chunk_data[sep+1:].decode('utf-8', errors='replace')
                result[key] = value

        elif chunk_type == b'iTXt':
            # iTXt: keyword\0compression_flag\0compression_method\0language\0translated\0text
            sep = chunk_data.find(b'\x00')
            if sep >= 0:
                key = chunk_data[:sep].decode('latin-1')
                rest = chunk_data[sep+1:]
                if len(rest) >= 2:
                    comp_flag = rest[0]
                    # Skip compression_method, language, translated_keyword
                    rest = rest[2:]  # skip comp_flag + comp_method
                    # language tag
                    sep2 = rest.find(b'\x00')
                    if sep2 >= 0:
                        rest = rest[sep2+1:]
                        # translated keyword
                        sep3 = rest.find(b'\x00')
                        if sep3 >= 0:
                            text_data = rest[sep3+1:]
                            if comp_flag:
                                try:
                                    text_data = zlib.decompress(text_data)
                                except Exception:
                                    pass
                            result[key] = text_data.decode('utf-8', errors='replace')

        elif chunk_type == b'IEND':
            break

        # 跳到下一个 chunk（length + 4字节CRC）
        offset += 8 + length + 4

    return result


def read_image_metadata(image_source) -> dict:
    """
    从图片中读取 ComfyUI 元数据。
    image_source: 文件路径 / URL / bytes

    返回 { 'prompt': dict, 'workflow': dict } 或空 dict。
    """
    if isinstance(image_source, (bytes, bytearray)):
        data = image_source
    elif isinstance(image_source, str):
        if image_source.startswith(('http://', 'https://')):
            req = urllib.request.Request(image_source, headers={
                'User-Agent': 'Mozilla/5.0'
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        elif os.path.isfile(image_source):
            with open(image_source, 'rb') as f:
                data = f.read()
        else:
            return {}
    else:
        return {}

    chunks = _read_png_text_chunks(data)
    result = {}

    for key in ('prompt', 'workflow'):
        raw = chunks.get(key, '')
        if raw:
            try:
                result[key] = json.loads(raw)
            except json.JSONDecodeError:
                result[key] = raw

    return result


# ============================================================
# 第 2 步：从 ComfyUI prompt JSON 提取生成参数
# ============================================================

def parse_gen_params(prompt_data: dict) -> dict:
    """
    从 ComfyUI API 格式的 prompt JSON 中提取关键生成参数。
    返回：
    {
        'checkpoint': str,
        'loras': [ { 'name': str, 'weight': float }, ... ],
        'positive_prompt': str,
        'negative_prompt': str,
        'sampler': str,
        'steps': int,
        'cfg': float,
        'width': int,
        'height': int,
        'seed': int,
    }
    """
    if not isinstance(prompt_data, dict):
        return {}

    result = {
        'checkpoint': '',
        'loras': [],
        'positive_prompt': '',
        'negative_prompt': '',
        'sampler': '',
        'steps': 0,
        'cfg': 0.0,
        'width': 0,
        'height': 0,
        'seed': 0,
    }

    # 辅助：按 class_type 找节点
    def find_nodes(class_type):
        return [(nid, node) for nid, node in prompt_data.items()
                if isinstance(node, dict) and node.get('class_type') == class_type]

    # Checkpoint
    for nid, node in find_nodes('CheckpointLoaderSimple'):
        result['checkpoint'] = node.get('inputs', {}).get('ckpt_name', '')
        break

    # KSampler
    positive_nid = negative_nid = None
    for nid, node in find_nodes('KSampler'):
        inputs = node.get('inputs', {})
        result['sampler'] = inputs.get('sampler_name', '')
        result['steps'] = inputs.get('steps', 0)
        result['cfg'] = inputs.get('cfg', 0.0)
        result['seed'] = inputs.get('seed', 0)
        # 追溯 positive / negative CLIPTextEncode 节点
        pos_ref = inputs.get('positive')
        neg_ref = inputs.get('negative')
        if isinstance(pos_ref, list):
            positive_nid = str(pos_ref[0])
        if isinstance(neg_ref, list):
            negative_nid = str(neg_ref[0])
        break

    # Positive / Negative prompt（从 CLIPTextEncode 获取）
    if positive_nid and positive_nid in prompt_data:
        result['positive_prompt'] = prompt_data[positive_nid].get('inputs', {}).get('text', '')
    if negative_nid and negative_nid in prompt_data:
        result['negative_prompt'] = prompt_data[negative_nid].get('inputs', {}).get('text', '')

    # EmptyLatentImage 尺寸
    for nid, node in find_nodes('EmptyLatentImage'):
        inputs = node.get('inputs', {})
        result['width'] = inputs.get('width', 0)
        result['height'] = inputs.get('height', 0)
        break

    # LoRA — 支持多种节点类型
    lora_types = [
        'LoraLoader',
        'Lora Loader Stack (rgthree)',
        'Power Lora Loader (rgthree)',
    ]
    for lora_type in lora_types:
        for nid, node in find_nodes(lora_type):
            inputs = node.get('inputs', {})

            if lora_type == 'LoraLoader':
                name = inputs.get('lora_name', '')
                weight = inputs.get('strength_model', 1.0)
                if name:
                    result['loras'].append({'name': name, 'weight': weight})

            elif 'Stack' in lora_type or 'Power' in lora_type:
                # rgthree Stack: lora_01..lora_N, strength_01..strength_N
                for i in range(1, 20):
                    key_name = f'lora_{i:02d}'
                    key_str = f'strength_{i:02d}'
                    name = inputs.get(key_name, '')
                    if not name or name == 'None':
                        continue
                    weight = inputs.get(key_str, 1.0)
                    result['loras'].append({'name': name, 'weight': weight})

    return result


# ============================================================
# 第 3 步：收集触发词
# ============================================================

def collect_trigger_words(gen_params: dict) -> dict:
    """
    从 model_index 中查找 checkpoint 和 LoRA 的触发词。
    返回 { 'model_name': [trigger_words] } 形式的 dict。

    注意：仅按文件名匹配（无 versionId），因为 PNG metadata 里没有 versionId。
    """
    from util import model_index
    model_index._ensure_loaded()

    tw_map = {}

    # 遍历索引找匹配
    all_entries = model_index._by_version

    # 收集所有要查的文件名
    names_to_find = {}
    ckpt = gen_params.get('checkpoint', '')
    if ckpt:
        names_to_find[os.path.basename(ckpt).lower()] = ckpt

    for lora in gen_params.get('loras', []):
        name = lora.get('name', '')
        if name:
            names_to_find[os.path.basename(name).lower()] = name

    # 从索引中按文件名查找
    for vid, entry in all_entries.items():
        fn = entry.get('filename', '').lower()
        if not fn:
            continue
        for search_fn, original_name in names_to_find.items():
            if fn == search_fn or fn.endswith(search_fn):
                tw = entry.get('trigger_words', [])
                if tw:
                    tw_map[original_name] = tw

    return tw_map


# ============================================================
# 第 4 步：组装 LLM Prompt
# ============================================================

_SYSTEM_PROMPT = """你是一位 AI 绘画美学分析师。你的任务是分析 AI 生成的图片，结合其生成参数和用户的主观感受，输出结构化的美学蓝图。

## 分析要求

1. **plain_text（纯净提示词）**：从原始 positive_prompt 中剥离所有模型触发词（我会提供已知触发词列表），只保留纯粹的创意描述。触发词可能跳跃穿插、不连续，请仔细识别。如果有疑似触发词但不在已知列表中（编码感强、非自然语言的 token），也请尝试剥离并在 notes 中说明。

2. **why_good（为什么好看）**：结合你对图片的视觉分析（构图、光影、色彩、氛围、叙事性）和用户提供的主观感受，融合形成一段中文美学分析。重点关注：
   - 色彩对比与和谐
   - 光影的情绪表达
   - 构图与视觉动线
   - 角色姿态与心理暗示
   - 材质感与渲染风格
   - 整体氛围与叙事张力

3. **output_prompt_plain（优化后的纯净提示词）**：基于 plain_text，适当优化和补充描述，使其更加精准和完整，但不加入任何模型特定的触发词。

4. **output_prompt_with_model（完整重建提示词）**：在 output_prompt_plain 基础上，附加模型、LoRA、Sampler 等完整参数信息，格式参考示例。

5. **其他字段**：work_title（4-8个中文字的诗意标题）、base_combo（模型组合简称）、image_type（anime/2.5d/realistic/concept 等）、tags（5-8个英文标签）、vibe（一句中文氛围描述）。

## 输出格式
请严格输出 JSON 对象，包含以下字段：
work_title, plain_text, why_good, output_prompt_plain, output_prompt_with_model, base_combo, image_type, tags, vibe

不要输出 JSON 以外的任何内容。"""


def _build_user_message(gen_params: dict, trigger_words: dict,
                        user_why_good: str) -> str:
    """组装发给 LLM 的用户消息文本。"""
    parts = []

    # 生成参数
    parts.append("## 生成参数")
    parts.append(f"- Checkpoint: {gen_params.get('checkpoint', '未知')}")

    loras = gen_params.get('loras', [])
    if loras:
        lora_strs = [f"{l['name']} ({l.get('weight', 1.0)})" for l in loras]
        parts.append(f"- LoRA: {'; '.join(lora_strs)}")

    parts.append(f"- Sampler: {gen_params.get('sampler', '未知')}")
    parts.append(f"- Steps: {gen_params.get('steps', '?')}, CFG: {gen_params.get('cfg', '?')}")
    parts.append(f"- Size: {gen_params.get('width', '?')}×{gen_params.get('height', '?')}")
    parts.append(f"- Seed: {gen_params.get('seed', '?')}")

    parts.append(f"\n## 原始 Positive Prompt\n{gen_params.get('positive_prompt', '（无）')}")
    parts.append(f"\n## 原始 Negative Prompt\n{gen_params.get('negative_prompt', '（无）')}")

    # 触发词
    parts.append("\n## 已知模型触发词")
    if trigger_words:
        for model_name, words in trigger_words.items():
            parts.append(f"- {os.path.basename(model_name)}: {', '.join(words)}")
    else:
        parts.append("（索引中未找到触发词，请根据经验识别疑似触发词）")

    # 用户主观描述
    parts.append(f"\n## 用户主观感受\n{user_why_good or '（用户未提供主观描述，请仅基于视觉分析）'}")

    return '\n'.join(parts)


# ============================================================
# 第 5 步：主分析函数
# ============================================================

def analyze(image_source, user_why_good: str = '',
            gen_params_override: dict = None) -> dict:
    """
    美学分析主入口。

    Args:
        image_source: 图片来源 — URL / 文件路径 / bytes
        user_why_good: 用户的主观感受描述
        gen_params_override: 可选，预解析的生成参数（跳过 PNG metadata 读取）

    Returns:
        完整的 aesthetic_blueprint dict
    """
    # 1. 提取生成参数
    if gen_params_override:
        gen_params = gen_params_override
    else:
        metadata = read_image_metadata(image_source)
        prompt_data = metadata.get('prompt', {})
        gen_params = parse_gen_params(prompt_data) if prompt_data else {}

    # 2. 收集触发词
    trigger_words = collect_trigger_words(gen_params) if gen_params else {}

    # 3. 组装 LLM 消息
    user_text = _build_user_message(gen_params, trigger_words, user_why_good)

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    # 4. 调用 LLM（带图片 + 强制 JSON）
    print("[Aesthetic] 调用 GPT-4o 分析中...")
    raw_reply = chat_with_image_json(
        messages=messages,
        image=image_source,
        user_text=user_text,
        max_tokens=1500,
        temperature=0.3,
    )

    # 5. 解析 JSON
    try:
        blueprint = json.loads(raw_reply)
    except json.JSONDecodeError:
        # 尝试提取 JSON 块
        import re
        m = re.search(r'\{[\s\S]*\}', raw_reply)
        if m:
            blueprint = json.loads(m.group())
        else:
            raise ValueError(f"LLM 返回内容无法解析为 JSON: {raw_reply[:200]}")

    # 6. 补全非 LLM 负责的字段
    blueprint['image_source'] = image_source if isinstance(image_source, str) else ''
    blueprint['base_model'] = gen_params.get('checkpoint', '')

    lora_entries = []
    for l in gen_params.get('loras', []):
        name = l.get('name', '')
        weight = l.get('weight', 1.0)
        if weight != 1.0:
            lora_entries.append(f"{name} ({weight})")
        else:
            lora_entries.append(name)
    blueprint['lora_list'] = lora_entries

    print(f"[Aesthetic] 分析完成: {blueprint.get('work_title', '?')}")
    return blueprint


# ============================================================
# 第 6 步：保存蓝图
# ============================================================

def save_blueprint(blueprint: dict) -> str:
    """
    保存蓝图（本地 + Azure 双写）。
    返回本地路径。
    """
    existing = load_blueprints()
    # 同一作品重复分析时，用最新结果覆盖旧的
    img_src = blueprint.get('image_source', '')
    if img_src:
        existing = [b for b in existing if b.get('image_source') != img_src]
    existing.append(blueprint)
    text = json.dumps(existing, ensure_ascii=False, indent=2)

    # 1. 始终写本地
    os.makedirs(os.path.dirname(_LOCAL_PATH), exist_ok=True)
    with open(_LOCAL_PATH, 'w', encoding='utf-8') as f:
        f.write(text)

    # 2. 有 Azure 时同步
    if _azure_available():
        try:
            blob = _get_blob()
            blob.put_json(_BLOB_SUBFOLDER, _BLOB_FILENAME, existing, indent=2)
        except Exception as e:
            print(f"[Aesthetic] Azure 写入失败（本地已保存）: {e}")

    print(f"[Aesthetic] 蓝图已保存，当前共 {len(existing)} 条")
    return _LOCAL_PATH


def load_blueprints() -> list:
    """加载所有蓝图（本地优先，Azure 回退迁移）。"""
    # 1. 读本地
    if os.path.exists(_LOCAL_PATH):
        try:
            with open(_LOCAL_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

    # 2. 本地为空，尝试从 Azure 迁移
    if _azure_available():
        try:
            blob = _get_blob()
            data = blob.get_json(_BLOB_SUBFOLDER, _BLOB_FILENAME)
            if isinstance(data, list) and data:
                os.makedirs(os.path.dirname(_LOCAL_PATH), exist_ok=True)
                with open(_LOCAL_PATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[Aesthetic] 从 Azure 迁移 {len(data)} 条蓝图到本地")
                return data
        except Exception as e:
            print(f"[Aesthetic] Azure 读取失败: {e}")

    return []
